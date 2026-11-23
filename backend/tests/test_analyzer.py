"""Unit tests for the Guitar Difficulty Analyzer and Capo Transposer."""

from src.core.analyzer import (
    evaluate_song_difficulty,
    find_best_capo,
    is_barre_chord,
    transpose_chord,
)
from src.core.chord_scraper import clean_track_name, parse_cifraclub_html, slugify


def test_barre_detection() -> None:
    assert is_barre_chord("F") is True
    assert is_barre_chord("Bm") is True
    assert is_barre_chord("F#m7") is True
    assert is_barre_chord("Bb") is True
    assert is_barre_chord("Cm") is True
    assert is_barre_chord("Gm") is True

    # Open chords and non-barre exceptions
    assert is_barre_chord("C") is False
    assert is_barre_chord("G") is False
    assert is_barre_chord("Am7") is False
    assert is_barre_chord("F7M") is False


def test_easy_song_evaluation() -> None:
    known = ["C", "G", "D", "A", "E", "Am", "Em", "Dm"]
    res = evaluate_song_difficulty(
        unique_chords=["G", "D", "Em", "C"],
        known_chords=known,
        avoid_barre=True,
        chord_frequencies={"G": 30, "D": 28, "Em": 30, "C": 28},
        avg_changes_per_line=2.1,
    )
    assert res["level"] == "Fácil"
    assert res["has_barre"] is False
    assert res["score"] >= 90
    assert res["weighted_known_pct"] == 100.0


def test_rare_intruder_barre_chord_does_not_ruin_easy_song() -> None:
    """A song that is 99% G, D, Em, C and has a single passing Bm (1x) should still be Fácil."""
    known = ["C", "G", "D", "A", "E", "Am", "Em", "Dm"]
    res = evaluate_song_difficulty(
        unique_chords=["G", "D", "Em", "C", "Bm"],
        known_chords=known,
        avoid_barre=True,
        chord_frequencies={"G": 40, "D": 35, "Em": 35, "C": 35, "Bm": 1},
        avg_changes_per_line=2.0,
    )
    assert res["level"] == "Fácil"
    assert res["rare_barre_chords"] == ["Bm"]
    assert res["core_barre_chords"] == []
    assert res["weighted_known_pct"] >= 98.0


def test_hard_song_with_multiple_barres_and_capo_escape() -> None:
    """A song in Bb major (Bb, F, Gm, Eb) has 4 barre chords, but with Capo on 3rd fret becomes G, D, Em, C (0 barres!)."""
    known = ["C", "G", "D", "A", "E", "Am", "Em", "Dm"]
    freqs = {"Bb": 25, "F": 25, "Gm": 25, "Eb": 25}
    res = evaluate_song_difficulty(
        unique_chords=list(freqs.keys()),
        known_chords=known,
        avoid_barre=True,
        chord_frequencies=freqs,
    )
    assert res["level"] == "Difícil"
    assert res["has_barre"] is True
    assert res["capo_suggestion"] is not None
    assert res["capo_suggestion"]["capo_fret"] == 3
    assert set(res["capo_suggestion"]["transposed_chords"]) == {"G", "D", "Em", "C"}


def test_transpose_and_slugify() -> None:
    assert transpose_chord("Bb", -3) == "G"
    assert transpose_chord("Gm", -3) == "Em"
    assert clean_track_name("Primeiros Erros (Chove) - Ao Vivo") == "Primeiros Erros (Chove)"
    assert clean_track_name("Wonderwall - Remastered 2014") == "Wonderwall"
    assert slugify("Legião Urbana") == "legiao-urbana"


def test_parse_cifraclub_html() -> None:
    sample_html = """
    <html>
      <body>
        <h1 class="t1">Tempo Perdido</h1>
        <h2 class="t3">Legião Urbana</h2>
        <span id="cifra_tom"><a href="#">G</a></span>
        <pre>
[Intro] <b>C</b>  <b>Am</b>  <b>Bm</b>  <b>Em</b>

<b>C</b>                  <b>Am</b>
Todos os dias quando acordo
<b>Bm</b>                   <b>Em</b>
Não tenho mais o tempo que passou
        </pre>
      </body>
    </html>
    """
    parsed = parse_cifraclub_html(sample_html, "https://www.cifraclub.com.br/legiao-urbana/tempo-perdido/")
    assert parsed is not None
    assert parsed["key"] == "G"
    assert parsed["unique_chords"] == ["C", "Am", "Bm", "Em"]
    assert parsed["chord_frequencies"] == {"C": 2, "Am": 2, "Bm": 2, "Em": 2}
