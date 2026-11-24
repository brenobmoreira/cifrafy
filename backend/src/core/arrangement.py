"""Adapted arrangement: capo plus conservative chord substitutions. Pure and deterministic.

Substitutions keep the root and the major or minor quality, so the song keeps its harmony.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.analyzer import (
    MAX_EASY_CAPO_FRET,
    _chord_key,
    _difficulty_level,
    _is_known,
    _known_keys,
    evaluate_song_difficulty,
    map_chord_line,
    parse_chord,
    structure_sheet,
    transpose_chord,
)

LEVEL_RANK = {"Fácil": 0, "Médio": 1, "Difícil": 2, "Desconhecido": 3}
MAJOR_SEVENTH = re.compile(r"^(7M|maj7|M7|7\+)")
SUSPENDED = re.compile(r"sus|(?:^|[(/])[24](?:[)/]|$)")
ALTERED = re.compile(r"b5|#5|5-|-5|5\+|\+5|dim|aug|[°º]")
PLAYABLE_EFFORT = 1.0
# Without a level gain, only small edits are worth moving away from the original.
MIN_FIDELITY_WITHOUT_GAIN = 60.0


def _base_seventh(parsed: dict[str, Any]) -> str:
    """Return the plain seventh of the chord, or an empty string when it has none."""
    ext = parsed["extension"]
    major = MAJOR_SEVENTH.match(ext)
    if major:
        return major.group(1)
    if "7" in ext or (not parsed["is_minor"] and ext.startswith(("9", "11", "13"))):
        return "7"
    if parsed["is_minor"] and ext.startswith(("9", "11", "13")):
        return "7"
    return ""


def _is_preserved_quality(parsed: dict[str, Any]) -> bool:
    """Diminished, half-diminished, augmented and power chords have no faithful simpler form."""
    return (
        parsed["quality"] in ("dim", "aug", "°", "º", "+")
        or bool(ALTERED.search(parsed["extension"]))
        or parsed["extension"] == "5"
    )


def _is_suspended(parsed: dict[str, Any]) -> bool:
    return not parsed["is_minor"] and bool(SUSPENDED.search(parsed["extension"]))


def _is_decorated(parsed: dict[str, Any]) -> bool:
    if _is_preserved_quality(parsed):
        return bool(parsed["bass"])
    return bool(parsed["bass"]) or parsed["extension"] not in ("", _base_seventh(parsed))


def _effort(parsed: dict[str, Any], known_keys: set[Any], avoid_barre: bool) -> float:
    """Lower is easier. Exact known chords cost nothing, unknown barres cost the most."""
    exact = _chord_key(parsed) in known_keys
    decoration = 0.5 if _is_decorated(parsed) and not exact else 0.0
    if parsed["is_barre"]:
        if exact:
            return 1.0 if avoid_barre else 0.0
        return 3.0 + decoration
    if exact:
        return 0.0
    if _is_known(parsed, known_keys):
        return 1.0 + decoration
    return 2.0 + decoration


def _candidates(parsed: dict[str, Any], known_keys: set[Any]) -> list[str]:
    """Simpler spellings of the chord, closest to the original first."""
    root = parsed["root"]
    quality = parsed["quality"]
    without_bass = f"{root}{quality}{parsed['extension']}"

    if _is_preserved_quality(parsed):
        return [without_bass] if parsed["bass"] else []

    seventh = _base_seventh(parsed)
    if _is_suspended(parsed):
        options = [f"{root}{seventh}"] if seventh else []
        options.append(root)
        return [c for c in options if _chord_key(parse_chord(c)) in known_keys]

    options = []
    if parsed["bass"]:
        options.append(without_bass)
    if seventh and parsed["extension"] != seventh:
        options.append(f"{root}{quality}{seventh}")
    if not seventh or parsed["extension"] != seventh or parsed["bass"]:
        options.append(f"{root}{quality}")

    is_plain = not parsed["extension"] or MAJOR_SEVENTH.match(parsed["extension"])
    if root == "F" and not quality and is_plain:
        options.append("Fmaj7")
    if parsed["root_idx"] == 11 and parsed["is_minor"] and not parsed["extension"]:
        minor_seventh = f"{root}{quality}7"
        if _chord_key(parse_chord(minor_seventh)) in known_keys:
            options.append(minor_seventh)
    return list(dict.fromkeys(c for c in options if c != parsed["raw"]))


def simplify_chord(chord: str, known_chords: list[str], avoid_barre: bool = True) -> str:
    """Return the closest easier form of a chord, or the chord itself."""
    return _simplify(chord, _known_keys(known_chords), avoid_barre)


def _simplify(chord: str, known_keys: set[Any], avoid_barre: bool) -> str:
    parsed = parse_chord(chord)
    if parsed["root_idx"] == -1:
        return chord
    current = _effort(parsed, known_keys, avoid_barre)
    if current == 0 or (not parsed["is_barre"] and _is_known(parsed, known_keys) and not _is_decorated(parsed)):
        return chord

    best, best_effort = chord, current
    for option in _candidates(parsed, known_keys):
        candidate = parse_chord(option)
        # Guard: a substitution never changes the root or the major/minor quality.
        if candidate["root_idx"] != parsed["root_idx"] or candidate["is_minor"] != parsed["is_minor"]:
            continue
        effort = _effort(candidate, known_keys, avoid_barre)
        if effort <= PLAYABLE_EFFORT and effort < current:
            return option
        if effort < best_effort:
            best, best_effort = option, effort
    return best


def _remaining_hard(chords: dict[str, int], known_keys: set[Any], avoid_barre: bool) -> list[str]:
    hard: list[str] = []
    for chord in chords:
        parsed = parse_chord(chord)
        exact_barre_ok = parsed["is_barre"] and not avoid_barre and _chord_key(parsed) in known_keys
        if not exact_barre_ok and (parsed["is_barre"] or not _is_known(parsed, known_keys)):
            hard.append(chord)
    return hard


def _option(
    freqs: dict[str, int], fret: int, known_keys: set[Any], avoid_barre: bool, simplify: bool = True
) -> dict[str, Any]:
    final: dict[str, int] = {}
    by_shape: dict[str, str] = {}
    mapping: dict[str, tuple[str, str]] = {}
    preserved = 0
    for chord, count in freqs.items():
        shape = transpose_chord(chord, -fret)
        target = _simplify(shape, known_keys, avoid_barre) if simplify else shape
        by_shape[shape] = target
        mapping[chord] = (shape, target)
        final[target] = final.get(target, 0) + count
        if target == shape:
            preserved += count

    total = sum(freqs.values())
    hard = _remaining_hard(final, known_keys, avoid_barre)
    return {
        "fret": fret,
        "final": final,
        "by_shape": by_shape,
        "mapping": mapping,
        "fidelity": round(preserved / total * 100, 1),
        "level": _difficulty_level(final, known_keys, avoid_barre),
        "hard": hard,
        "effort": sum(_effort(parse_chord(c), known_keys, avoid_barre) * n for c, n in final.items()),
    }


def _substitutions(option: dict[str, Any], freqs: dict[str, int]) -> list[dict[str, Any]]:
    fret = option["fret"]
    items: list[dict[str, Any]] = []
    for chord, (shape, target) in option["mapping"].items():
        if target == chord:
            continue
        if target == shape:
            kind = "capo"
            reason = f"Mesmo som, com a forma de {shape} e capotraste na {fret}ª casa."
        elif parse_chord(shape)["is_barre"] and not parse_chord(target)["is_barre"]:
            kind = "avoid_barre"
            reason = f"{target} evita a pestana de {shape} e mantém a função do acorde."
        else:
            kind = "simplify"
            reason = f"{target} mantém a base de {shape} com uma forma mais simples."
        items.append({"from": chord, "to": target, "occurrences": freqs[chord], "kind": kind, "reason": reason})
    items.sort(key=lambda item: (-item["occurrences"], item["from"]))
    return items


def _note(option: dict[str, Any], substitutions: list[dict[str, Any]]) -> str:
    changed = sum(1 for item in substitutions if item["kind"] != "capo")
    if option.get("no_gain"):
        capo = f" O capotraste na {option['fret']}ª casa deixa as formas mais confortáveis." if option["fret"] else ""
        return f"Não há versão mais fácil sem descaracterizar a harmonia, então os acordes foram mantidos.{capo}"
    if not substitutions:
        return "Esta música já cabe no seu nível, sem nenhuma adaptação."
    parts = []
    if option["fret"]:
        parts.append(f"Capotraste na {option['fret']}ª casa.")
    if changed:
        parts.append(f"{changed} {'acorde simplificado' if changed == 1 else 'acordes simplificados'}.")
    parts.append(f"{option['fidelity']:g}% dos acordes seguem iguais ao original.")
    return " ".join(parts)


def build_arrangement(
    chord_frequencies: dict[str, int],
    raw_sheet: str,
    known_chords: list[str],
    avoid_barre: bool,
    avg_changes_per_line: float = 2.0,
) -> dict[str, Any]:
    """Pick the capo and substitutions that best fit the player, keeping as much as possible."""
    known_keys = _known_keys(known_chords)
    freqs = {c: n for c, n in chord_frequencies.items() if n > 0 and parse_chord(c)["root_idx"] != -1}
    before = evaluate_song_difficulty(list(freqs), known_chords, avoid_barre, freqs, avg_changes_per_line)

    if not freqs:
        return {
            "capo_fret": 0, "substitutions": [], "fidelity_pct": 100.0,
            "level_before": before["level"], "level_after": before["level"],
            "score_before": before["score"], "score_after": before["score"],
            "remaining_hard": [], "chords": [], "sheet_lines": structure_sheet(raw_sheet),
            "note": "Esta cifra não tem acordes para adaptar.",
        }

    if before["level"] == "Fácil":
        option = {
            "fret": 0,
            "final": dict(freqs),
            "by_shape": {c: c for c in freqs},
            "mapping": {c: (c, c) for c in freqs},
            "fidelity": 100.0,
            "level": "Fácil",
            "hard": _remaining_hard(freqs, known_keys, avoid_barre),
        }
    else:
        options = [_option(freqs, fret, known_keys, avoid_barre) for fret in range(MAX_EASY_CAPO_FRET + 1)]
        # Among versions at the best level, keep the one closest to the original.
        option = min(options, key=lambda o: (LEVEL_RANK[o["level"]], -o["fidelity"], o["effort"], o["fret"]))
        if LEVEL_RANK[option["level"]] >= LEVEL_RANK[before["level"]]:
            faithful = [o for o in options if o["fidelity"] >= MIN_FIDELITY_WITHOUT_GAIN]
            if faithful:
                option = min(faithful, key=lambda o: (o["effort"], -o["fidelity"], o["fret"]))
            else:
                capo_only = [
                    _option(freqs, fret, known_keys, avoid_barre, simplify=False)
                    for fret in range(MAX_EASY_CAPO_FRET + 1)
                ]
                option = {**min(capo_only, key=lambda o: (o["effort"], o["fret"])), "no_gain": True}

    after = evaluate_song_difficulty(
        list(option["final"]), known_chords, avoid_barre, option["final"], avg_changes_per_line
    )
    by_shape = option["by_shape"]
    lines = structure_sheet(raw_sheet, -option["fret"])
    for line in lines:
        if line["type"] == "chords":
            line["text"] = map_chord_line(line["text"], lambda chord: by_shape.get(chord, chord))

    substitutions = _substitutions(option, freqs)
    return {
        "capo_fret": option["fret"],
        "substitutions": substitutions,
        "fidelity_pct": option["fidelity"],
        "level_before": before["level"],
        "level_after": after["level"],
        "score_before": before["score"],
        "score_after": after["score"],
        "remaining_hard": option["hard"],
        "chords": list(option["final"]),
        "sheet_lines": lines,
        "note": _note(option, substitutions),
    }
