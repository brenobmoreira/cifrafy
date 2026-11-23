"""Guitar Difficulty Analyzer & Smart Capo / Transposition Engine.

Implements multi-dimensional difficulty analysis:
1. Chord Shape Taxonomy (Open Basic vs Extended vs Barre vs Complex/Dissonant)
2. Weighted Frequency Analysis (distinguishing core chords from rare 'intruder' passing chords)
3. Harmonic Change Density (chords per lyric line)
4. User Known Chords Coverage (both unique % and time-weighted %)
5. Automatic Capo / Transposition finder to eliminate barre chords
"""

from __future__ import annotations

import re
from typing import Any

from src.config import settings

# Chromatic scale in sharps and mapping from flat equivalents
CHROMATIC_SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_TO_INDEX: dict[str, int] = {
    "C": 0,
    "B#": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "Fb": 4,
    "F": 5,
    "E#": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
    "Cb": 11,
}

# Roots that ALWAYS require a barre chord in standard tuning (except specific open voicings like F7M)
BARRE_ROOT_INDICES = {
    1,   # C# / Db
    3,   # D# / Eb
    5,   # F
    6,   # F# / Gb
    8,   # G# / Ab
    10,  # A# / Bb
    11,  # B
}

# Open roots (C, D, E, G, A) that become barre chords when minor (Cm, Gm)
BARRE_MINOR_ROOT_INDICES = {
    0,  # Cm
    7,  # Gm
}

# Chords with barre roots that actually have common easy non-barre open shapes
NON_BARRE_EXCEPTIONS = {
    "F7M",
    "Fmaj7",
    "F7M(9)",
    "B7",  # Open B7 on 2nd fret (x21202) is frequently played without a barre
}

CHORD_REGEX = re.compile(
    r"^([A-G][#b]?)(m(?!aj)|min|dim|aug|°|\+)?([0-9Mmajaddsus#b()\-\+]*)(?:/([A-G][#b]?))?$"
)


def parse_chord(chord: str) -> dict[str, Any]:
    """Parse a chord string into root, minor flag, extension, bass note, and barre status."""
    cleaned = chord.strip()
    match = CHORD_REGEX.match(cleaned)
    if not match:
        return {
            "raw": cleaned,
            "root": cleaned,
            "root_idx": -1,
            "is_minor": False,
            "extension": "",
            "bass": None,
            "is_barre": False,
            "complexity_weight": 2.0,
        }

    root, quality, ext, bass = match.groups()
    root_idx = NOTE_TO_INDEX.get(root, -1)
    is_minor = quality in ("m", "min")
    is_dim_aug = quality in ("dim", "aug", "°", "+") or "dim" in (ext or "") or "b5" in (ext or "")
    ext = ext or ""

    # Normalize base chord name without slash bass for exception checking
    base_chord = f"{root}{quality or ''}{ext}"
    if base_chord in NON_BARRE_EXCEPTIONS:
        is_barre = False
    elif root_idx in BARRE_ROOT_INDICES:
        is_barre = True
    elif is_minor and root_idx in BARRE_MINOR_ROOT_INDICES:
        is_barre = True
    else:
        is_barre = False

    # Calculate ergonomic complexity weight (1.0 = basic open, 4.5 = complex jazz/barre)
    if is_dim_aug or any(x in ext for x in ("b9", "#9", "#11", "b13")):
        weight = 4.5
    elif is_barre:
        weight = 3.2 if (ext or bass) else 3.0
    elif ext or bass:
        weight = 1.4
    else:
        weight = 1.0

    return {
        "raw": cleaned,
        "root": root,
        "root_idx": root_idx,
        "quality": quality or "",
        "is_minor": is_minor,
        "extension": ext,
        "bass": bass,
        "is_barre": is_barre,
        "complexity_weight": weight,
    }


def is_barre_chord(chord: str) -> bool:
    """Return True if the chord requires a barre in standard guitar tuning."""
    return bool(parse_chord(chord)["is_barre"])


def transpose_chord(chord: str, semitones: int) -> str:
    """Transpose a single chord by `semitones` (positive or negative)."""
    if semitones % 12 == 0:
        return chord
    parsed = parse_chord(chord)
    if parsed["root_idx"] == -1:
        return chord

    new_root_idx = (parsed["root_idx"] + semitones) % 12
    new_root = CHROMATIC_SHARPS[new_root_idx]
    result = f"{new_root}{parsed['quality']}{parsed['extension']}"

    if parsed["bass"] and parsed["bass"] in NOTE_TO_INDEX:
        new_bass_idx = (NOTE_TO_INDEX[parsed["bass"]] + semitones) % 12
        result += f"/{CHROMATIC_SHARPS[new_bass_idx]}"

    return result


