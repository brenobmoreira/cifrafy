"""Learning trail and timed chord change drills, shared by the REST API and the MCP server."""

from __future__ import annotations

from collections import Counter
from typing import Any

from src.core import library
from src.core.analyzer import (
    chord_change_key,
    count_easy_songs,
    is_barre_chord,
    is_known_chord,
    suggest_next_chords,
    transpose_chord,
)
from src.db import db

TARGET_PER_MINUTE = 30
ACTIVE_SONG_WEIGHT = 3
UNKNOWN_CHORD_WEIGHT = 1.5
ON_TARGET_WEIGHT = 0.25

MIN_COUNT, MAX_COUNT = 0, 300
MIN_DURATION, MAX_DURATION = 15, 300
MIN_BPM, MAX_BPM = 30, 240
MIN_SESSION_MINUTES, MAX_SESSION_MINUTES = 5, 120


async def learning_trail(playlist_id: str | None = None, limit: int = 5) -> dict[str, Any]:
    """Pick the best chord, simulate learning it and repeat. The profile is never changed."""
    songs = await library.songs_in_scope(playlist_id)
    profile = db.get_user_profile()
    known, avoid_barre = profile["known_chords"], profile["avoid_barre"]

    learned: list[str] = []
    steps: list[dict[str, Any]] = []
    for step in range(1, limit + 1):
        best = suggest_next_chords(songs, known, avoid_barre, 1, learned)
        if not best:
            break
        suggestion = best[0]
        learned.append(suggestion["chord"])
        steps.append(
            {
                "step": step,
                "chord": suggestion["chord"],
                "is_barre": is_barre_chord(suggestion["chord"]),
                "songs_unlocked_easy": suggestion["songs_unlocked_easy"],
                "songs_improved": suggestion["songs_improved"],
                "total_easy_after": count_easy_songs(songs, known, avoid_barre, learned),
                "examples": suggestion["examples"],
            }
        )
    return {
        "steps": steps,
        "current_easy": count_easy_songs(songs, known, avoid_barre),
        "total_songs": len(songs),
    }


async def _change_scope(playlist_id: str | None, song_id: int | None) -> list[dict[str, Any]]:
    if song_id is not None:
        song = db.get_song_by_id(song_id)
        if song is None:
            raise LookupError(f"Música {song_id} não encontrada.")
        return [song]
    songs = await library.songs_in_scope(playlist_id)
    if playlist_id:
        return songs
    # A simplified version in the repertoire is hidden from the catalog but is still being practiced.
    seen = {song["id"] for song in songs}
    return songs + [song for song in db.list_repertoire_songs() if song["id"] not in seen]


def _priority(weighted_occurrences: int, involves_unknown: bool, best: float | None) -> float:
    if best is not None and best >= TARGET_PER_MINUTE:
        progress = ON_TARGET_WEIGHT
    else:
        progress = 1 + (TARGET_PER_MINUTE - (best or 0)) / TARGET_PER_MINUTE
    return round(weighted_occurrences * (UNKNOWN_CHORD_WEIGHT if involves_unknown else 1) * progress, 2)


async def list_changes(
    playlist_id: str | None = None, song_id: int | None = None, limit: int = 8
) -> dict[str, Any]:
    """Rank chord changes, in the tone each song is played, by what is most worth drilling."""
    songs = await _change_scope(playlist_id, song_id)
    sequences = db.get_chord_sequences()
    repertoire = db.get_repertoire_map()
    known = db.get_user_profile()["known_chords"]

    spellings: dict[tuple[str, str], Counter[tuple[str, str]]] = {}
    occurrences: Counter[tuple[str, str]] = Counter()
    weighted: Counter[tuple[str, str]] = Counter()
    song_counts: Counter[tuple[str, str]] = Counter()
    without_sequence = 0

    for song in songs:
        sequence = sequences.get(song["id"])
        if not sequence:
            without_sequence += 1
            continue
        entry = repertoire.get(song["id"]) or {}
        semitones = entry.get("preferred_semitones") or 0
        weight = ACTIVE_SONG_WEIGHT if entry.get("status") in ("want", "practicing") else 1
        played = [transpose_chord(chord, semitones) for chord in sequence]

        keys_in_song: set[tuple[str, str]] = set()
        for first, second in zip(played, played[1:]):
            key = chord_change_key(first, second)
            if key is None:
                continue
            spellings.setdefault(key, Counter())[(first, second)] += 1
            occurrences[key] += 1
            weighted[key] += weight
            keys_in_song.add(key)
        song_counts.update(keys_in_song)

    stats = db.get_practice_stats()
    changes: list[dict[str, Any]] = []
    for key, total in occurrences.items():
        first, second = spellings[key].most_common(1)[0][0]
        progress = stats.get(key, {})
        best = progress.get("best_per_minute")
        involves_unknown = not (is_known_chord(first, known) and is_known_chord(second, known))
        changes.append(
            {
                "from": first,
                "to": second,
                "occurrences": total,
                "songs": song_counts[key],
                "involves_unknown": involves_unknown,
                "best_per_minute": best,
                "last_per_minute": progress.get("last_per_minute"),
                "sessions": progress.get("sessions", 0),
                "target_per_minute": TARGET_PER_MINUTE,
                "priority": _priority(weighted[key], involves_unknown, best),
            }
        )
    changes.sort(key=lambda c: (-c["priority"], -c["occurrences"], c["from"], c["to"]))
    return {"changes": changes[:limit], "songs_without_sequence": without_sequence}


def _allocate_minutes(weights: list[float], minutes: int) -> list[int]:
    """Split the minutes by weight, at least 1 per block, summing exactly to the total."""
    extra = minutes - len(weights)
    total = sum(weights)
    shares = [extra * weight / total for weight in weights]
    result = [1 + int(share) for share in shares]
    by_remainder = sorted(range(len(weights)), key=lambda i: (-(shares[i] - int(shares[i])), i))
    for index in by_remainder[: minutes - sum(result)]:
        result[index] += 1
    return result


