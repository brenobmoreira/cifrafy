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
from typing import Any, Callable

from src.config import settings

# Chromatic scale in sharps and mapping from flat equivalents
CHROMATIC_SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
CHROMATIC_FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
# Usual guitar spelling when the source root is natural or sharp
MAJOR_SPELLING = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MINOR_SPELLING = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]
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
    "F7+",
    "Fmaj7",
    "F7M(9)",
    "B7",  # Open B7 on 2nd fret (x21202) is frequently played without a barre
}

CHORD_REGEX = re.compile(
    r"^([A-G][#b]?)"
    r"(min|m(?!aj)|dim|aug|°|º|\+)?"
    r"((?:maj|add|sus|dim|aug|M|m|[0-9#b+\-°º]|\([^)]*\)|/[#b]?\d+)*)"
    r"(?:/([A-G][#b]?))?$"
)

RARE_CHORD_SHARE = 0.04
MAX_EASY_CAPO_FRET = 7

# Valid chords that are also common words in lyrics
AMBIGUOUS_LYRIC_TOKENS = {"A", "E", "Em", "Am"}
TAB_LINE = re.compile(r"^\s*[eBGDAE]\|")
SHEET_TOKEN = re.compile(r"\[[^\]]*\]|\S+")
SECTION_LINE = re.compile(r"^(?:\[[^\]]*\]|\S+:)$")
NEUTRAL_SHEET_TOKEN = re.compile(r"^(?:\|+|-+|%|/+|\.+|\(?\d+x\)?|\(?x\d+\)?|\(?n\.c\.?\)?)$", re.IGNORECASE)


def parse_chord(chord: str) -> dict[str, Any]:
    """Parse a chord string; tokens that are not chords get `root_idx == -1`."""
    cleaned = chord.strip()
    match = CHORD_REGEX.match(cleaned)
    if not match:
        return {
            "raw": cleaned,
            "root": cleaned,
            "root_idx": -1,
            "quality": "",
            "is_minor": False,
            "extension": "",
            "bass": None,
            "is_barre": False,
            "complexity_weight": 0.0,
        }

    root, quality, ext, bass = match.groups()
    root_idx = NOTE_TO_INDEX.get(root, -1)
    is_minor = quality in ("m", "min")
    is_dim_aug = quality in ("dim", "aug", "°", "º", "+") or any(mark in (ext or "") for mark in ("dim", "b5", "5-", "-5"))
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


def _chord_key(parsed: dict[str, Any]) -> tuple[int, str, str, int | None]:
    """Enharmonic identity of a chord, so that A#m7 and Bbm7 compare equal."""
    quality = {"min": "m", "º": "°"}.get(parsed["quality"], parsed["quality"])
    bass = NOTE_TO_INDEX.get(parsed["bass"]) if parsed["bass"] else None
    return parsed["root_idx"], quality, parsed["extension"], bass


def _known_keys(known_chords: list[str]) -> set[tuple[int, str, str, int | None]]:
    parsed = (parse_chord(c) for c in known_chords)
    return {_chord_key(p) for p in parsed if p["root_idx"] != -1}


def _is_known(parsed: dict[str, Any], known_keys: set[tuple[int, str, str, int | None]]) -> bool:
    """Exact match, or a simple open extension of a known triad (Am7 -> Am)."""
    key = _chord_key(parsed)
    if key in known_keys:
        return True
    triad = (key[0], key[1], "", None)
    return not parsed["is_barre"] and parsed["complexity_weight"] <= 1.4 and triad in known_keys


def transpose_chord(chord: str, semitones: int) -> str:
    """Transpose a single chord by `semitones` (positive or negative)."""
    if semitones % 12 == 0:
        return chord
    parsed = parse_chord(chord)
    if parsed["root_idx"] == -1:
        return chord

    new_root_idx = (parsed["root_idx"] + semitones) % 12
    if "b" in parsed["root"]:
        spelling = CHROMATIC_FLATS
    elif parsed["is_minor"] or parsed["quality"] in ("dim", "°", "º"):
        spelling = MINOR_SPELLING
    else:
        spelling = MAJOR_SPELLING
    new_root = spelling[new_root_idx]
    result = f"{new_root}{parsed['quality']}{parsed['extension']}"

    if parsed["bass"] and parsed["bass"] in NOTE_TO_INDEX:
        new_bass_idx = (NOTE_TO_INDEX[parsed["bass"]] + semitones) % 12
        use_flats = "b" in parsed["bass"] or "b" in new_root or new_root == "F"
        result += f"/{(CHROMATIC_FLATS if use_flats else CHROMATIC_SHARPS)[new_bass_idx]}"

    return result


