"""Repertoire, catalog, learning, login and MCP tests. Nothing reaches the network."""

import asyncio
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import src.api as api
from src.config import settings
from src.core import library
from src.core import spotify_client as spotify
from src.db import db
from src.mcp_server import mcp


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "db_path", tmp_path / "test.db")
    db._init_schema()
    db.update_user_profile(known_chords=["C", "G", "D", "Em"], avoid_barre=True)


@pytest.fixture
def client():
    return TestClient(api.app, follow_redirects=False)


def add_song(slug, chords, sheet=None):
    return db.upsert_song_index(
        artist_slug="artista",
        track_slug=slug,
        artist_name="Artista",
        track_name=slug.title(),
        cifra_url=f"https://example.test/{slug}/",
        musical_key="C",
        chord_frequencies={c: 10 for c in chords},
        has_barre_chords=False,
        avg_changes_per_line=2.0,
        has_tabs=False,
        raw_chord_sheet=sheet,
    )


def test_catalog_with_capo_and_repertoire(client):
    easy = add_song("facil", ["C", "G"])
    capo = add_song("capo", ["Bb", "F", "Gm", "Eb"])
    client.put(f"/api/repertoire/{capo}", json={"status": "want", "preferred_capo": 3})

    plain = client.get("/api/catalog", params={"level": "Fácil"}).json()
    assert [s["id"] for s in plain] == [easy]

    items = {s["id"]: s for s in client.get("/api/catalog", params={"level": "Fácil", "with_capo": True}).json()}
    assert set(items) == {easy, capo}
    assert items[capo]["easy_with_capo"] is True
    assert items[capo]["repertoire"] == {"status": "want", "preferred_semitones": None, "preferred_capo": 3}
    assert items[easy]["repertoire"] is None


def test_repertoire_flow_and_validation(client):
    song = add_song("musica", ["C", "G"])
    assert client.put("/api/repertoire/9999", json={"status": "want"}).status_code == 404
    assert client.put(f"/api/repertoire/{song}", json={"status": "nope"}).status_code == 422
    assert client.put(f"/api/repertoire/{song}", json={"status": "want", "preferred_semitones": 12}).status_code == 422
    assert client.put(f"/api/repertoire/{song}", json={"status": "want", "preferred_capo": -1}).status_code == 422

    client.put(f"/api/repertoire/{song}", json={"status": "want", "preferred_semitones": 2})
    kept = client.put(f"/api/repertoire/{song}", json={"status": "practicing"}).json()
    assert kept == {"status": "practicing", "preferred_semitones": 2, "preferred_capo": None}

    listed = client.get("/api/repertoire", params={"status": "practicing"}).json()
    assert [s["id"] for s in listed] == [song]
    assert listed[0]["level"] == "Fácil"
    assert client.get("/api/repertoire", params={"status": "mastered"}).json() == []

    assert client.delete(f"/api/repertoire/{song}").status_code == 200
    assert client.delete(f"/api/repertoire/{song}").status_code == 404


def test_chords_returns_structured_transposed_sheet(client):
    song = add_song("musica", ["C", "G"], sheet="[Intro]\nC  G\nLetra aqui")
    client.put(f"/api/repertoire/{song}", json={"status": "mastered"})
    data = client.get(
        "/api/chords", params={"artist": "Artista", "track_name": "Musica", "semitones": 2}
    ).json()
    assert data["song_id"] == song
    assert data["repertoire"]["status"] == "mastered"
    assert data["sheet_lines"] == [
        {"type": "section", "text": "[Intro]"},
        {"type": "chords", "text": "D  A"},
        {"type": "lyric", "text": "Letra aqui"},
    ]


def test_playlist_tracks_include_song_and_repertoire(client, monkeypatch):
    song = add_song("musica", ["C", "G"])
    db.set_repertoire_entry(song, "want")

    async def tracks(playlist_id, limit):
        return [
            {"track_id": "x", "name": "Musica", "artist": "Artista", "album": ""},
            {"track_id": "y", "name": "Outra", "artist": "Artista", "album": ""},
        ]

    monkeypatch.setattr(library, "fetch_playlist_tracks", tracks)
    data = client.get("/api/playlists/abc/tracks").json()
    assert data[0]["song_id"] == song
    assert data[0]["repertoire"]["status"] == "want"
    assert data[1]["is_synced"] is False


def test_next_chords_endpoint(client, monkeypatch):
    add_song("um", ["C", "G", "F"])
    add_song("dois", ["C", "F"])
    add_song("tres", ["C", "Bm"])

    result = client.get("/api/learning/next-chords").json()
    assert result[0]["chord"] == "F"
    assert result[0]["songs_unlocked_easy"] == 2

    async def tracks(playlist_id, limit):
        return [{"track_id": "z", "name": "Tres", "artist": "Artista", "album": ""}]

    monkeypatch.setattr(library, "fetch_playlist_tracks", tracks)
    scoped = client.get("/api/learning/next-chords", params={"playlist_id": "abc"}).json()
    assert [s["chord"] for s in scoped] == ["Bm"]


def test_update_known_chords():
    profile = library.update_known_chords(add=["F", "C"], remove=["D"])
    assert profile["known_chords"] == ["C", "G", "Em", "F"]


def login_state(monkeypatch):
    monkeypatch.setattr(settings, "SPOTIFY_CLIENT_ID", "id")
    url = spotify.get_spotify_authorize_url()
    return parse_qs(urlparse(url).query)["state"][0]


def test_login_redirects_with_valid_state(client, monkeypatch):
    async def exchange(code):
        assert code == "abc"
        return {}

    monkeypatch.setattr(api, "exchange_code_for_token", exchange)
    state = login_state(monkeypatch)
    resp = client.get("/api/spotify/callback", params={"code": "abc", "state": state})
    assert resp.status_code == 303
    assert resp.headers["location"] == f"{settings.FRONTEND_URL}/?spotify=connected"

    replay = client.get("/api/spotify/callback", params={"code": "abc", "state": state})
    assert replay.headers["location"].endswith("spotify=error")


def test_login_rejects_bad_state_and_handles_denial(client, monkeypatch):
    async def exchange(code):
        raise AssertionError("não deveria trocar o código")

    monkeypatch.setattr(api, "exchange_code_for_token", exchange)
    bad = client.get("/api/spotify/callback", params={"code": "abc", "state": "forjado"})
    assert bad.headers["location"].endswith("spotify=error")

    state = login_state(monkeypatch)
    denied = client.get("/api/spotify/callback", params={"error": "access_denied", "state": state})
    assert denied.headers["location"].endswith("spotify=denied")


def test_no_demo_playlists():
    playlists = asyncio.run(spotify.fetch_user_playlists())
    assert playlists == []


def test_mcp_exposes_new_tools():
    tools = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert {"update_known_chords", "suggest_next_chords", "set_song_status", "list_repertoire"} <= tools
