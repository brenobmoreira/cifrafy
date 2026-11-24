"""Learning trail and chord change drill tests. Nothing reaches the network."""

import asyncio
import sqlite3
import zlib

import pytest
from fastapi.testclient import TestClient

import src.api as api
from src.core import practice
from src.core.analyzer import chord_change_key, extract_chord_sequence
from src.db import Database, db
from src.mcp_server import mcp


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "db_path", tmp_path / "test.db")
    db._init_schema()
    db.update_user_profile(known_chords=["C", "G", "D", "Em"], avoid_barre=True)


@pytest.fixture
def client():
    return TestClient(api.app)


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


def test_extract_sequence_ignores_lyrics_tabs_and_notation():
    sheet = "[Intro] C  G (2x)\n\nAm     F\nA casa e nossa\nE|--0--2--|\nB|--1--3--|\n| C7(9) | N.C. |\n"
    assert extract_chord_sequence(sheet) == ["C", "G", "Am", "F", "C7(9)"]
    assert extract_chord_sequence("") == []


def test_change_key_has_no_direction_and_is_enharmonic():
    assert chord_change_key("G", "C") == chord_change_key("C", "G")
    assert chord_change_key("Bbm", "C") == chord_change_key("C", "A#m")
    assert chord_change_key("Bb", "A#") is None
    assert chord_change_key("C", "N.C.") is None
    assert chord_change_key("C", "C/E") is not None


def test_upsert_keeps_sequence_when_sheet_is_absent():
    song = add_song("musica", ["C", "G"], "C  G\nLetra da musica\n")
    add_song("musica", ["C", "G"])
    assert db.get_chord_sequences()[song] == ["C", "G"]


def test_backfill_from_old_schema_is_idempotent_and_survives_bad_sheet(tmp_path):
    path = tmp_path / "old.db"
    Database(path)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE song_index DROP COLUMN chord_sequence_json")
    for song_id, blob in ((1, zlib.compress(b"C  G\nLetra\n")), (2, b"not zlib")):
        conn.execute(
            """INSERT INTO song_index (id, artist_slug, track_slug, artist_name, track_name, cifra_url, updated_at)
               VALUES (?, 'a', ?, 'A', 'T', 'u', 'now')""",
            (song_id, f"t{song_id}"),
        )
        conn.execute(
            "INSERT INTO chord_sheets_cache (song_id, compressed_sheet, last_accessed_at) VALUES (?, ?, 'now')",
            (song_id, blob),
        )
    conn.commit()
    conn.close()

    migrated = Database(path)
    assert migrated.get_chord_sequences() == {1: ["C", "G"]}
    assert Database(path).get_chord_sequences() == {1: ["C", "G"]}


def test_trail_is_greedy_keeps_learned_barres_and_does_not_change_profile(client):
    add_song("um", ["C", "G", "Bm"])
    add_song("dois", ["D", "Em", "Bm"])
    add_song("tres", ["C", "Bm", "F"])
    add_song("facil", ["C", "G"])

    body = client.get("/api/practice/trail").json()

    assert body["current_easy"] == 1
    assert body["total_songs"] == 4
    assert [s["chord"] for s in body["steps"]] == ["Bm", "F"]
    assert [s["step"] for s in body["steps"]] == [1, 2]
    assert body["steps"][0]["songs_unlocked_easy"] == 2
    assert body["steps"][0]["total_easy_after"] == 3
    assert body["steps"][1]["total_easy_after"] == 4
    assert body["steps"][0]["examples"][0]["song_id"]
    assert db.get_user_profile()["known_chords"] == ["C", "G", "D", "Em"]


def test_changes_merge_directions_and_count_songs(client):
    add_song("um", ["C", "G"], "C  G  C  C  G\nLetra\n")
    add_song("dois", ["G", "C"], "G  C\nLetra\n")
    add_song("sem-texto", ["D"])

    body = client.get("/api/practice/changes").json()

    assert body["songs_without_sequence"] == 1
    assert len(body["changes"]) == 1
    change = body["changes"][0]
    assert {change["from"], change["to"]} == {"C", "G"}
    assert change["occurrences"] == 4
    assert change["songs"] == 2
    assert change["involves_unknown"] is False
    assert change["best_per_minute"] is None
    assert change["sessions"] == 0
    assert change["target_per_minute"] == 30