def _split_sheet_token(token: str) -> tuple[str, str, str]:
    """Separate a token from surrounding notation: `(G` -> `(`, `G`, ``."""
    core = token
    lead = ""
    trail = ""
    while core and core[0] in "(|":
        lead += core[0]
        core = core[1:]
    while core and (core[-1] in "|,;." or (core[-1] == ")" and core.count(")") > core.count("("))):
        trail = core[-1] + trail
        core = core[:-1]
    return lead, core, trail


def _is_chord_token(token: str) -> bool:
    return bool(token) and parse_chord(token)["root_idx"] != -1


def _classify_sheet_line(line: str) -> tuple[bool, bool]:
    """Return (only chords and notation, only chords that could be lyric words)."""
    if TAB_LINE.match(line):
        return False, False

    chords: list[str] = []
    has_label = False
    for position, match in enumerate(SHEET_TOKEN.finditer(line)):
        token = match.group()
        if token.startswith("[") and token.endswith("]"):
            has_label = True
            continue
        if position == 0 and token.endswith(":") and len(token) > 1:
            has_label = True
            continue
        if NEUTRAL_SHEET_TOKEN.match(token):
            continue
        _, core, _ = _split_sheet_token(token)
        if not _is_chord_token(core):
            return False, False
        chords.append(core)

    if not chords:
        return False, False
    return True, not has_label and all(c in AMBIGUOUS_LYRIC_TOKENS for c in chords)


def map_chord_line(line: str, convert: Callable[[str], str]) -> str:
    """Replace chord tokens, absorbing length changes in the following spaces."""
    output: list[str] = []
    position = 0
    extra_width = 0
    for match in SHEET_TOKEN.finditer(line):
        gap = line[position : match.start()]
        if extra_width > 0:
            removable = min(extra_width, max(len(gap) - 1, 0))
            gap = gap[removable:]
            extra_width -= removable
        elif extra_width < 0:
            gap = " " * -extra_width + gap
            extra_width = 0

        token = match.group()
        new_token = token
        if not (token.startswith("[") and token.endswith("]")):
            lead, core, trail = _split_sheet_token(token)
            if _is_chord_token(core):
                new_token = f"{lead}{convert(core)}{trail}"
        extra_width += len(new_token) - len(token)
        output.extend((gap, new_token))
        position = match.end()
    output.append(line[position:])
    return "".join(output)


def _transpose_chord_line(line: str, semitones: int) -> str:
    return map_chord_line(line, lambda chord: transpose_chord(chord, semitones))


def _sheet_line_types(lines: list[str]) -> list[str]:
    """Classify each line as chords, lyric, tab, section or blank.

    A line holding only words like `A` or `E` right after a chord line is lyric,
    because chord lines are followed by the lyric they belong to.
    """
    types: list[str] = []
    after_chords_or_ambiguous_lyric = False
    for line in lines:
        is_chord_line, is_ambiguous = _classify_sheet_line(line)
        if is_chord_line and is_ambiguous and after_chords_or_ambiguous_lyric:
            types.append("lyric")
            continue
        after_chords_or_ambiguous_lyric = is_chord_line
        stripped = line.strip()
        if is_chord_line:
            types.append("chords")
        elif not stripped:
            types.append("blank")
        elif TAB_LINE.match(line):
            types.append("tab")
        elif SECTION_LINE.match(stripped):
            types.append("section")
        else:
            types.append("lyric")
    return types


def transpose_sheet(text: str, semitones: int) -> str:
    """Transpose only the chord lines of a sheet, leaving lyrics and tabs untouched."""
    if semitones % 12 == 0 or not text:
        return text
    return "\n".join(line["text"] for line in structure_sheet(text, semitones))


def structure_sheet(text: str, semitones: int = 0) -> list[dict[str, str]]:
    """Split a sheet into typed lines, already transposed.

    Lines are classified on the original text, since a transposed chord can look like a lyric word.
    """
    if not text:
        return []
    lines = text.split("\n")
    transpose = semitones % 12 != 0
    return [
        {
            "type": line_type,
            "text": _transpose_chord_line(line, semitones) if transpose and line_type == "chords" else line,
        }
        for line, line_type in zip(lines, _sheet_line_types(lines))
    ]


