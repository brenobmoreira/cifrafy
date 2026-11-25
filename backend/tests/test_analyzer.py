"""Unit tests for the Guitar Difficulty Analyzer and Capo Transposer."""

from src.core.analyzer import (
    evaluate_song_difficulty,
    find_best_capo,
    is_barre_chord,
    parse_chord,
    structure_sheet,
    suggest_next_chords,
    transpose_chord,
    transpose_sheet,
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


def test_parse_chord_accepts_brazilian_notation() -> None:
    for chord in ("Gº", "F#º", "C7(9/11)", "C7+", "E7(4)", "C7/9", "Am7(b5)", "Cm(maj7)", "Cadd9", "Bbm7/Ab"):
        parsed = parse_chord(chord)
        assert parsed["root_idx"] != -1, chord
        assert "quality" in parsed
    assert parse_chord("Bbm7/Ab")["bass"] == "Ab"
    assert parse_chord("C7(9/11)")["bass"] is None


def test_non_chord_tokens_do_not_crash_or_count_as_hard() -> None:
    parsed = parse_chord("N.C.")
    assert parsed["root_idx"] == -1
    assert parsed["quality"] == ""
    assert parsed["is_barre"] is False

    freqs = {"N.C.": 5, "G": 10, "C": 10}
    res = evaluate_song_difficulty(list(freqs), ["G", "C"], True, freqs)
    assert res["level"] == "Fácil"
    assert res["weighted_known_pct"] == 100.0
    assert "N.C." not in res["unknown_chords"]
    assert find_best_capo({"N.C.": 1, "F": 3}, ["E"])["capo_fret"] == 1
    assert evaluate_song_difficulty(["N.C."])["level"] == "Desconhecido"


def test_unusual_chords_with_barre_do_not_crash_evaluation() -> None:
    for freqs in ({"Gº": 2, "F": 2}, {"C7(9/11)": 2, "Bm": 2}):
        assert evaluate_song_difficulty(list(freqs), ["C"], True, freqs)["level"]


def test_known_barre_is_not_treated_as_unknown() -> None:
    freqs = {"F": 25, "Am": 25, "C": 25, "G": 25}
    unknown = evaluate_song_difficulty(list(freqs), ["C", "G"], True, freqs)
    known = evaluate_song_difficulty(list(freqs), ["C", "G", "F", "Am"], True, freqs)
    assert unknown["level"] == "Difícil"
    assert known["level"] == "Médio"
    assert known["score"] > unknown["score"]
    assert set(known["known_barre_chords"]) == {"F"}


def test_avoid_barre_changes_level_for_known_barres() -> None:
    freqs = {"F": 25, "Bm": 25, "C": 25, "G": 25}
    known = ["C", "G", "F", "Bm"]
    avoiding = evaluate_song_difficulty(list(freqs), known, True, freqs)
    allowing = evaluate_song_difficulty(list(freqs), known, False, freqs)
    assert avoiding["level"] == "Difícil"
    assert allowing["level"] == "Fácil"
    assert allowing["score"] > avoiding["score"]


def test_known_chords_match_enharmonic_spelling() -> None:
    res = evaluate_song_difficulty(["A#m"], ["Bbm"], False, {"A#m": 4})
    assert res["unknown_chords"] == []


def test_capo_respects_empty_known_chords() -> None:
    assert find_best_capo({"F": 1, "C": 1}, [])["weighted_known_pct"] == 0.0


def test_transpose_uses_usual_spelling() -> None:
    assert [transpose_chord(c, 1) for c in ("A", "D", "G", "C")] == ["Bb", "Eb", "Ab", "Db"]
    assert transpose_chord("Dm", -1) == "C#m"
    assert transpose_chord("F", -1) == "E"
    assert transpose_chord("C#", 2) == "Eb"
    assert transpose_chord("Bbm", 3) == "Dbm"
    assert transpose_chord("D/F#", 2) == "E/G#"
    assert transpose_chord("Am/G", 1) == "Bbm/Ab"
    assert transpose_chord("C/Bb", 2) == "D/C"
    assert transpose_chord("Gº", 2) == "Aº"
    assert transpose_chord("N.C.", 2) == "N.C."


def test_transpose_sheet_changes_only_chords() -> None:
    sheet = "[Intro] C  Am\n\nG      D\nA casa é nossa\nC   G\nE\nA\nDo jeito que for\n"
    assert transpose_sheet(sheet, 2) == (
        "[Intro] D  Bm\n\nA      E\nA casa é nossa\nD   A\nE\nA\nDo jeito que for\n"
    )


def test_transpose_sheet_transposes_standalone_ambiguous_chord() -> None:
    assert transpose_sheet("E\nVou seguir\n", 2) == "F#\nVou seguir\n"
    assert transpose_sheet("[Refrão] A E\n", 2) == "[Refrão] B F#\n"


def test_transpose_sheet_preserves_alignment_and_tabs() -> None:
    assert transpose_sheet("C       G", 1) == "Db      Ab"
    assert transpose_sheet("Db      Ab", -1) == "C       G"
    tab = "E|--0--2--|\nB|--1--3--|"
    assert transpose_sheet(tab, 2) == tab
    assert transpose_sheet("(G  D) | C7(9) |", 2) == "(A  E) | D7(9) |"
    assert transpose_sheet("C G", 0) == "C G"


def test_level_follows_the_user_rule() -> None:
    known = ["C", "G", "D", "Am", "Em"]
    level = lambda *chords: evaluate_song_difficulty(list(chords), known, True, {c: 10 for c in chords})["level"]
    assert level("C", "G", "A7M", "E") == "Fácil"  # no barre and <= 4 chords
    assert level("C", "G", "D", "Am", "Em", "C/E") == "Fácil"  # all known
    assert level("C", "G", "D", "Am", "Em", "A") == "Médio"  # 1 new chord
    assert level("C", "G", "D", "F") == "Médio"  # 1 barre
    assert level("C", "G", "D", "Am", "A", "E") == "Difícil"  # 2 new chords
    assert level("C", "G", "F", "Bm") == "Difícil"  # 2 barres


def test_density_changes_score_but_not_level() -> None:
    freqs = {"C": 10, "G": 10}
    slow = evaluate_song_difficulty(list(freqs), ["C", "G"], True, freqs, avg_changes_per_line=2.0)
    fast = evaluate_song_difficulty(list(freqs), ["C", "G"], True, freqs, avg_changes_per_line=6.0)
    assert slow["level"] == fast["level"] == "Fácil"
    assert fast["score"] < slow["score"]


def test_capo_that_makes_song_easy() -> None:
    freqs = {"Bb": 25, "F": 25, "Gm": 25, "Eb": 25}
    res = evaluate_song_difficulty(list(freqs), ["C", "G", "D", "Em"], True, freqs)
    assert res["easy_with_capo"] is True
    assert res["capo_suggestion"]["capo_fret"] == 3
    assert res["capo_suggestion"]["makes_easy"] is True
    assert res["capo_suggestion"]["remaining_barres"] == []
    assert "!" not in res["capo_suggestion"]["message"]


def test_easy_song_has_no_capo_flag() -> None:
    freqs = {"C": 10, "G": 10}
    res = evaluate_song_difficulty(list(freqs), ["C", "G"], True, freqs)
    assert res["easy_with_capo"] is False
    assert res["capo_suggestion"] is None


def test_structure_sheet_types_and_transposition() -> None:
    sheet = "[Intro]\nC  G\nA casa\n\nE|--0--|\nC\nA\n"
    lines = structure_sheet(sheet, 2)
    assert [line["type"] for line in lines] == ["section", "chords", "lyric", "blank", "tab", "chords", "lyric", "blank"]
    assert lines[1]["text"] == "D  A"
    assert lines[2]["text"] == "A casa"
    assert lines[4]["text"] == "E|--0--|"


def test_structure_sheet_classifies_before_transposing() -> None:
    # C transposed by -3 becomes A, which alone would look like a lyric word.
    lines = structure_sheet("G\nC\nletra", -3)
    assert lines[1]["type"] == "chords"


def test_suggest_next_chords_ranks_by_unlocked_songs() -> None:
    songs = [
        {"artist": "X", "track_name": "Um", "chord_frequencies": {"C": 10, "G": 10, "F": 10}},
        {"artist": "X", "track_name": "Dois", "chord_frequencies": {"C": 10, "F": 10}},
        {"artist": "X", "track_name": "Tres", "chord_frequencies": {"C": 10, "Bm": 10}},
        {"artist": "X", "track_name": "Facil", "chord_frequencies": {"C": 10, "G": 10}},
    ]
    result = suggest_next_chords(songs, ["C", "G"], True)
    assert result[0]["chord"] == "F"
    assert result[0]["is_barre"] is True
    assert result[0]["songs_unlocked_easy"] == 2
    assert result[0]["songs_improved"] == 2
    assert [e["track_name"] for e in result[0]["examples"]] == ["Um", "Dois"]
    assert result[1]["chord"] == "Bm"


def test_written_key_follows_the_chords():
    from src.core.analyzer import written_key

    # Cifra Club states the recording key (half step down) while the chords are in E.
    assert written_key("Eb", {"E": 10, "C#m": 6, "G#m": 5, "A": 4, "B": 4, "G#": 1}) == "E"
    assert written_key("Em", {"Em": 10, "C": 8, "D": 8}) == "Em"
    assert written_key("Am", {"Am": 5, "G": 3, "F": 3, "E": 2}) == "Am"
    assert written_key(None, {"C": 1}) is None
    assert written_key("C", {}) == "C"


def test_preferred_key_plan():
    from src.core.analyzer import preferred_key_plan

    assert preferred_key_plan("D", "G") == {"key": "G", "semitones": 5, "capo": 7}
    assert preferred_key_plan("C", "G") == {"key": "G", "semitones": -5, "capo": 5}
    assert preferred_key_plan("F#m", "G") == {"key": "Em", "semitones": -2, "capo": 2}
    assert preferred_key_plan("E", "G") == {"key": "G", "semitones": 3, "capo": None}
    assert preferred_key_plan("G", "G") == {"key": "G", "semitones": 0, "capo": None}
    assert preferred_key_plan(None, "G") is None
