"""Access control, path resolution, persistent cooldown and chord variant rules."""

import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.api as api
from src.config import SERVICE_ROOT, resolve_service_path
from src.core.analyzer import (
    evaluate_song_difficulty,
    extract_chord_sequence,
    structure_sheet,
    suggest_next_chords,
    transpose_sheet,
)
from src.core.chord_scraper import COOLDOWN_STATE_KEY, CifraClubLimiter, CifraClubRateLimited
from src.core import practice
from src.db import db


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "db_path", tmp_path / "test.db")
    db._init_schema()


def test_unknown_origin_gets_no_cors():
    response = TestClient(api.app).get("/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_unknown_origin_preflight_is_refused():
    response = TestClient(api.app).options(
        "/api/profile",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "PUT"},
    )
    assert response.status_code == 400


@pytest.mark.parametrize("origin", ["http://127.0.0.1:3000", "http://localhost:3000"])
def test_local_origin_is_allowed(origin):
    response = TestClient(api.app).get("/health", headers={"Origin": origin})
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("path", ["/health", "/api/profile", "/mcp/"])
def test_unknown_host_is_blocked(path):
    response = TestClient(api.app).get(path, headers={"Host": "evil.example"})
    assert response.status_code == 400


def test_local_host_is_allowed():
    assert TestClient(api.app).get("/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_relative_paths_start_at_backend(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert resolve_service_path("./data/guitar.db") == SERVICE_ROOT / "data" / "guitar.db"
    assert resolve_service_path("/opt/x.db") == Path("/opt/x.db")


def _blocked_client():
    transport = httpx.MockTransport(lambda request: httpx.Response(429, headers={"Retry-After": "120"}))
    return httpx.AsyncClient(transport=transport)


async def _trigger_block(limiter):
    async with _blocked_client() as client:
        with pytest.raises(CifraClubRateLimited):
            await limiter.get(client, "https://cifra.test/")


def test_cooldown_survives_new_instance():
    import asyncio

    first = CifraClubLimiter(0.0, 60.0)
    asyncio.run(_trigger_block(first))
    assert 100 < CifraClubLimiter(0.0, 60.0).cooldown_remaining() <= 120


def test_expired_cooldown_is_ignored():
    db.set_service_value(COOLDOWN_STATE_KEY, time.time() - 5)
    assert CifraClubLimiter(0.0, 60.0).cooldown_remaining() == 0


def test_reset_clears_saved_cooldown():
    db.set_service_value(COOLDOWN_STATE_KEY, time.time() + 500)
    limiter = CifraClubLimiter(0.0, 60.0)
    assert limiter.cooldown_remaining() > 0
    limiter.reset()
    assert limiter.cooldown_remaining() == 0
    assert CifraClubLimiter(0.0, 60.0).cooldown_remaining() == 0


@pytest.mark.parametrize("spelling", ["Cmaj7", "CM7", "C7+", "C7M"])
def test_major_seventh_spellings_are_equal(spelling):
    result = evaluate_song_difficulty([spelling], ["C7M"], False, {spelling: 4})
    assert result["unknown_chords"] == []


def _song(song_id, **freqs):
    return {"id": song_id, "artist": "A", "track_name": f"S{song_id}", "chord_frequencies": freqs}


def test_variants_are_one_suggestion_named_by_most_frequent():
    songs = [_song(1, G=10, Bm=4), _song(2, G=10, Bm7=9)]
    result = suggest_next_chords(songs, ["G"], True)
    assert [item["chord"] for item in result] == ["Bm7"]
    assert result[0]["songs_unlocked_easy"] == 2


def test_major_and_minor_are_not_grouped():
    songs = [_song(1, G=10, B=5), _song(2, G=10, Bm=5)]
    assert {item["chord"] for item in suggest_next_chords(songs, ["G"], True)} == {"B", "Bm"}


@pytest.mark.parametrize("other", ["Bdim", "Bm7(b5)", "Bm7(5-)", "Bsus4"])
def test_altered_chords_stay_separate(other):
    songs = [
        {"id": 1, "artist": "A", "track_name": "x", "chord_frequencies": {"G": 10, "Bm": 5}},
        {"id": 2, "artist": "A", "track_name": "y", "chord_frequencies": {"G": 10, other: 5}},
    ]
    assert {item["chord"] for item in suggest_next_chords(songs, ["G"], True)} == {"Bm", other}


def test_trail_does_not_repeat_variant(monkeypatch):
    songs = [
        {"id": 1, "artist": "A", "track_name": "x", "chord_frequencies": {"G": 10, "Bm": 6}},
        {"id": 2, "artist": "A", "track_name": "y", "chord_frequencies": {"G": 10, "Bm7": 5}},
        {"id": 3, "artist": "A", "track_name": "z", "chord_frequencies": {"G": 10, "F": 5}},
    ]

    async def scope(_playlist_id):
        return songs

    monkeypatch.setattr(practice.library, "songs_in_scope", scope)
    db.update_user_profile(known_chords=["G"], avoid_barre=True)
    import asyncio

    trail = asyncio.run(practice.learning_trail())
    chords = [step["chord"] for step in trail["steps"]]
    assert sorted(chords) == ["Bm", "F"]
    assert trail["steps"][-1]["total_easy_after"] == 3


def test_ambiguous_chord_before_clear_chord_line():
    sheet = "C G\nAm\nF\nLetra"
    assert [line["type"] for line in structure_sheet(sheet)] == ["chords", "chords", "chords", "lyric"]
    assert transpose_sheet(sheet, 2) == "D A\nBm\nG\nLetra"
    assert extract_chord_sequence(sheet) == ["C", "G", "Am", "F"]


def test_ambiguous_run_after_section_is_chords():
    sheet = "[Refrão]\nA\nE\nVou cantar"
    assert transpose_sheet(sheet, 2) == "[Refrão]\nB\nF#\nVou cantar"
    assert extract_chord_sequence(sheet) == ["A", "E"]


def test_ambiguous_words_after_chord_line_stay_lyric():
    sheet = "C G\nE\nA\nDo jeito que for"
    assert transpose_sheet(sheet, 2) == "D A\nE\nA\nDo jeito que for"
    assert extract_chord_sequence(sheet) == ["C", "G"]


def test_known_barre_covers_its_plain_seventh():
    result = evaluate_song_difficulty(["G", "Bm7"], ["G", "Bm"], False, {"G": 10, "Bm7": 8})
    assert result["unknown_chords"] == []
    assert result["level"] == "Fácil"
    assert suggest_next_chords([_song(1, G=10, Bm7=8)], ["G", "Bm"], True) == []


def test_known_barre_does_not_cover_harder_variants():
    for chord in ("Bm9", "Bm/A", "Bm7(b5)"):
        assert evaluate_song_difficulty([chord], ["Bm"], False, {chord: 4})["unknown_chords"] == [chord]


def test_zero_width_spaces_do_not_hide_chords():
    sheet = "​[Intro] Em  C  D (12x)\n​       Em    C\n​Nas favelas\n​( Em  C  D )"
    types = [line["type"] for line in structure_sheet(sheet)]
    assert types == ["chords", "chords", "lyric", "chords"]
    assert transpose_sheet(sheet, 2).splitlines()[1] == "       F#m   D"
    assert extract_chord_sequence(sheet) == ["Em", "C", "D", "Em", "C", "Em", "C", "D"]


def test_old_sequences_are_recomputed():
    song_id = db.upsert_song_index(
        artist_slug="a", track_slug="b", artist_name="A", track_name="B", cifra_url="u", musical_key=None,
        chord_frequencies={"G": 1}, has_barre_chords=False, avg_changes_per_line=1.0, has_tabs=False,
        raw_chord_sheet="​G  C\nLetra",
    )
    with db.connect() as conn:
        conn.execute("UPDATE song_index SET chord_sequence_json = '[]' WHERE id = ?", (song_id,))
        conn.execute("DELETE FROM service_state")
    db._init_schema()
    assert db.get_chord_sequences()[song_id] == ["G", "C"]


def test_cached_song_found_when_url_differs_from_title(monkeypatch):
    from src.core import chord_scraper as scraper

    def fail(*_args, **_kwargs):
        raise AssertionError("não deveria acessar a rede")

    monkeypatch.setattr(scraper, "_build_client", fail)
    db.upsert_song_index(
        artist_slug="legiao-urbana", track_slug="que-pais-e-esse", artist_name="Legião Urbana",
        track_name="Que País É Este", cifra_url="u", musical_key=None, chord_frequencies={"Em": 3},
        has_barre_chords=False, avg_changes_per_line=1.0, has_tabs=False, raw_chord_sheet="Em  C\nLetra",
    )
    data = TestClient(api.app).get("/api/chords", params={"artist": "Legião Urbana", "track_name": "Que País É Este"}).json()
    assert data["cached"] is True
    assert data["sheet_lines"][0]["text"] == "Em  C"