def extract_chord_sequence(text: str) -> list[str]:
    """Return the chords of a sheet in playing order, ignoring lyrics, tabs and notation."""
    if not text:
        return []
    lines = text.split("\n")
    sequence: list[str] = []
    for line, line_type in zip(lines, _sheet_line_types(lines)):
        if line_type != "chords":
            continue
        for position, match in enumerate(SHEET_TOKEN.finditer(line)):
            token = match.group()
            if token.startswith("[") or (position == 0 and token.endswith(":")):
                continue
            if NEUTRAL_SHEET_TOKEN.match(token):
                continue
            _, core, _ = _split_sheet_token(token)
            if _is_chord_token(core):
                sequence.append(core)
    return sequence


def canonical_chord(chord: str) -> str | None:
    """Single spelling for enharmonic chords (Bbm and A#m), or None if it is not a chord."""
    parsed = parse_chord(chord)
    if parsed["root_idx"] == -1:
        return None
    root, quality, extension, bass = _chord_key(parsed)
    slash = f"/{CHROMATIC_SHARPS[bass]}" if bass is not None else ""
    return f"{CHROMATIC_SHARPS[root]}{quality}{extension}{slash}"


def chord_change_key(first: str, second: str) -> tuple[str, str] | None:
    """Direction-free identity of a chord change, or None if it is not a real change."""
    a, b = canonical_chord(first), canonical_chord(second)
    if a is None or b is None or a == b:
        return None
    return (a, b) if a < b else (b, a)


def is_known_chord(chord: str, known_chords: list[str]) -> bool:
    return _is_known(parse_chord(chord), _known_keys(known_chords))


def count_easy_songs(
    songs: list[dict[str, Any]],
    known_chords: list[str],
    avoid_barre: bool,
    learned_chords: list[str] | None = None,
) -> int:
    learned = _known_keys(learned_chords or [])
    known = _known_keys(known_chords) | learned
    return sum(
        _difficulty_level(song.get("chord_frequencies") or {}, known, avoid_barre, learned) == "Fácil"
        for song in songs
    )


def find_best_capo(
    chord_frequencies: dict[str, int],
    known_chords: list[str] | None = None,
) -> dict[str, Any]:
    """Find the optimal capo fret (1..11) that minimizes barre chords and maximizes known chords.

    Note: Placing a capo on fret `N` means playing chord shapes transposed by `-N` semitones!
    """
    chord_frequencies = {
        ch: count for ch, count in chord_frequencies.items() if parse_chord(ch)["root_idx"] != -1
    }
    if not chord_frequencies:
        return {
            "capo_fret": 0,
            "semitone_shift": 0,
            "transposed_chords": [],
            "barre_chords": [],
            "barre_count": 0,
            "weighted_barre_pct": 0.0,
            "weighted_known_pct": 0.0,
        }

    known_keys = _known_keys(known_chords if known_chords is not None else settings.DEFAULT_KNOWN_CHORDS)
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
            count for ch, count in transposed_freqs.items() if _is_known(parse_chord(ch), known_keys)
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


def _is_rare(count: int, total_events: int, has_real_frequencies: bool) -> bool:
    """A chord appearing <= 2 times and < 4% of the song is an intruder/passing chord."""
    return has_real_frequencies and count / total_events < RARE_CHORD_SHARE and count <= 2


def _difficulty_level(
    freqs: dict[str, int],
    known_keys: set[tuple[int, str, str, int | None]],
    avoid_barre: bool,
    learned_keys: set[tuple[int, str, str, int | None]] | None = None,
) -> str:
    """Fácil: no relevant barre and (<= 4 chords or all known). Médio: exactly 1 obstacle.

    A known barre is an obstacle only for who avoids barres. `learned_keys` are chords the user
    would learn on purpose, so they stop being obstacles even if they are barres.
    """
    learned_keys = learned_keys or set()
    playable = {ch: c for ch, c in freqs.items() if c > 0 and parse_chord(ch)["root_idx"] != -1}
    if not playable:
        return "Desconhecido"

    total_events = sum(playable.values())
    has_real_frequencies = total_events > len(playable)
    relevant = [ch for ch, c in playable.items() if not _is_rare(c, total_events, has_real_frequencies)]
    relevant = relevant or list(playable)

    barre_obstacles = 0
    new_chords = 0
    for ch in relevant:
        parsed = parse_chord(ch)
        is_known = _is_known(parsed, known_keys)
        if parsed["is_barre"]:
            if not is_known or (avoid_barre and _chord_key(parsed) not in learned_keys):
                barre_obstacles += 1
        elif not is_known:
            new_chords += 1

    if barre_obstacles == 0 and (len(relevant) <= 4 or new_chords == 0):
        return "Fácil"
    if barre_obstacles + new_chords == 1:
        return "Médio"
    return "Difícil"


