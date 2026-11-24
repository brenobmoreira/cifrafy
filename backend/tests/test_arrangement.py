"""Adapted arrangement tests. Pure logic, plus the endpoint with mocked Cifra Club."""

import httpx
import pytest
from fastapi.testclient import TestClient

import src.api as api
from src.core import chord_scraper as scraper
from src.core.analyzer import parse_chord
from src.core.arrangement import build_arrangement, simplify_chord
from src.core.chord_scraper import cifraclub_limiter
from src.db import db

OPEN = ["C", "G", "D", "A", "E", "Am", "Em", "Dm"]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "db_path", tmp_path / "test.db")
    db._init_schema()
    monkeypatch.setattr(cifraclub_limiter, "min_interval", 0.0)
    cifraclub_limiter.reset()
    yield
    cifraclub_limiter.reset()


@pytest.mark.parametrize(
    "chord, expected",
    [("C7(9)", "C7"), ("G6", "G"), ("Cadd9", "C"), ("G/B", "G"), ("F", "Fmaj7"), ("Am7", "Am7"), ("C", "C")],
)
def test_conservative_substitutions(chord, expected):
    assert simplify_chord(chord, OPEN) == expected


def test_fmaj7_only_when_appropriate():
    assert simplify_chord("F", OPEN + ["F"], avoid_barre=False) == "F"
    assert simplify_chord("F7", OPEN) != "Fmaj7"
    assert simplify_chord("Fm", OPEN) != "Fmaj7"


def test_bm7_only_when_known():
    assert simplify_chord("Bm", OPEN) == "Bm"
    assert simplify_chord("Bm", OPEN + ["Bm7"]) == "Bm7"


def test_half_diminished_is_preserved():
    assert simplify_chord("Am7(b5)", OPEN) == "Am7(b5)"
    assert simplify_chord("Bm7(b5)/F", OPEN) == "Bm7(b5)"
    assert simplify_chord("Gº", OPEN) == "Gº"
    assert simplify_chord("Caug", OPEN) == "Caug"
    assert simplify_chord("G5", OPEN) == "G5"


def test_suspended_only_becomes_known_chord():
    assert simplify_chord("Dsus4", OPEN) == "D"
    assert simplify_chord("Bsus4", OPEN) == "Bsus4"
    assert simplify_chord("E7(4)", OPEN) == "E"


@pytest.mark.parametrize(
    "chord",
    ["C7(9)", "Am7(9)", "Dm9", "G/B", "Am/G", "F/A", "Bm7/A", "F#m7(11)", "Cm(maj7)", "Bb7M(9)", "Em7(4)", "Gm6", "A9"],
)
def test_never_changes_root_or_quality(chord):
    for known in (OPEN, OPEN + ["Fmaj7", "Bm7"], []):
        source, target = parse_chord(chord), parse_chord(simplify_chord(chord, known))
        assert target["root_idx"] == source["root_idx"]
        assert target["is_minor"] == source["is_minor"]


def test_easy_song_is_left_untouched():
    freqs = {"G": 10, "D": 10, "Em": 10, "C": 10}
    result = build_arrangement(freqs, "G D\nLetra", OPEN, True)
    assert result["capo_fret"] == 0
    assert result["substitutions"] == []
    assert result["fidelity_pct"] == 100.0
    assert result["sheet_lines"][0]["text"] == "G D"


def test_capo_keeps_full_fidelity():
    freqs = {"Bb": 25, "F": 25, "Gm": 25, "Eb": 25}
    result = build_arrangement(freqs, "Bb  F\nVou cantar", OPEN, True)
    assert result["capo_fret"] == 3
    assert result["level_before"] == "Difícil"
    assert result["level_after"] == "Fácil"
    assert result["fidelity_pct"] == 100.0
    assert {s["kind"] for s in result["substitutions"]} == {"capo"}
    assert result["sheet_lines"][0]["text"] == "G   D"
    assert result["sheet_lines"][1]["text"] == "Vou cantar"


def test_fidelity_weights_occurrences_and_lyrics_stay_intact():
    freqs = {"C": 30, "G": 30, "E": 10, "A": 10, "F": 20, "C7(9)": 20, "Am7(b5)": 10}
    sheet = "C7(9)    G\nA casa e nossa\nE|--0--2--|"
    known = ["C", "G", "E", "A"]
    result = build_arrangement(freqs, sheet, known, True)
    assert result["capo_fret"] == 0
    assert result["fidelity_pct"] == round(90 / 130 * 100, 1)
    assert {s["from"]: s["to"] for s in result["substitutions"]} == {"F": "Fmaj7", "C7(9)": "C7"}
    assert {s["from"]: s["kind"] for s in result["substitutions"]} == {"F": "avoid_barre", "C7(9)": "simplify"}
    assert result["sheet_lines"][0]["text"] == "C7       G"
    assert result["sheet_lines"][1]["text"] == "A casa e nossa"
    assert result["sheet_lines"][2]["text"] == "E|--0--2--|"
    assert "Am7(b5)" in result["remaining_hard"]
    assert result["note"]


def test_arrangement_is_deterministic():
    freqs = {"F#m": 10, "D": 10, "A": 10, "E": 10, "Bm7": 5, "C#7": 2}
    first = build_arrangement(freqs, "F#m D\nLetra", OPEN, True)
    assert first == build_arrangement(freqs, "F#m D\nLetra", OPEN, True)
    assert 0 <= first["capo_fret"] <= 7


def test_endpoint_uses_cache_without_network(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("não deveria acessar a rede")

    monkeypatch.setattr(scraper, "_build_client", fail)
    song_id = db.upsert_song_index(
        artist_slug="artista", track_slug="musica", artist_name="Artista", track_name="Musica", cifra_url="u",
        musical_key="Bb", chord_frequencies={"Bb": 25, "F": 25, "Gm": 25, "Eb": 25}, has_barre_chords=True,
        avg_changes_per_line=2.0, has_tabs=False, raw_chord_sheet="Bb  F\nLetra",
    )
    db.update_user_profile(known_chords=OPEN, avoid_barre=True)
    data = TestClient(api.app).get("/api/arrangement", params={"artist": "Artista", "track_name": "Musica"}).json()
    assert data["status"] == "cached"
    assert data["song_id"] == song_id
    assert data["capo_fret"] == 3
    assert data["chords"]


def test_endpoint_reports_rate_limit_without_crashing(monkeypatch):
    monkeypatch.setattr(
        scraper,
        "_build_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(429))),
    )
    response = TestClient(api.app).get("/api/arrangement", params={"artist": "Novo", "track_name": "Faixa"})
    assert response.status_code == 200
    assert response.json()["status"] == "rate_limited"
    assert response.json()["sheet_lines"] == []


@pytest.mark.parametrize("chord", ["F#m7(5-)", "Bm7(b5)", "C#m7(-5)", "G°", "Gº", "Caug", "C7(5+)"])
def test_altered_quality_is_never_removed(chord):
    simplified = simplify_chord(chord, OPEN)
    assert simplified == chord


def test_harmony_is_kept_when_song_cannot_get_easier():
    freqs = {"F7M/A": 4, "Fm9": 4, "F#m7(5-)": 4, "Bb7(13)": 4, "Eb7M": 4, "Ab7M": 4, "Db7M": 4}
    sheet = "  ".join(freqs) + "\nLetra da musica\n"
    result = build_arrangement(freqs, sheet, ["C"], True)
    assert result["level_before"] == result["level_after"] == "Difícil"
    assert result["fidelity_pct"] == 100.0
    assert all(item["kind"] == "capo" for item in result["substitutions"])
    assert "descaracterizar" in result["note"]