def _song_ref(song: dict[str, Any]) -> dict[str, Any]:
    return {
        "song_id": song["id"],
        "artist": song["artist"],
        "track_name": song["track_name"],
        "level": song.get("level"),
    }


def _pick_active_song(
    repertoire: list[dict[str, Any]], scope: list[dict[str, Any]], excluded: set[int]
) -> dict[str, Any] | None:
    """Prefer a song being practiced, then a wanted one, then the easiest indexed song not mastered."""
    for status in ("practicing", "want"):
        matches = [s for s in repertoire if s["repertoire"]["status"] == status and s["id"] not in excluded]
        if matches:
            return min(matches, key=lambda s: (-s["score"], s["id"]))
    profile = db.get_user_profile()
    mastered = {s["id"] for s in repertoire if s["repertoire"]["status"] == "mastered"}
    others = [
        {**song, **library.evaluate_song(song, profile)}
        for song in scope
        if song["id"] not in mastered and song["id"] not in excluded
    ]
    return min(others, key=lambda s: (-s["score"], s["id"])) if others else None


async def build_session(minutes: int = 20, playlist_id: str | None = None) -> dict[str, Any]:
    """Assemble a ready practice session. Blocks without data are left out."""
    if not MIN_SESSION_MINUTES <= minutes <= MAX_SESSION_MINUTES:
        raise ValueError(f"minutes deve estar entre {MIN_SESSION_MINUTES} e {MAX_SESSION_MINUTES}.")

    scope = await library.songs_in_scope(playlist_id)
    scope_ids = {song["id"] for song in scope}
    repertoire = [s for s in library.list_repertoire() if not playlist_id or s["id"] in scope_ids]
    changes = (await list_changes(playlist_id, None, 1))["changes"]
    trail = (await learning_trail(playlist_id, 1))["steps"]

    blocks: list[tuple[float, dict[str, Any]]] = []
    if changes:
        change = changes[0]
        blocks.append((
            0.2,
            {
                "kind": "warmup",
                "title": f"Aquecimento: {change['from']} e {change['to']}",
                "change": {
                    "from": change["from"],
                    "to": change["to"],
                    "target_per_minute": change["target_per_minute"],
                },
                "instruction": "Faça séries de 1 minuto, contando as trocas com som limpo.",
            },
        ))

    mastered = [s for s in repertoire if s["repertoire"]["status"] == "mastered"]
    used: set[int] = set()
    if mastered:
        song = min(mastered, key=lambda s: (-s["score"], s["id"]))
        used.add(song["id"])
        blocks.append((
            0.2,
            {
                "kind": "mastered",
                "title": f"Para soltar a mão: {song['track_name']}",
                "song": _song_ref(song),
                "instruction": "Toque do começo ao fim sem parar, curtindo a música.",
            },
        ))

    active = _pick_active_song(repertoire, scope, used)
    if active:
        blocks.append((
            0.4,
            {
                "kind": "in_progress",
                "title": f"Em andamento: {active['track_name']}",
                "song": _song_ref(active),
                "instruction": "Repita devagar o trecho mais difícil e depois toque a música inteira.",
            },
        ))

    if trail:
        step = trail[0]
        blocks.append((
            0.2,
            {
                "kind": "new_chord",
                "title": f"Acorde novo: {step['chord']}",
                "chord": {"chord": step["chord"], "is_barre": step["is_barre"]},
                "instruction": "Monte a forma corda por corda até todas soarem limpas.",
            },
        ))

    allocated = _allocate_minutes([weight for weight, _ in blocks], minutes) if blocks else []
    return {
        "minutes": minutes,
        "blocks": [{**block, "minutes": share} for (_, block), share in zip(blocks, allocated)],
    }


def _require_change(first: str, second: str) -> tuple[str, str]:
    key = chord_change_key(first.strip(), second.strip())
    if key is None:
        raise ValueError("Informe dois acordes válidos e diferentes.")
    return key


def log_change(
    from_chord: str, to_chord: str, count: int, duration_seconds: int = 60, bpm: int | None = None
) -> dict[str, Any]:
    key = _require_change(from_chord, to_chord)
    if not MIN_COUNT <= count <= MAX_COUNT:
        raise ValueError(f"count deve estar entre {MIN_COUNT} e {MAX_COUNT}.")
    if not MIN_DURATION <= duration_seconds <= MAX_DURATION:
        raise ValueError(f"duration_seconds deve estar entre {MIN_DURATION} e {MAX_DURATION}.")
    if bpm is not None and not MIN_BPM <= bpm <= MAX_BPM:
        raise ValueError(f"bpm deve estar entre {MIN_BPM} e {MAX_BPM}.")

    previous_best = db.get_practice_stats().get(key, {}).get("best_per_minute") or 0
    per_minute = round(count * 60 / duration_seconds, 1)
    session = db.add_practice_session(*key, count, duration_seconds, per_minute, bpm)
    current = db.get_practice_stats()[key]
    return {
        "session": {"from": from_chord.strip(), "to": to_chord.strip(), **session},
        "best_per_minute": current["best_per_minute"],
        "last_per_minute": current["last_per_minute"],
        "sessions": current["sessions"],
        "improved": per_minute > previous_best,
    }


def change_history(from_chord: str, to_chord: str, limit: int = 30) -> dict[str, Any]:
    key = _require_change(from_chord, to_chord)
    return {
        "from": from_chord.strip(),
        "to": to_chord.strip(),
        "sessions": db.list_practice_sessions(*key, limit),
    }
