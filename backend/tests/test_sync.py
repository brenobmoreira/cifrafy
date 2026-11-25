"""Sync, cache, catalog and Spotify tests. All HTTP is mocked: nothing reaches the network."""

import asyncio
import sqlite3
import zlib

import httpx
import pytest
from fastapi.testclient import TestClient

import src.api as api
from src.core import chord_scraper as scraper
from src.core import spotify_client as spotify
from src.core.chord_scraper import (
    analyze_tracks,
    clean_track_name,
    cifraclub_limiter,
    fetch_chord_sheet_async,
    parse_cifraclub_html,
    split_artists,
)
from src.db import db

KNOWN = ["C", "G", "D", "Am", "Em"]


def page(title="Musica", artist="Artista", chords=("C", "G"), lyric="Letra completa da musica"):
    marks = "  ".join(f"<b>{c}</b>" for c in chords)
    return (
        f'<h1 class="t1">{title}</h1><h2 class="t3">{artist}</h2>'
        f'<span id="cifra_tom"><a href="#">C</a></span><pre>{marks}\n{lyric}\n</pre>'
    )


class Site:
    """Mock of Cifra Club: maps URL paths to responses and records each request."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests = []

    def __call__(self, request):
        self.requests.append(str(request.url))
        if request.url.host.startswith("solr"):
            return self.routes.get("solr", httpx.Response(200, json={"response": {"docs": []}}))
        return self.routes.get(request.url.path, httpx.Response(404, text="nao existe"))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "db_path", tmp_path / "test.db")
    db._init_schema()
    monkeypatch.setattr(cifraclub_limiter, "min_interval", 0.0)
    cifraclub_limiter.reset()
    yield
    cifraclub_limiter.reset()


@pytest.fixture
def site(monkeypatch):
    mock = Site()
    monkeypatch.setattr(
        scraper,
        "_build_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(mock), follow_redirects=True),
    )
    return mock


def sync(tracks, **kwargs):
    options = {"simplified": False, "known_chords": KNOWN, "avoid_barre": True, **kwargs}
    return asyncio.run(analyze_tracks(tracks, **options))


def index_song(slug, name=None, freqs=None, sheet=None, artist_slug="artista"):
    return db.upsert_song_index(
        artist_slug=artist_slug,
        track_slug=slug,
        artist_name="Artista",
        track_name=name or slug,
        cifra_url=f"https://www.cifraclub.com.br/{artist_slug}/{slug}/",
        musical_key="C",
        chord_frequencies=freqs or {"C": 5, "G": 5},
        has_barre_chords=False,
        avg_changes_per_line=2.0,
        has_tabs=False,
        raw_chord_sheet=sheet,
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Best Part of Me", "The Best Part of Me"),
        ("Take Part in Love", "Take Part in Love"),
        ("Live Forever", "Live Forever"),
        ("Song (Live Forever)", "Song (Live Forever)"),
        ("Tempo Perdido - Ao Vivo", "Tempo Perdido"),
        ("Wonderwall - Remastered 2014", "Wonderwall"),
        ("Song 2 - 2012 Remaster", "Song 2"),
        ("Amor (part. Ana Cañas)", "Amor"),
        ("Amor part. Ana", "Amor"),
        ("Amor (feat. Ana)", "Amor"),
        ("Amor [Live at Wembley]", "Amor"),
    ],
)
def test_clean_track_name(title, expected):
    assert clean_track_name(title) == expected


def test_split_artists():
    assert split_artists("Anavitória,\xa0Nando Reis") == ["Anavitória", "Nando Reis"]
    assert split_artists("Earth, Wind & Fire") == ["Earth, Wind & Fire"]
    assert split_artists("Simon & Garfunkel") == ["Simon & Garfunkel"]


def test_density_ignores_intro_and_counts_lyric_lines():
    html = (
        "<pre>[Intro] <b>C</b> <b>Am</b> <b>Bm</b> <b>Em</b>\n\n<b>C</b>   <b>Am</b>\n"
        "Todos os dias\n<b>Bm</b>   <b>Em</b>\nNao tenho\n</pre>"
    )
    assert parse_cifraclub_html(html, "u")["avg_changes_per_line"] == 2.0


@pytest.mark.parametrize(
    "snippet",
    [
        '<span id="cifra_tom">tom: <a>Em</a></span>',
        '<span data-key="Em"></span>',
        "<p>Tom: <a>Em</a></p>",
        '<script>var c = {"tom": "Em"};</script>',
    ],
)
def test_key_extraction_formats(snippet):
    assert parse_cifraclub_html(f"{snippet}<pre><b>C</b>\nLetra</pre>", "u")["key"] == "Em"


def test_key_is_none_when_absent():
    assert parse_cifraclub_html("<p>Atom: C</p><pre><b>C</b>\nLetra</pre>", "u")["key"] is None


def test_cooldown_stops_batch_and_respects_retry_after(site):
    site.routes["/artista/a/"] = httpx.Response(429, headers={"Retry-After": "60"})
    tracks = [{"artist": "Artista", "name": n} for n in "abc"]
    results = sync(tracks)
    assert [r["status"] for r in results] == ["rate_limited"] * 3
    assert len(site.requests) == 1
    assert 0 < cifraclub_limiter.cooldown_remaining() <= 60


def test_403_opens_default_cooldown(site):
    site.routes["/artista/a/"] = httpx.Response(403)
    assert sync([{"artist": "Artista", "name": "a"}])[0]["status"] == "rate_limited"
    assert cifraclub_limiter.cooldown_remaining() > 60


def test_batch_reports_each_status_without_dropping_tracks(site):
    index_song("indexada")
    site.routes["/artista/nova/"] = httpx.Response(200, text=page("Nova"))
    site.routes["/artista/quebrada/"] = httpx.Response(500)
    names = ["indexada", "nova", "inexistente", "quebrada"]
    results = sync([{"artist": "Artista", "name": n} for n in names])
    assert [r["status"] for r in results] == ["cached", "synced", "not_found", "error"]


def test_unexpected_failure_does_not_break_batch(site, monkeypatch):
    site.routes["/artista/boa/"] = httpx.Response(200, text=page("Boa"))
    real = scraper.evaluate_song_difficulty
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("falha")
        return real(**kwargs)

    monkeypatch.setattr(scraper, "evaluate_song_difficulty", flaky)
    results = sync([{"artist": "Artista", "name": "boa"}, {"artist": "Artista", "name": "boa"}])
    assert [r["status"] for r in results] == ["error", "cached"]


def test_redirected_slug_is_found_again_without_request(site):
    site.routes["/legiao-urbana/que-pais-e-este/"] = httpx.Response(
        301, headers={"Location": "https://www.cifraclub.com.br/legiao-urbana/que-pais-e-esse/"}
    )
    site.routes["/legiao-urbana/que-pais-e-esse/"] = httpx.Response(200, text=page("Que País É Este"))
    track = {"artist": "Legião Urbana", "name": "Que País É Este"}
    assert sync([track])[0]["status"] == "synced"
    count = len(site.requests)
    assert sync([track])[0]["status"] == "cached"
    assert len(site.requests) == count


def test_spotify_id_link_finds_song_with_other_title(site):
    track_id = "4uLU6hMCjMI75M1A2tKUQC"
    site.routes["/artista/original/"] = httpx.Response(200, text=page("Original"))
    sync([{"artist": "Artista", "name": "Original", "track_id": track_id}])
    count = len(site.requests)
    result = sync([{"artist": "Artista", "name": "Titulo Renomeado", "track_id": track_id}])[0]
    assert result["status"] == "cached"
    assert len(site.requests) == count


def test_multiple_artists_use_main_artist(site):
    site.routes["/anavitoria/trevo/"] = httpx.Response(200, text=page("Trevo", "Anavitória"))
    result = sync([{"artist": "Anavitória,\xa0Nando Reis", "name": "Trevo"}])[0]
    assert result["status"] == "synced"
    assert "/anavitoria/trevo/" in result["url"]


def test_solr_result_of_other_artist_is_rejected(site):
    docs = [{"d": "outro-artista", "u": "tempo-perdido", "a": "Outro", "m": "Tempo Perdido"}]
    site.routes["solr"] = httpx.Response(200, text='suggest_callback({"response":{"docs":%s}})' % str(docs).replace("'", '"'))
    assert sync([{"artist": "Artista", "name": "Tempo Perdido"}])[0]["status"] == "not_found"
    assert not any("outro-artista" in url for url in site.requests)


def test_solr_validated_result_is_used(site):
    docs = '[{"d": "artista", "u": "pais-filhos", "a": "Artista", "m": "Pais e Filhos"}]'
    site.routes["solr"] = httpx.Response(200, text='suggest_callback({"response":{"docs":%s}})' % docs)
    site.routes["/artista/pais-filhos/"] = httpx.Response(200, text=page("Pais e Filhos"))
    assert sync([{"artist": "Artista", "name": "Pais e Filhos"}])[0]["status"] == "synced"


def test_new_lookups_are_capped(site):
    tracks = [{"artist": "Artista", "name": f"musica {i}"} for i in range(20)]
    for i in range(20):
        site.routes[f"/artista/musica-{i}/"] = httpx.Response(200, text=page(f"musica {i}"))
    results = sync(tracks)
    assert sum(r["status"] == "synced" for r in results) == 15
    assert sum(r["status"] == "rate_limited" for r in results) == 5
    assert len(site.requests) == 15


def test_sync_saves_full_text_and_open_makes_zero_requests(site):
    site.routes["/artista/musica/"] = httpx.Response(200, text=page(lyric="Letra guardada no banco"))
    client = TestClient(api.app)
    response = client.post(
        "/api/playlists/sync-selected",
        json={"tracks": [{"artist": "Artista", "name": "Musica"}]},
    )
    body = response.json()
    assert body["summary"]["synced"] == 1
    assert body["results"][0]["status"] == "synced"
    assert "raw_chord_sheet" not in body["results"][0]

    count = len(site.requests)
    opened = client.get("/api/chords", params={"artist": "Artista", "track_name": "Musica"}).json()
    assert opened["status"] == "cached"
    assert opened["sheet_available"] is True
    assert "Letra guardada no banco" in opened["raw_chord_sheet"]
    assert len(site.requests) == count


def test_sheets_are_stored_with_zlib_9():
    text = "C G Am F letra " * 200
    song_id = index_song("zlib", sheet=text)
    with sqlite3.connect(db.db_path) as conn:
        blob = conn.execute(
            "SELECT compressed_sheet FROM chord_sheets_cache WHERE song_id = ?", (song_id,)
        ).fetchone()[0]
    assert blob == zlib.compress(text.encode(), level=9)
    assert zlib.decompress(blob).decode() == text


def test_more_than_200_sheets_are_never_removed():
    for i in range(205):
        index_song(f"musica-{i}", sheet=f"cifra {i}")
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chord_sheets_cache").fetchone()[0] == 205
    assert db.get_song_by_slugs("artista", "musica-0")["raw_chord_sheet"] == "cifra 0"


def test_legacy_song_downloads_text_once_on_open(site):
    index_song("antiga")
    site.routes["/artista/antiga/"] = httpx.Response(200, text=page("antiga", lyric="Texto recuperado"))
    assert sync([{"artist": "Artista", "name": "antiga"}])[0]["status"] == "cached"
    assert site.requests == []

    client = TestClient(api.app)
    params = {"artist": "Artista", "track_name": "antiga"}
    assert "Texto recuperado" in client.get("/api/chords", params=params).json()["raw_chord_sheet"]
    assert len(site.requests) == 1
    assert client.get("/api/chords", params=params).json()["status"] == "cached"
    assert len(site.requests) == 1


def test_legacy_song_during_cooldown_keeps_indexed_data(site):
    index_song("antiga", freqs={"C": 3, "G": 2})
    site.routes["/artista/antiga/"] = httpx.Response(429, headers={"Retry-After": "120"})
    data = TestClient(api.app).get(
        "/api/chords", params={"artist": "Artista", "track_name": "antiga"}
    ).json()
    assert data["status"] == "rate_limited"
    assert data["found"] is True
    assert data["sheet_available"] is False
    assert data["retry_after_seconds"] > 0
    assert data["chord_frequencies"] == {"C": 3, "G": 2}
    assert db.get_song_by_slugs("artista", "antiga") is not None


def test_normal_and_simplified_are_stored_separately(site):
    site.routes["/artista/musica/"] = httpx.Response(200, text=page(lyric="Versao normal"))
    site.routes["/artista/musica/simplificada.html"] = httpx.Response(200, text=page(lyric="Versao facil"))

    async def run():
        normal = await fetch_chord_sheet_async("Artista", "Musica")
        easy = await fetch_chord_sheet_async("Artista", "Musica", simplified=True)
        again = await fetch_chord_sheet_async("Artista", "Musica")
        return normal, easy, again

    normal, easy, again = asyncio.run(run())
    assert "Versao normal" in normal["raw_chord_sheet"]
    assert "Versao facil" in easy["raw_chord_sheet"] and easy["is_simplified"] is True
    assert "Versao normal" in again["raw_chord_sheet"] and again["status"] == "cached"


def test_missing_simplified_uses_normal_without_new_requests(site):
    site.routes["/artista/musica/"] = httpx.Response(200, text=page(lyric="Versao normal"))

    async def run():
        first = await fetch_chord_sheet_async("Artista", "Musica", simplified=True)
        count = len(site.requests)
        second = await fetch_chord_sheet_async("Artista", "Musica", simplified=True)
        return first, count, second

    first, count, second = asyncio.run(run())
    assert first["is_simplified"] is False
    assert second["status"] == "cached" and second["is_simplified"] is False
    assert len(site.requests) == count
    assert db.get_song_by_slugs("artista", "musica__simplificada") is None


def test_transposition_does_not_change_stored_sheet(site):
    site.routes["/artista/musica/"] = httpx.Response(200, text=page(chords=("C", "G")))
    client = TestClient(api.app)
    params = {"artist": "Artista", "track_name": "Musica"}
    client.get("/api/chords", params=params)
    moved = client.get("/api/chords", params={**params, "semitones": 2}).json()
    assert moved["raw_chord_sheet"].split()[:2] == ["D", "A"]
    assert db.get_song_by_slugs("artista", "musica")["raw_chord_sheet"].split()[:2] == ["C", "G"]


def test_catalog_filters_level_before_limit_and_hides_duplicates():
    db.update_user_profile(known_chords=["C", "G"], preferred_key="Bb")
    for i in range(3):
        index_song(f"facil-{i}", name=f"Facil {i}")
    index_song("facil-0__simplificada", name="Facil 0", freqs={"C": 9})
    index_song("dificil", name="Acentuação", freqs={"F": 9, "Bb": 9, "Eb": 9, "Fm": 9})
    client = TestClient(api.app)

    hard = client.get("/api/catalog", params={"level": "Difícil", "limit": 1}).json()
    assert [s["track_name"] for s in hard] == ["Acentuação"]
    assert len(client.get("/api/catalog").json()) == 4
    assert client.get("/api/status").json()["indexed_songs_count"] == 4
    assert [s["track_name"] for s in client.get("/api/catalog", params={"search": "acentuacao"}).json()] == ["Acentuação"]
    assert client.get("/api/catalog", params={"search": "%"}).json() == []


def test_spotify_items_pagination_and_field_fallback(monkeypatch):
    seen = []

    def handler(request):
        assert request.url.path == "/v1/playlists/abc/items"
        limit = int(request.url.params["limit"])
        offset = int(request.url.params["offset"])
        seen.append(limit)
        assert limit <= 50
        field = "item" if offset == 0 else "track"
        items = [
            {field: {"id": f"t{offset + i}", "name": f"Faixa {offset + i}", "artists": [{"name": "A"}]}}
            for i in range(limit)
        ]
        return httpx.Response(200, json={"items": items, "next": "more" if offset + limit < 70 else None})

    async def token(**_):
        return "token"

    monkeypatch.setattr(spotify, "_build_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(spotify, "is_spotify_configured", lambda: True)
    monkeypatch.setattr(spotify, "is_spotify_user_logged_in", lambda: True)
    monkeypatch.setattr(spotify, "get_valid_access_token", token)

    tracks = asyncio.run(spotify.fetch_playlist_tracks("abc", limit=70))
    assert len(tracks) == 70
    assert seen == [50, 20]
    assert tracks[69]["name"] == "Faixa 69"


def test_spotify_failure_falls_back_to_embed(monkeypatch):
    async def embed(_):
        return {"tracks": [{"track_id": "x", "name": "Embed", "artist": "A", "album": ""}]}

    async def token(**_):
        return "token"

    monkeypatch.setattr(spotify, "_build_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403)), **kw))
    monkeypatch.setattr(spotify, "is_spotify_configured", lambda: True)
    monkeypatch.setattr(spotify, "is_spotify_user_logged_in", lambda: True)
    monkeypatch.setattr(spotify, "get_valid_access_token", token)
    monkeypatch.setattr(spotify, "_fetch_public_playlist_via_embed", embed)
    assert asyncio.run(spotify.fetch_playlist_tracks("abc"))[0]["name"] == "Embed"


def test_free_search_lists_only_songs(site):
    docs = (
        '[{"d": "legiao-urbana", "a": "Legião Urbana"},'
        ' {"d": "legiao-urbana", "u": "tempo-perdido", "a": "Legião Urbana", "m": "Tempo Perdido"},'
        ' {"d": "legiao-urbana", "u": "tempo-perdido", "a": "Legião Urbana", "m": "Tempo Perdido"},'
        ' {"d": "../x", "u": "y", "a": "X", "m": "Y"}]'
    )
    site.routes["solr"] = httpx.Response(200, text="suggest_callback({\"response\":{\"docs\":%s}})" % docs)
    client = TestClient(api.app)
    response = client.get("/api/search", params={"q": "tempo perdido"})
    assert response.status_code == 200
    assert response.json() == [{"artist": "Legião Urbana", "track_name": "Tempo Perdido"}]


def test_free_search_reports_cooldown(site):
    site.routes["solr"] = httpx.Response(429, headers={"Retry-After": "60"})
    client = TestClient(api.app)
    response = client.get("/api/search", params={"q": "tempo perdido"})
    assert response.status_code == 429
    assert client.get("/api/search", params={"q": "x"}).status_code == 422


def test_delete_song_removes_sheet_and_repertoire():
    song_id = index_song("apagar", freqs={"C": 2, "G": 1}, sheet="C G\nletra")
    db.set_repertoire_entry(song_id, "practicing")
    client = TestClient(api.app)
    assert client.delete(f"/api/songs/{song_id}").json() == {"status": "deleted", "song_id": song_id}
    assert db.get_repertoire_entry(song_id) is None
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chord_sheets_cache WHERE song_id = ?", (song_id,)).fetchone()[0] == 0
    assert client.delete(f"/api/songs/{song_id}").status_code == 404