def find_best_capo(
    chord_frequencies: dict[str, int],
    known_chords: list[str] | None = None,
) -> dict[str, Any]:
    """Find the optimal capo fret (1..11) that minimizes barre chords and maximizes known chords.

    Note: Placing a capo on fret `N` means playing chord shapes transposed by `-N` semitones!
    """
    if not chord_frequencies:
        return {"capo_fret": 0, "transposed_chords": [], "barre_count": 0, "weighted_barre_pct": 0.0}

    known_set = {c.strip() for c in (known_chords or settings.DEFAULT_KNOWN_CHORDS)}
    total_events = max(sum(chord_frequencies.values()), 1)

    best_option: dict[str, Any] | None = None

    for capo_fret in range(0, 12):
        semitone_shift = -capo_fret
        transposed_freqs: dict[str, int] = {}
        for ch, count in chord_frequencies.items():
            t_ch = transpose_chord(ch, semitone_shift)
            transposed_freqs[t_ch] = transposed_freqs.get(t_ch, 0) + count

        barre_chords = [ch for ch in transposed_freqs if is_barre_chord(ch)]
        barre_events = sum(transposed_freqs[ch] for ch in barre_chords)
        known_events = sum(
            count for ch, count in transposed_freqs.items() if ch in known_set or parse_chord(ch)["root"] + parse_chord(ch)["quality"] in known_set
        )

        option = {
            "capo_fret": capo_fret,
            "semitone_shift": semitone_shift,
            "transposed_chords": list(transposed_freqs.keys()),
            "barre_chords": barre_chords,
            "barre_count": len(barre_chords),
            "weighted_barre_pct": round((barre_events / total_events) * 100, 1),
            "weighted_known_pct": round((known_events / total_events) * 100, 1),
            # Prefer fewer barre events, higher known coverage, and lower capo fret
            "_rank_key": (
                barre_events,
                len(barre_chords),
                -known_events,
                capo_fret if capo_fret <= 7 else capo_fret + 10,
            ),
        }
        if best_option is None or option["_rank_key"] < best_option["_rank_key"]:
            best_option = option

    assert best_option is not None
    best_option.pop("_rank_key", None)
    return best_option