def test_priority_favors_active_songs_unknown_chords_and_below_target(client):
    plain = add_song("plain", ["C", "G"], "C  G\nLetra\n")
    active = add_song("active", ["D", "Em"], "D  Em\nLetra\n")
    add_song("novo", ["C", "Am"], "C  Am\nLetra\n")
    client.put(f"/api/repertoire/{active}", json={"status": "practicing"})

    order = [{c["from"], c["to"]} for c in client.get("/api/practice/changes").json()["changes"]]
    assert order == [{"D", "Em"}, {"C", "Am"}, {"C", "G"}]

    client.post("/api/practice/changes", json={"from": "D", "to": "Em", "count": 40})
    order = [{c["from"], c["to"]} for c in client.get("/api/practice/changes").json()["changes"]]
    assert order[-1] == {"D", "Em"}
    assert plain


def test_changes_use_preferred_tone_and_song_scope(client):
    song = add_song("um", ["C", "G"], "C  G\nLetra\n")
    add_song("dois", ["D", "Em"], "D  Em\nLetra\n")
    client.put(f"/api/repertoire/{song}", json={"status": "want", "preferred_semitones": 2})

    change = client.get("/api/practice/changes", params={"song_id": song}).json()["changes"]
    assert len(change) == 1
    assert (change[0]["from"], change[0]["to"]) == ("D", "A")
    assert client.get("/api/practice/changes", params={"song_id": 9999}).status_code == 404


def test_log_change_tracks_progress_in_any_direction(client):
    first = client.post("/api/practice/changes", json={"from": "G", "to": "C", "count": 20}).json()
    assert first["improved"] is True
    assert first["session"]["per_minute"] == 20.0

    second = client.post(
        "/api/practice/changes",
        json={"from": "C", "to": "G", "count": 15, "duration_seconds": 30, "bpm": 80},
    ).json()
    assert second["session"]["per_minute"] == 30.0
    assert second["best_per_minute"] == 30.0
    assert second["sessions"] == 2
    assert second["improved"] is True

    third = client.post("/api/practice/changes", json={"from": "G", "to": "C", "count": 10}).json()
    assert third["improved"] is False
    assert third["best_per_minute"] == 30.0
    assert third["last_per_minute"] == 10.0

    history = client.get("/api/practice/changes/history", params={"from": "C", "to": "G"}).json()
    assert (history["from"], history["to"]) == ("C", "G")
    assert [s["per_minute"] for s in history["sessions"]] == [20.0, 30.0, 10.0]
    assert history["sessions"][1]["bpm"] == 80
    recent = client.get("/api/practice/changes/history", params={"from": "C", "to": "G", "limit": 2}).json()
    assert [s["per_minute"] for s in recent["sessions"]] == [30.0, 10.0]


@pytest.mark.parametrize(
    "payload",
    [
        {"from": "C", "to": "C", "count": 10},
        {"from": "Bb", "to": "A#", "count": 10},
        {"from": "C", "to": "banana", "count": 10},
        {"from": "C", "to": "G", "count": 301},
        {"from": "C", "to": "G", "count": 10, "duration_seconds": 14},
        {"from": "C", "to": "G", "count": 10, "bpm": 20},
    ],
)
def test_log_change_rejects_invalid_input(client, payload):
    assert client.post("/api/practice/changes", json=payload).status_code == 422
    assert db.get_practice_stats() == {}


def test_history_rejects_invalid_change(client):
    response = client.get("/api/practice/changes/history", params={"from": "C", "to": "C"})
    assert response.status_code == 422


def test_log_change_validates_outside_the_api():
    with pytest.raises(ValueError):
        practice.log_change("C", "G", 500)


def test_mcp_exposes_practice_tools():
    tools = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert {"get_learning_trail", "get_practice_changes", "log_practice_change"} <= tools
