"""Ready practice session and listening recommendation tests. All HTTP is mocked."""

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import src.api as api
from src.core import practice, spotify_client as spotify
from src.db import db


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "db_path", tmp_path / "test.db")
    db._init_schema()
    db.update_user_profile(known_chords=["C", "G", "D", "Em"], avoid_barre=True)
    monkeypatch.setattr(spotify.settings, "SPOTIFY_CACHE_PATH", str(tmp_path / "token.json"))
    monkeypatch.setattr(spotify.settings, "SPOTIFY_CLIENT_ID", "client")
    monkeypatch.setattr(spotify.settings, "SPOTIFY_CLIENT_SECRET", "secret")


@pytest.fixture
def client():
    return TestClient(api.app)


def add_song(slug, chords, spotify_id=None):
    sheet = "\n".join(["  ".join(chords), "Letra da musica"] * 3)
    return db.upsert_song_index(
        artist_slug="artista", track_slug=slug, artist_name="Artista", track_name=slug.title(),
        cifra_url="u", musical_key="C", chord_frequencies={c: 10 for c in chords}, has_barre_chords=False,
        avg_changes_per_line=2.0, has_tabs=False, raw_chord_sheet=sheet, spotify_track_id=spotify_id,
    )


def login():
    token = {"access_token": "t", "refresh_token": "r", "expires_at": int(time.time()) + 600}
    spotify._save_cached_token(token)


def mock_spotify(monkeypatch, handler):
    monkeypatch.setattr(
        spotify, "_build_client",
        lambda **_kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def track(track_id, name, artist="Artista"):
    return {"id": track_id, "name": name, "type": "track", "artists": [{"name": artist}], "album": {"name": "Disco"}}


def test_full_session_has_every_block_and_exact_minutes(client):
    easy = add_song("facil", ["C", "G", "D", "Em"])
    going = add_song("andamento", ["C", "G", "Am", "F"])
    db.set_repertoire_entry(easy, "mastered")
    db.set_repertoire_entry(going, "practicing")

    for minutes in (5, 7, 20, 33, 120):
        data = client.get("/api/practice/session", params={"minutes": minutes}).json()
        assert data["minutes"] == minutes
        assert [b["kind"] for b in data["blocks"]] == ["warmup", "mastered", "in_progress", "new_chord"]
        assert sum(b["minutes"] for b in data["blocks"]) == minutes
        assert all(b["minutes"] >= 1 and b["instruction"] for b in data["blocks"])

    blocks = {b["kind"]: b for b in client.get("/api/practice/session").json()["blocks"]}
    assert blocks["mastered"]["song"]["song_id"] == easy
    assert blocks["in_progress"]["song"]["song_id"] == going
    assert blocks["in_progress"]["song"]["level"]
    assert set(blocks["warmup"]["change"]) == {"from", "to", "target_per_minute"}
    assert blocks["new_chord"]["chord"]["chord"]


def test_session_is_deterministic(client):
    add_song("a", ["C", "G", "Am"])
    add_song("b", ["D", "F", "Em"])
    assert client.get("/api/practice/session").json() == client.get("/api/practice/session").json()


def test_missing_blocks_redistribute_time(client):
    add_song("facil", ["C", "G"])
    data = client.get("/api/practice/session", params={"minutes": 10}).json()
    assert "mastered" not in [b["kind"] for b in data["blocks"]]
    assert sum(b["minutes"] for b in data["blocks"]) == 10


def test_empty_library_has_no_blocks(client):
    assert client.get("/api/practice/session").json() == {"minutes": 20, "blocks": []}


def test_session_limits(client):
    assert client.get("/api/practice/session", params={"minutes": 4}).status_code == 422
    assert client.get("/api/practice/session", params={"minutes": 121}).status_code == 422
    with pytest.raises(ValueError):
        asyncio.run(practice.build_session(4))


def test_scope_asks_for_listening_permissions():
    assert "user-top-read" in spotify.SPOTIFY_SCOPE
    assert "user-read-recently-played" in spotify.SPOTIFY_SCOPE


def test_listening_without_login(client):
    data = client.get("/api/recommendations/listening").json()
    assert data == {"connected": False, "needs_reconnect": False, "tracks": []}


@pytest.mark.parametrize("status", [401, 403])
def test_listening_asks_to_reconnect(client, monkeypatch, status):
    login()
    mock_spotify(monkeypatch, lambda request: httpx.Response(status))
    data = client.get("/api/recommendations/listening").json()
    assert data == {"connected": True, "needs_reconnect": True, "tracks": []}


def test_top_tracks_are_ranked_by_closeness(client, monkeypatch):
    login()
    easy = add_song("facil", ["C", "G", "D", "Em"], "a" * 22)
    medium = add_song("medio", ["C", "G", "D", "F"], "b" * 22)
    hard = add_song("dificil", ["F", "Bm", "Eb", "Ab"], "c" * 22)
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        items = [
            track("z" * 22, "Nao Sincronizada"),
            track("c" * 22, "Dificil"),
            track("b" * 22, "Medio"),
            track("a" * 22, "Facil"),
        ]
        return httpx.Response(200, json={"items": items})

    mock_spotify(monkeypatch, handler)
    data = client.get("/api/recommendations/listening", params={"time_range": "long_term", "limit": 10}).json()
    assert seen["path"] == "/v1/me/top/tracks"
    assert seen["params"] == {"limit": "10", "time_range": "long_term"}
    assert [t.get("song_id") for t in data["tracks"]] == [easy, medium, hard, None]
    assert [t["closeness"] for t in data["tracks"]] == [3, 2, 1, 0]
    assert data["tracks"][-1]["is_synced"] is False


def test_recent_tracks_are_unnested_and_deduplicated(client, monkeypatch):
    login()
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        items = [
            {"track": track("x" * 22, "Repetida"), "played_at": "2026-11-23T10:00:00Z"},
            {"track": track("x" * 22, "Repetida"), "played_at": "2026-11-23T09:00:00Z"},
            {"track": track("y" * 22, "Outra"), "played_at": "2026-11-23T08:00:00Z"},
        ]
        return httpx.Response(200, content=json.dumps({"items": items}))

    mock_spotify(monkeypatch, handler)
    data = client.get("/api/recommendations/listening", params={"source": "recent", "limit": 1}).json()
    assert seen["path"] == "/v1/me/player/recently-played"
    assert [t["track_id"] for t in data["tracks"]] == ["x" * 22]


def test_listening_validates_input(client):
    assert client.get("/api/recommendations/listening", params={"source": "x"}).status_code == 422
    assert client.get("/api/recommendations/listening", params={"limit": 51}).status_code == 422