def evaluate_song_difficulty(
    unique_chords: list[str],
    known_chords: list[str] | None = None,
    avoid_barre: bool = True,
    chord_frequencies: dict[str, int] | None = None,
    avg_changes_per_line: float = 2.0,
    has_tabs: bool = False,
) -> dict[str, Any]:
    """Evaluate song difficulty with both spec-compliant fields and rich weighted metrics."""
    effective_known = [
        c.strip() for c in (known_chords if known_chords is not None else settings.DEFAULT_KNOWN_CHORDS) if c.strip()
    ]
    known_set = set(effective_known)

    # Build frequency map (if only unique_chords was passed, assume uniform frequency = 1)
    if chord_frequencies:
        freqs = {ch: count for ch, count in chord_frequencies.items() if count > 0}
    else:
        freqs = {ch: 1 for ch in unique_chords if ch.strip()}

    all_chords = list(freqs.keys())
    if not all_chords:
        return {
            "level": "Desconhecido",
            "has_barre": False,
            "barre_chords": [],
            "rare_barre_chords": [],
            "unknown_chords": [],
            "known_match_pct": 0.0,
            "weighted_known_pct": 0.0,
            "score": 0,
            "capo_suggestion": None,
        }

    total_events = max(sum(freqs.values()), 1)
    has_real_frequencies = total_events > len(all_chords)

    barre_chords: list[str] = []
    core_barre_chords: list[str] = []
    rare_barre_chords: list[str] = []
    unknown_chords: list[str] = []
    core_unknown_chords: list[str] = []

    known_events = 0
    barre_events = 0
    weighted_complexity_sum = 0.0

    for ch in all_chords:
        count = freqs[ch]
        share = count / total_events
        parsed = parse_chord(ch)
        weighted_complexity_sum += parsed["complexity_weight"] * count

        # Check if chord is known (exact match or simple major/minor root match for basic extensions like Am7 -> Am)
        base_triad = f"{parsed['root']}{parsed['quality']}"
        is_known = (ch in known_set) or (
            not parsed["is_barre"]
            and parsed["complexity_weight"] <= 1.4
            and base_triad in known_set
        )

        if is_known:
            known_events += count
        else:
            unknown_chords.append(ch)
            if not has_real_frequencies or share >= 0.04 or count > 2:
                core_unknown_chords.append(ch)

        if parsed["is_barre"]:
            barre_chords.append(ch)
            barre_events += count
            # A barre chord appearing <= 2 times and < 4% of the song is an "intruder/passing" chord
            if has_real_frequencies and share < 0.04 and count <= 2:
                rare_barre_chords.append(ch)
            else:
                core_barre_chords.append(ch)

    has_barre = len(barre_chords) > 0
    unique_match_pct = round(((len(all_chords) - len(unknown_chords)) / len(all_chords)) * 100, 1)
    weighted_known_pct = round((known_events / total_events) * 100, 1)
    weighted_barre_pct = round((barre_events / total_events) * 100, 1)

    # Calculate 0-100 Ease Score
    # 1. Coverage component (0-50 pts)
    coverage_pts = 0.50 * weighted_known_pct

    # 2. Barre component (0-25 pts)
    if not barre_chords:
        barre_pts = 25.0
    elif not core_barre_chords:
        # Only rare passing barre chord(s)
        barre_pts = 20.0
    else:
        penalty_multiplier = 1.0 if avoid_barre else 0.5
        barre_pts = max(0.0, 25.0 - (len(core_barre_chords) * 11.0 + weighted_barre_pct * 0.3) * penalty_multiplier)

    # 3. Harmonic density component (0-15 pts)
    if avg_changes_per_line <= 2.5:
        density_pts = 15.0
    elif avg_changes_per_line <= 4.0:
        density_pts = 10.0
    else:
        density_pts = max(2.0, 15.0 - (avg_changes_per_line - 2.5) * 4.0)

    # 4. Chord count & complexity simplicity bonus (0-10 pts)
    avg_complexity = weighted_complexity_sum / total_events
    if len(all_chords) <= 4 and avg_complexity <= 1.3:
        simplicity_pts = 10.0
    elif len(all_chords) <= 6 and avg_complexity <= 1.8:
        simplicity_pts = 7.0
    else:
        simplicity_pts = max(0.0, 10.0 - (len(all_chords) - 4) * 1.5)

    score = int(round(min(100.0, max(0.0, coverage_pts + barre_pts + density_pts + simplicity_pts))))

    # Classification Rules (combining spec rules + weighted intelligence)
    effective_barres = core_barre_chords if has_real_frequencies else barre_chords
    effective_unknowns = core_unknown_chords if has_real_frequencies else unknown_chords

    if (
        len(effective_barres) == 0
        and avg_changes_per_line <= 3.8
        and (len(all_chords) <= 4 or len(effective_unknowns) == 0 or weighted_known_pct >= 95.0)
    ):
        level = "Fácil"
    elif (
        (len(effective_barres) <= 1 and len(effective_unknowns) <= 1 and avg_changes_per_line <= 4.2)
        or (len(effective_barres) == 0 and len(effective_unknowns) <= 2 and score >= 60)
    ):
        level = "Médio"
    else:
        level = "Difícil"

    # Find if a Capo makes a Medium/Hard song Easy!
    capo_suggestion = None
    if has_barre:
        best_capo = find_best_capo(freqs, effective_known)
        if best_capo["capo_fret"] > 0 and best_capo["barre_count"] < len(barre_chords):
            capo_suggestion = {
                "capo_fret": best_capo["capo_fret"],
                "transposed_chords": best_capo["transposed_chords"],
                "remaining_barres": best_capo["barre_chords"],
                "message": (
                    f"Com Capotraste na {best_capo['capo_fret']}ª casa, "
                    f"você toca usando { ', '.join(best_capo['transposed_chords'][:6]) } "
                    f"({best_capo['barre_count']} pestanas)!"
                ),
            }

    return {
        "level": level,
        "has_barre": has_barre,
        "barre_chords": barre_chords,
        "core_barre_chords": core_barre_chords,
        "rare_barre_chords": rare_barre_chords,
        "unknown_chords": unknown_chords,
        "known_match_pct": unique_match_pct,
        "weighted_known_pct": weighted_known_pct,
        "weighted_barre_pct": weighted_barre_pct,
        "avg_changes_per_line": round(avg_changes_per_line, 2),
        "has_tabs": has_tabs,
        "score": score,
        "capo_suggestion": capo_suggestion,
    }