def _transpose_frequencies(freqs: dict[str, int], semitones: int) -> dict[str, int]:
    transposed: dict[str, int] = {}
    for ch, count in freqs.items():
        new_chord = transpose_chord(ch, semitones)
        transposed[new_chord] = transposed.get(new_chord, 0) + count
    return transposed


def _find_easy_capo(
    freqs: dict[str, int],
    known_keys: set[tuple[int, str, str, int | None]],
    avoid_barre: bool,
) -> tuple[int, dict[str, int]] | None:
    """Return the lowest capo fret (1 to 7) whose shapes make the song Fácil."""
    for fret in range(1, MAX_EASY_CAPO_FRET + 1):
        shapes = _transpose_frequencies(freqs, -fret)
        if _difficulty_level(shapes, known_keys, avoid_barre) == "Fácil":
            return fret, shapes
    return None


def _capo_message(fret: int, shapes: list[str], barre_count: int) -> str:
    if barre_count == 0:
        barres = "sem pestanas"
    elif barre_count == 1:
        barres = "1 pestana"
    else:
        barres = f"{barre_count} pestanas"
    return f"Com capotraste na {fret}ª casa, você toca usando {', '.join(shapes[:6])} ({barres})"


def suggest_next_chords(
    songs: list[dict[str, Any]],
    known_chords: list[str],
    avoid_barre: bool,
    limit: int = 5,
    learned_chords: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Rank the chords whose learning makes the most songs easier.

    `learned_chords` simulates chords already learned on purpose, as in a learning trail.
    """
    learned_keys = _known_keys(learned_chords or [])
    known_keys = _known_keys(known_chords) | learned_keys
    rank = {"Fácil": 0, "Médio": 1, "Difícil": 2}
    pending: list[tuple[dict[str, Any], int]] = []
    candidates: dict[tuple[int, str, str, int | None], str] = {}

    for song in songs:
        freqs = song.get("chord_frequencies") or {}
        level = _difficulty_level(freqs, known_keys, avoid_barre, learned_keys)
        if level not in ("Médio", "Difícil"):
            continue
        pending.append((song, rank[level]))
        for ch in freqs:
            parsed = parse_chord(ch)
            if parsed["root_idx"] != -1 and not _is_known(parsed, known_keys):
                candidates.setdefault(_chord_key(parsed), ch)

    suggestions: list[dict[str, Any]] = []
    for key, chord in candidates.items():
        improved: list[dict[str, Any]] = []
        unlocked: list[dict[str, Any]] = []
        for song, before in pending:
            after = _difficulty_level(
                song["chord_frequencies"], known_keys | {key}, avoid_barre, learned_keys | {key}
            )
            if rank[after] < before:
                improved.append(song)
                if after == "Fácil":
                    unlocked.append(song)
        if not improved:
            continue
        unlocked_ids = {id(song) for song in unlocked}
        examples = unlocked + [song for song in improved if id(song) not in unlocked_ids]
        suggestions.append(
            {
                "chord": chord,
                "is_barre": is_barre_chord(chord),
                "songs_improved": len(improved),
                "songs_unlocked_easy": len(unlocked),
                "examples": [
                    {
                        "song_id": song.get("id"),
                        "artist": song.get("artist"),
                        "track_name": song.get("track_name") or song.get("name"),
                    }
                    for song in examples[:3]
                ],
            }
        )

    suggestions.sort(key=lambda s: (-s["songs_unlocked_easy"], -s["songs_improved"], s["chord"]))
    return suggestions[:limit]


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
    known_keys = _known_keys(effective_known)

    # Build frequency map (if only unique_chords was passed, assume uniform frequency = 1)
    if chord_frequencies:
        freqs = {ch: count for ch, count in chord_frequencies.items() if count > 0}
    else:
        freqs = {ch: 1 for ch in unique_chords if ch.strip()}
    freqs = {ch: count for ch, count in freqs.items() if parse_chord(ch)["root_idx"] != -1}

    all_chords = list(freqs.keys())
    if not all_chords:
        return {
            "level": "Desconhecido",
            "has_barre": False,
            "barre_chords": [],
            "core_barre_chords": [],
            "rare_barre_chords": [],
            "known_barre_chords": [],
            "unknown_chords": [],
            "known_match_pct": 0.0,
            "weighted_known_pct": 0.0,
            "weighted_barre_pct": 0.0,
            "avg_changes_per_line": round(avg_changes_per_line, 2),
            "has_tabs": has_tabs,
            "score": 0,
            "capo_suggestion": None,
            "easy_with_capo": False,
        }

    total_events = max(sum(freqs.values()), 1)
    has_real_frequencies = total_events > len(all_chords)

    barre_chords: list[str] = []
    core_barre_chords: list[str] = []
    rare_barre_chords: list[str] = []
    unknown_chords: list[str] = []
    known_barre_chords: list[str] = []
    hard_barre_chords: list[str] = []  # frequent barres the user does not know yet
    known_core_barres: list[str] = []

    known_events = 0
    barre_events = 0
    hard_barre_events = 0
    weighted_complexity_sum = 0.0

    for ch in all_chords:
        count = freqs[ch]
        parsed = parse_chord(ch)
        weighted_complexity_sum += parsed["complexity_weight"] * count

        is_known = _is_known(parsed, known_keys)

        if is_known:
            known_events += count
        else:
            unknown_chords.append(ch)

        if parsed["is_barre"]:
            barre_chords.append(ch)
            barre_events += count
            if is_known:
                known_barre_chords.append(ch)
            if _is_rare(count, total_events, has_real_frequencies):
                rare_barre_chords.append(ch)
            else:
                core_barre_chords.append(ch)
                if is_known:
                    known_core_barres.append(ch)
                else:
                    hard_barre_chords.append(ch)
                    hard_barre_events += count

    has_barre = len(barre_chords) > 0
    unique_match_pct = round(((len(all_chords) - len(unknown_chords)) / len(all_chords)) * 100, 1)
    weighted_known_pct = round((known_events / total_events) * 100, 1)
    weighted_barre_pct = round((barre_events / total_events) * 100, 1)

    # Calculate 0-100 Ease Score
    # 1. Coverage component (0-50 pts)
    coverage_pts = 0.50 * weighted_known_pct

    # 2. Barre component (0-25 pts)
    hard_barre_pct = (hard_barre_events / total_events) * 100
    hard_penalty = (len(hard_barre_chords) * 11.0 + hard_barre_pct * 0.3) * (1.0 if avoid_barre else 0.5)
    known_penalty = len(known_core_barres) * 3.0 if avoid_barre else 0.0
    has_rare_unknown = any(ch not in known_barre_chords for ch in rare_barre_chords)
    rare_penalty = 5.0 if has_rare_unknown and not core_barre_chords else 0.0
    barre_pts = max(0.0, 25.0 - hard_penalty - known_penalty - rare_penalty)

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

    level = _difficulty_level(freqs, known_keys, avoid_barre)

    capo_suggestion = None
    easy_capo = _find_easy_capo(freqs, known_keys, avoid_barre) if level != "Fácil" else None
    if easy_capo:
        fret, shapes = easy_capo
        shape_names = list(shapes)
        remaining = [ch for ch in shape_names if is_barre_chord(ch)]
        capo_suggestion = {
            "capo_fret": fret,
            "transposed_chords": shape_names,
            "remaining_barres": remaining,
            "message": _capo_message(fret, shape_names, len(remaining)),
            "makes_easy": True,
        }
    elif has_barre:
        best_capo = find_best_capo(freqs, effective_known)
        if best_capo["capo_fret"] > 0 and best_capo["barre_count"] < len(barre_chords):
            capo_suggestion = {
                "capo_fret": best_capo["capo_fret"],
                "transposed_chords": best_capo["transposed_chords"],
                "remaining_barres": best_capo["barre_chords"],
                "message": _capo_message(
                    best_capo["capo_fret"], best_capo["transposed_chords"], best_capo["barre_count"]
                ),
                "makes_easy": False,
            }

    return {
        "level": level,
        "has_barre": has_barre,
        "barre_chords": barre_chords,
        "core_barre_chords": core_barre_chords,
        "rare_barre_chords": rare_barre_chords,
        "known_barre_chords": known_barre_chords,
        "unknown_chords": unknown_chords,
        "known_match_pct": unique_match_pct,
        "weighted_known_pct": weighted_known_pct,
        "weighted_barre_pct": weighted_barre_pct,
        "avg_changes_per_line": round(avg_changes_per_line, 2),
        "has_tabs": has_tabs,
        "score": score,
        "capo_suggestion": capo_suggestion,
        "easy_with_capo": bool(easy_capo),
    }
