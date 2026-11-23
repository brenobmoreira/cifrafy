"""SQLite storage with 2-tier architecture:
1. song_index: Lightweight permanent harmonic metadata (~200 bytes/song) for instant SQL queries.
2. chord_sheets_cache: Zlib-compressed raw chord sheets with LRU eviction so the DB never bloats.
3. user_profile: Dynamic known chords and user preferences.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from src.config import settings


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or settings.DATABASE_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS song_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist_slug TEXT NOT NULL,
                    track_slug TEXT NOT NULL,
                    spotify_track_id TEXT,
                    artist_name TEXT NOT NULL,
                    track_name TEXT NOT NULL,
                    cifra_url TEXT NOT NULL,
                    has_simplified INTEGER NOT NULL DEFAULT 0,
                    musical_key TEXT,
                    unique_chords_count INTEGER NOT NULL DEFAULT 0,
                    has_barre_chords INTEGER NOT NULL DEFAULT 0,
                    chord_frequencies_json TEXT NOT NULL DEFAULT '{}',
                    simplified_chords_json TEXT,
                    total_chord_events INTEGER NOT NULL DEFAULT 0,
                    avg_changes_per_line REAL NOT NULL DEFAULT 0.0,
                    has_tabs INTEGER NOT NULL DEFAULT 0,
                    best_capo_fret INTEGER NOT NULL DEFAULT 0,
                    best_capo_chords_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    UNIQUE(artist_slug, track_slug)
                );

                CREATE INDEX IF NOT EXISTS idx_song_chords_count
                    ON song_index(unique_chords_count, has_barre_chords);
                CREATE INDEX IF NOT EXISTS idx_song_spotify_id
                    ON song_index(spotify_track_id);

                CREATE TABLE IF NOT EXISTS chord_sheets_cache (
                    song_id INTEGER PRIMARY KEY,
                    compressed_sheet BLOB NOT NULL,
                    compressed_simplified BLOB,
                    last_accessed_at TEXT NOT NULL,
                    FOREIGN KEY (song_id) REFERENCES song_index(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_profile (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    known_chords_json TEXT NOT NULL,
                    learning_chords_json TEXT NOT NULL DEFAULT '[]',
                    avoid_barre INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                """
            )
            # Ensure default user profile exists
            cur = conn.execute("SELECT id FROM user_profile WHERE id = 1")
            if cur.fetchone() is None:
                conn.execute(
                    """
                    INSERT INTO user_profile (id, known_chords_json, learning_chords_json, avoid_barre, updated_at)
                    VALUES (1, ?, '[]', 1, ?)
                    """,
                    (json.dumps(settings.DEFAULT_KNOWN_CHORDS), _utc_now()),
                )

    # --- User Profile Methods ---

    def get_user_profile(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
            if not row:
                return {
                    "known_chords": settings.DEFAULT_KNOWN_CHORDS,
                    "learning_chords": [],
                    "avoid_barre": True,
                }
            return {
                "known_chords": json.loads(row["known_chords_json"]),
                "learning_chords": json.loads(row["learning_chords_json"]),
                "avoid_barre": bool(row["avoid_barre"]),
                "updated_at": row["updated_at"],
            }

    def update_user_profile(
        self,
        known_chords: list[str] | None = None,
        learning_chords: list[str] | None = None,
        avoid_barre: bool | None = None,
    ) -> dict[str, Any]:
        current = self.get_user_profile()
        new_known = known_chords if known_chords is not None else current["known_chords"]
        new_learning = (
            learning_chords if learning_chords is not None else current["learning_chords"]
        )
        new_avoid = avoid_barre if avoid_barre is not None else current["avoid_barre"]

        with self.connect() as conn:
            conn.execute(
                """
                UPDATE user_profile
                SET known_chords_json = ?, learning_chords_json = ?, avoid_barre = ?, updated_at = ?
                WHERE id = 1
                """,
                (
                    json.dumps(new_known),
                    json.dumps(new_learning),
                    int(new_avoid),
                    _utc_now(),
                ),
            )
        return self.get_user_profile()

    # --- Song Index & Sheet Cache Methods ---

    def upsert_song_index(
        self,
        *,
        artist_slug: str,
        track_slug: str,
        artist_name: str,
        track_name: str,
        cifra_url: str,
        musical_key: str | None,
        chord_frequencies: dict[str, int],
        has_barre_chords: bool,
        avg_changes_per_line: float,
        has_tabs: bool,
        best_capo_fret: int = 0,
        best_capo_chords: list[str] | None = None,
        has_simplified: bool = False,
        simplified_chords: dict[str, int] | None = None,
        spotify_track_id: str | None = None,
        raw_chord_sheet: str | None = None,
    ) -> int:
        now = _utc_now()
        unique_count = len(chord_frequencies)
        total_events = sum(chord_frequencies.values())

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO song_index (
                    artist_slug, track_slug, spotify_track_id, artist_name, track_name,
                    cifra_url, has_simplified, musical_key, unique_chords_count,
                    has_barre_chords, chord_frequencies_json, simplified_chords_json,
                    total_chord_events, avg_changes_per_line, has_tabs,
                    best_capo_fret, best_capo_chords_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artist_slug, track_slug) DO UPDATE SET
                    spotify_track_id = COALESCE(excluded.spotify_track_id, song_index.spotify_track_id),
                    artist_name = excluded.artist_name,
                    track_name = excluded.track_name,
                    cifra_url = excluded.cifra_url,
                    has_simplified = excluded.has_simplified,
                    musical_key = excluded.musical_key,
                    unique_chords_count = excluded.unique_chords_count,
                    has_barre_chords = excluded.has_barre_chords,
                    chord_frequencies_json = excluded.chord_frequencies_json,
                    simplified_chords_json = COALESCE(excluded.simplified_chords_json, song_index.simplified_chords_json),
                    total_chord_events = excluded.total_chord_events,
                    avg_changes_per_line = excluded.avg_changes_per_line,
                    has_tabs = excluded.has_tabs,
                    best_capo_fret = excluded.best_capo_fret,
                    best_capo_chords_json = excluded.best_capo_chords_json,
                    updated_at = excluded.updated_at
                """,
                (
                    artist_slug,
                    track_slug,
                    spotify_track_id,
                    artist_name,
                    track_name,
                    cifra_url,
                    int(has_simplified),
                    musical_key,
                    unique_count,
                    int(has_barre_chords),
                    json.dumps(chord_frequencies, ensure_ascii=False),
                    json.dumps(simplified_chords, ensure_ascii=False)
                    if simplified_chords
                    else None,
                    total_events,
                    round(avg_changes_per_line, 2),
                    int(has_tabs),
                    best_capo_fret,
                    json.dumps(best_capo_chords or [], ensure_ascii=False),
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM song_index WHERE artist_slug = ? AND track_slug = ?",
                (artist_slug, track_slug),
            ).fetchone()
            song_id = int(row["id"])

            if raw_chord_sheet:
                compressed = zlib.compress(raw_chord_sheet.encode("utf-8"), level=6)
                conn.execute(
                    """
                    INSERT INTO chord_sheets_cache (song_id, compressed_sheet, last_accessed_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(song_id) DO UPDATE SET
                        compressed_sheet = excluded.compressed_sheet,
                        last_accessed_at = excluded.last_accessed_at
                    """,
                    (song_id, compressed, now),
                )
                # Enforce LRU cache limit so raw sheets never bloat the database
                conn.execute(
                    """
                    DELETE FROM chord_sheets_cache
                    WHERE song_id NOT IN (
                        SELECT song_id FROM chord_sheets_cache
                        ORDER BY last_accessed_at DESC
                        LIMIT ?
                    )
                    """,
                    (settings.MAX_CACHED_SHEETS,),
                )

            return song_id

    def get_song_by_slugs(
        self, artist_slug: str, track_slug: str, include_sheet: bool = True
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM song_index WHERE artist_slug = ? AND track_slug = ?",
                (artist_slug, track_slug),
            ).fetchone()
            if not row:
                return None

            data = self._row_to_dict(row)
            if include_sheet:
                cache_row = conn.execute(
                    "SELECT compressed_sheet FROM chord_sheets_cache WHERE song_id = ?",
                    (row["id"],),
                ).fetchone()
                if cache_row and cache_row["compressed_sheet"]:
                    conn.execute(
                        "UPDATE chord_sheets_cache SET last_accessed_at = ? WHERE song_id = ?",
                        (_utc_now(), row["id"]),
                    )
                    data["raw_chord_sheet"] = zlib.decompress(
                        cache_row["compressed_sheet"]
                    ).decode("utf-8")
                else:
                    data["raw_chord_sheet"] = None
            return data

    def query_indexed_songs(
        self,
        *,
        max_chords: int | None = None,
        avoid_barre: bool | None = None,
        search: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []

        if max_chords is not None:
            clauses.append("unique_chords_count <= ?")
            params.append(max_chords)
        if avoid_barre:
            clauses.append("(has_barre_chords = 0 OR best_capo_fret > 0)")
        if search:
            clauses.append("(artist_name LIKE ? OR track_name LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        params.append(limit)
        sql = f"""
            SELECT * FROM song_index
            WHERE {' AND '.join(clauses)}
            ORDER BY has_barre_chords ASC, unique_chords_count ASC, avg_changes_per_line ASC
            LIMIT ?
        """
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        chord_freqs: dict[str, int] = json.loads(row["chord_frequencies_json"])
        return {
            "id": row["id"],
            "artist_slug": row["artist_slug"],
            "track_slug": row["track_slug"],
            "spotify_track_id": row["spotify_track_id"],
            "artist": row["artist_name"],
            "track_name": row["track_name"],
            "url": row["cifra_url"],
            "has_simplified": bool(row["has_simplified"]),
            "key": row["musical_key"],
            "unique_chords": list(chord_freqs.keys()),
            "unique_chords_count": row["unique_chords_count"],
            "has_barre_chords": bool(row["has_barre_chords"]),
            "chord_frequencies": chord_freqs,
            "total_chord_events": row["total_chord_events"],
            "avg_changes_per_line": row["avg_changes_per_line"],
            "has_tabs": bool(row["has_tabs"]),
            "best_capo_fret": row["best_capo_fret"],
            "best_capo_chords": json.loads(row["best_capo_chords_json"]),
            "updated_at": row["updated_at"],
        }


db = Database()
