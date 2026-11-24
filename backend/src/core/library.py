"""Use cases shared by the REST API and the MCP server."""

from __future__ import annotations

from typing import Any

from src.core.analyzer import evaluate_song_difficulty, suggest_next_chords
from src.core.arrangement import build_arrangement
from src.core.chord_scraper import fetch_chord_sheet_async, find_cached_song
from src.core.spotify_client import fetch_listening_tracks, fetch_playlist_tracks
from src.db import db

REPERTOIRE_STATUSES = ("want", "practicing", "mastered")
NEXT_CHORDS_PLAYLIST_LIMIT = 200
LEVEL_CLOSENESS = {"Fácil": 3, "Médio": 2, "Difícil": 1}


def evaluate_song(song: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    return evaluate_song_difficulty(
        unique_chords=song["unique_chords"],
        known_chords=profile["known_chords"],
        avoid_barre=profile["avoid_barre"],
        chord_frequencies=song["chord_frequencies"],
        avg_changes_per_line=song["avg_changes_per_line"],
        has_tabs=song["has_tabs"],
    )


def search_catalog(
    *,
    max_chords: int | None = None,
    avoid_barre: bool | None = None,
    level: str | None = None,
    with_capo: bool = False,
    search: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Evaluate indexed songs for the current profile, easiest first, without using the network."""
    songs = db.query_indexed_songs(max_chords=max_chords, avoid_barre=avoid_barre, search=search, limit=None)
    profile = db.get_user_profile()
    repertoire = db.get_repertoire_map()
    wanted = level.strip().lower() if level else None

    items: list[dict[str, Any]] = []
    for song in songs:
        item = {**song, **evaluate_song(song, profile), "repertoire": repertoire.get(song["id"])}
        if wanted and item["level"].lower() != wanted:
            if not (with_capo and wanted == "fácil" and item["easy_with_capo"]):
                continue
        items.append(item)

    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:limit]


async def playlist_tracks_with_status(
    playlist_id: str, limit: int = 100, simplified: bool = False
) -> list[dict[str, Any]]:
    """List playlist tracks and join what is already indexed, without touching Cifra Club."""
    tracks = await fetch_playlist_tracks(playlist_id=playlist_id, limit=limit)
    return join_indexed_tracks(tracks, simplified)


def join_indexed_tracks(tracks: list[dict[str, Any]], simplified: bool = False) -> list[dict[str, Any]]:
    """Add the local index and evaluation to Spotify tracks. Never touches Cifra Club."""
    profile = db.get_user_profile()
    repertoire = db.get_repertoire_map()

    enriched: list[dict[str, Any]] = []
    for track in tracks:
        indexed = find_cached_song(
            track["artist"],
            track["name"],
            simplified=simplified,
            spotify_track_id=track.get("track_id"),
            fallback_to_normal=True,
        )
        if indexed and indexed.get("unique_chords"):
            enriched.append(
                {
                    **track,
                    "is_synced": True,
                    "song_id": indexed["id"],
                    "repertoire": repertoire.get(indexed["id"]),
                    "url": indexed["url"],
                    "key": indexed["key"],
                    "unique_chords": indexed["unique_chords"],
                    "chord_frequencies": indexed["chord_frequencies"],
                    **evaluate_song(indexed, profile),
                }
            )
        else:
            enriched.append({**track, "is_synced": False})
    return enriched


async def songs_in_scope(playlist_id: str | None = None) -> list[dict[str, Any]]:
    """Indexed songs of one playlist, or the whole library. Never touches Cifra Club."""
    if not playlist_id:
        return db.query_indexed_songs(limit=None)
    tracks = await fetch_playlist_tracks(playlist_id=playlist_id, limit=NEXT_CHORDS_PLAYLIST_LIMIT)
    by_id: dict[int, dict[str, Any]] = {}
    for track in tracks:
        song = find_cached_song(
            track["artist"], track["name"], spotify_track_id=track.get("track_id"), fallback_to_normal=True
        )
        if song:
            by_id[song["id"]] = song
    return list(by_id.values())


async def listening_recommendations(
    source: str = "top", time_range: str = "short_term", limit: int = 20
) -> dict[str, Any]:
    """Rank what the user really listens to, songs closest to their level first."""
    listening = await fetch_listening_tracks(source, time_range, limit)
    tracks = [
        {**track, "closeness": LEVEL_CLOSENESS.get(track.get("level"), 0) if track["is_synced"] else 0}
        for track in join_indexed_tracks(listening["tracks"])
    ]
    tracks.sort(key=lambda t: (-t["closeness"], -(t.get("score") or 0)))
    return {**listening, "tracks": tracks}


async def adapted_arrangement(artist: str, track_name: str, simplified: bool = False) -> dict[str, Any]:
    """Build the player's version of a song. The arrangement is never persisted."""
    sheet = await fetch_chord_sheet_async(
        artist=artist, track_name=track_name, simplified=simplified, require_sheet=True
    )
    base = {
        "status": sheet["status"],
        "artist": sheet.get("artist") or artist,
        "track_name": sheet.get("track_name") or track_name,
        "error": sheet.get("error"),
        "retry_after_seconds": sheet.get("retry_after_seconds"),
    }
    if not sheet.get("found"):
        return {
            **base,
            "song_id": None,
            "capo_fret": 0,
            "substitutions": [],
            "fidelity_pct": 100.0,
            "level_before": "Desconhecido",
            "level_after": "Desconhecido",
            "score_before": 0,
            "score_after": 0,
            "remaining_hard": [],
            "chords": [],
            "sheet_lines": [],
            "note": sheet.get("error") or "Cifra indisponível.",
        }

    song_id = sheet.get("id")
    if song_id is None and sheet.get("artist_slug"):
        stored = db.get_song_by_slugs(sheet["artist_slug"], sheet["track_slug"], include_sheet=False)
        song_id = stored["id"] if stored else None
    profile = db.get_user_profile()
    arrangement = build_arrangement(
        sheet.get("chord_frequencies") or {},
        sheet.get("raw_chord_sheet") or "",
        profile["known_chords"],
        profile["avoid_barre"],
        sheet.get("avg_changes_per_line", 2.0),
    )
    return {**base, "song_id": song_id, **arrangement}


async def next_chords_to_learn(playlist_id: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Rank chords to learn using only indexed songs, optionally those of one playlist."""
    songs = await songs_in_scope(playlist_id)
    profile = db.get_user_profile()
    return suggest_next_chords(songs, profile["known_chords"], profile["avoid_barre"], limit)


def update_known_chords(add: list[str] | None = None, remove: list[str] | None = None) -> dict[str, Any]:
    profile = db.get_user_profile()
    removed = {c.strip() for c in (remove or [])}
    known = [c for c in profile["known_chords"] if c not in removed]
    for chord in add or []:
        chord = chord.strip()
        if chord and chord not in known:
            known.append(chord)
    return db.update_user_profile(known_chords=known)


def list_repertoire(status: str | None = None) -> list[dict[str, Any]]:
    if status and status not in REPERTOIRE_STATUSES:
        raise ValueError(f"Status inválido. Use um destes: {', '.join(REPERTOIRE_STATUSES)}.")
    profile = db.get_user_profile()
    return [{**song, **evaluate_song(song, profile)} for song in db.list_repertoire_songs(status)]


def set_song_status(
    song_id: int,
    status: str,
    preferred_semitones: int | None = None,
    preferred_capo: int | None = None,
) -> dict[str, Any]:
    """Save a repertoire entry. Raises LookupError when the song is not indexed."""
    if status not in REPERTOIRE_STATUSES:
        raise ValueError(f"Status inválido. Use um destes: {', '.join(REPERTOIRE_STATUSES)}.")
    if preferred_semitones is not None and not -11 <= preferred_semitones <= 11:
        raise ValueError("preferred_semitones deve estar entre -11 e 11.")
    if preferred_capo is not None and not 0 <= preferred_capo <= 11:
        raise ValueError("preferred_capo deve estar entre 0 e 11.")
    entry = db.set_repertoire_entry(song_id, status, preferred_semitones, preferred_capo)
    if entry is None:
        raise LookupError(f"Música {song_id} não encontrada.")
    return entry
