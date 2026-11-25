"""SQLite storage with 2-tier architecture:
1. song_index: Lightweight permanent harmonic metadata (~200 bytes/song) for instant SQL queries.
2. chord_sheets_cache: Permanent zlib-compressed full chord sheets, never evicted automatically.
3. user_profile: Dynamic known chords and user preferences.
4. repertoire: Songs the user wants to learn, is practicing or has mastered.
5. practice_sessions: Timed chord change drills.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import unicodedata
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from src.config import settings
from src.core.analyzer import extract_chord_sequence

logger = logging.getLogger(__name__)

SIMPLIFIED_SUFFIX = "__simplificada"
# Key whose shapes the user likes to play in. Minor songs aim at its relative minor.
DEFAULT_PREFERRED_KEY = "G"
CHORD_SEQUENCE_VERSION_KEY = "chord_sequence_version"
CHORD_SEQUENCE_VERSION = 2

# A simplified row is hidden when the normal version of the same song is also indexed.
_VISIBLE_SONG_SQL = f"""
    (s.track_slug NOT LIKE '%\\_\\_simplificada' ESCAPE '\\'
     OR NOT EXISTS (
        SELECT 1 FROM song_index base
        WHERE base.artist_slug = s.artist_slug
          AND base.track_slug = substr(s.track_slug, 1, length(s.track_slug) - {len(SIMPLIFIED_SUFFIX)})
     ))
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fold_text(value: str | None) -> str:
    """Lowercase and remove accents so searches match with or without them."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


class Database:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or settings.DATABASE_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.create_function("fold_text", 1, fold_text, deterministic=True)
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

                CREATE TABLE IF NOT EXISTS saved_playlists (
                    playlist_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    total_tracks INTEGER NOT NULL DEFAULT 0,
                    source_url TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS song_aliases (
                    artist_slug TEXT NOT NULL,
                    track_slug TEXT NOT NULL,
                    song_id INTEGER NOT NULL,
                    PRIMARY KEY (artist_slug, track_slug),
                    FOREIGN KEY (song_id) REFERENCES song_index(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS spotify_song_links (
                    spotify_track_id TEXT NOT NULL,
                    is_simplified INTEGER NOT NULL,
                    song_id INTEGER NOT NULL,
                    PRIMARY KEY (spotify_track_id, is_simplified),
                    FOREIGN KEY (song_id) REFERENCES song_index(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS repertoire (
                    song_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('want', 'practicing', 'mastered')),
                    preferred_semitones INTEGER CHECK (preferred_semitones BETWEEN -11 AND 11),
                    preferred_capo INTEGER CHECK (preferred_capo BETWEEN 0 AND 11),
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (song_id) REFERENCES song_index(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS practice_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chord_a TEXT NOT NULL,
                    chord_b TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    duration_seconds INTEGER NOT NULL,
                    per_minute REAL NOT NULL,
                    bpm INTEGER,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_practice_change
                    ON practice_sessions(chord_a, chord_b, id);

                CREATE TABLE IF NOT EXISTS study_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_date TEXT NOT NULL,
                    duration_seconds INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('free', 'song', 'changes', 'session')),
                    song_id INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (song_id) REFERENCES song_index(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_study_date ON study_sessions(local_date);

                CREATE TABLE IF NOT EXISTS service_state (
                    key TEXT PRIMARY KEY,
                    value REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS study_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    goal_minutes INTEGER NOT NULL DEFAULT 20
                );

                INSERT OR IGNORE INTO study_settings (id, goal_minutes) VALUES (1, 20);

                INSERT OR IGNORE INTO spotify_song_links (spotify_track_id, is_simplified, song_id)
                SELECT spotify_track_id, track_slug LIKE '%\\_\\_simplificada' ESCAPE '\\', id
                FROM song_index
                WHERE length(spotify_track_id) = 22 AND instr(spotify_track_id, '_') = 0;
                """
            )
            profile_columns = {row["name"] for row in conn.execute("PRAGMA table_info(user_profile)")}
            if "preferred_key" not in profile_columns:
                conn.execute("ALTER TABLE user_profile ADD COLUMN preferred_key TEXT NOT NULL DEFAULT 'G'")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(song_index)")}
            if "chord_sequence_json" not in columns:
                conn.execute("ALTER TABLE song_index ADD COLUMN chord_sequence_json TEXT")
            self._backfill_chord_sequences(conn)

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

    @staticmethod
    def _backfill_chord_sequences(conn: sqlite3.Connection) -> None:
        """Extract sequences from sheets already stored. One bad sheet never blocks startup.

        Bump CHORD_SEQUENCE_VERSION when the extraction rules change to redo every stored sheet.
        """
        saved = conn.execute("SELECT value FROM service_state WHERE key = ?", (CHORD_SEQUENCE_VERSION_KEY,)).fetchone()
        redo_all = saved is None or saved["value"] < CHORD_SEQUENCE_VERSION
        rows = conn.execute(
            f"""
            SELECT s.id, c.compressed_sheet
            FROM song_index s JOIN chord_sheets_cache c ON c.song_id = s.id
            {"" if redo_all else "WHERE s.chord_sequence_json IS NULL"}
            """
        ).fetchall()
        conn.execute(
            "INSERT INTO service_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (CHORD_SEQUENCE_VERSION_KEY, CHORD_SEQUENCE_VERSION),
        )
        for row in rows:
            try:
                sheet = zlib.decompress(row["compressed_sheet"]).decode("utf-8")
                sequence = extract_chord_sequence(sheet)
            except (zlib.error, UnicodeDecodeError, ValueError) as exc:
                logger.warning("Cifra %s ignorada ao extrair a sequência: %s", row["id"], exc)
                continue
            conn.execute(
                "UPDATE song_index SET chord_sequence_json = ? WHERE id = ?",
                (json.dumps(sequence, ensure_ascii=False), row["id"]),
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
                    "preferred_key": DEFAULT_PREFERRED_KEY,
                }
            return {
                "known_chords": json.loads(row["known_chords_json"]),
                "learning_chords": json.loads(row["learning_chords_json"]),
                "avoid_barre": bool(row["avoid_barre"]),
                "preferred_key": row["preferred_key"] or DEFAULT_PREFERRED_KEY,
                "updated_at": row["updated_at"],
            }

    def update_user_profile(
        self,
        known_chords: list[str] | None = None,
        learning_chords: list[str] | None = None,
        avoid_barre: bool | None = None,
        preferred_key: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_user_profile()
        new_key = preferred_key or current["preferred_key"]
        new_known = known_chords if known_chords is not None else current["known_chords"]
        new_learning = (
            learning_chords if learning_chords is not None else current["learning_chords"]
        )
        new_avoid = avoid_barre if avoid_barre is not None else current["avoid_barre"]

        with self.connect() as conn:
            conn.execute(
                """
                UPDATE user_profile
                SET known_chords_json = ?, learning_chords_json = ?, avoid_barre = ?, preferred_key = ?,
                    updated_at = ?
                WHERE id = 1
                """,
                (
                    json.dumps(new_known),
                    json.dumps(new_learning),
                    int(new_avoid),
                    new_key,
                    _utc_now(),
                ),
            )
        return self.get_user_profile()

    # --- Saved Playlists Methods ---

    def list_saved_playlists(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM saved_playlists ORDER BY created_at DESC"
            ).fetchall()
            return [
                {
                    "id": r["playlist_id"],
                    "name": r["name"],
                    "total_tracks": r["total_tracks"],
                    "source_url": r["source_url"],
                }
                for r in rows
            ]

    def upsert_saved_playlist(
        self, playlist_id: str, name: str, total_tracks: int = 0, source_url: str | None = None
    ) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO saved_playlists (playlist_id, name, total_tracks, source_url, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(playlist_id) DO UPDATE SET
                    name = excluded.name,
                    total_tracks = excluded.total_tracks,
                    source_url = COALESCE(excluded.source_url, saved_playlists.source_url)
                """,
                (playlist_id, name, total_tracks, source_url, _utc_now()),
            )
        return {"id": playlist_id, "name": name, "total_tracks": total_tracks}

    def delete_saved_playlist(self, playlist_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM saved_playlists WHERE playlist_id = ?", (playlist_id,))

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
        sequence_json = (
            json.dumps(extract_chord_sequence(raw_chord_sheet), ensure_ascii=False) if raw_chord_sheet else None
        )

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO song_index (
                    artist_slug, track_slug, spotify_track_id, artist_name, track_name,
                    cifra_url, has_simplified, musical_key, unique_chords_count,
                    has_barre_chords, chord_frequencies_json, simplified_chords_json,
                    total_chord_events, avg_changes_per_line, has_tabs,
                    best_capo_fret, best_capo_chords_json, chord_sequence_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    chord_sequence_json = COALESCE(excluded.chord_sequence_json, song_index.chord_sequence_json),
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
                    sequence_json,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM song_index WHERE artist_slug = ? AND track_slug = ?",
                (artist_slug, track_slug),
            ).fetchone()
            song_id = int(row["id"])

            if raw_chord_sheet:
                compressed = zlib.compress(raw_chord_sheet.encode("utf-8"), level=9)
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

    def link_song(
        self,
        song_id: int,
        aliases: Iterable[tuple[str, str]],
        *,
        spotify_track_id: str | None = None,
        simplified: bool = False,
    ) -> None:
        """Remember which requested slugs and Spotify track resolve to this song."""
        with self.connect() as conn:
            # A real row at these slugs now exists, so an older alias must not shadow it.
            conn.execute(
                """
                DELETE FROM song_aliases
                WHERE (artist_slug, track_slug) IN (
                    SELECT artist_slug, track_slug FROM song_index WHERE id = ?
                )
                """,
                (song_id,),
            )
            conn.executemany(
                """
                INSERT INTO song_aliases (artist_slug, track_slug, song_id)
                VALUES (?, ?, ?)
                ON CONFLICT(artist_slug, track_slug) DO UPDATE SET song_id = excluded.song_id
                """,
                [(artist, track, song_id) for artist, track in aliases],
            )
            if spotify_track_id:
                conn.execute(
                    """
                    INSERT INTO spotify_song_links (spotify_track_id, is_simplified, song_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT(spotify_track_id, is_simplified) DO UPDATE SET song_id = excluded.song_id
                    """,
                    (spotify_track_id, int(simplified), song_id),
                )

    def find_song(
        self,
        artist_slugs: list[str],
        track_slug: str,
        *,
        spotify_track_id: str | None = None,
        simplified: bool = False,
        include_sheet: bool = False,
    ) -> dict[str, Any] | None:
        """Resolve a song by Spotify link, exact slug or alias."""
        with self.connect() as conn:
            row = None
            if spotify_track_id:
                row = conn.execute(
                    """
                    SELECT s.artist_slug, s.track_slug
                    FROM spotify_song_links l JOIN song_index s ON s.id = l.song_id
                    WHERE l.spotify_track_id = ? AND l.is_simplified = ?
                    """,
                    (spotify_track_id, int(simplified)),
                ).fetchone()
            # Aliases come first: a legacy "simplified" row may hold no real simplified version.
            for artist_slug in artist_slugs:
                if row:
                    break
                row = conn.execute(
                    """
                    SELECT s.artist_slug, s.track_slug
                    FROM song_aliases a JOIN song_index s ON s.id = a.song_id
                    WHERE a.artist_slug = ? AND a.track_slug = ?
                    """,
                    (artist_slug, track_slug),
                ).fetchone() or conn.execute(
                    "SELECT artist_slug, track_slug FROM song_index WHERE artist_slug = ? AND track_slug = ?",
                    (artist_slug, track_slug),
                ).fetchone()
            if not row:
                return None
            found = (row["artist_slug"], row["track_slug"])
        return self.get_song_by_slugs(*found, include_sheet=include_sheet)

    def songs_by_artist_slugs(self, artist_slugs: list[str]) -> list[dict[str, str]]:
        """Slugs and display names of the indexed songs of an artist, for name based lookup."""
        if not artist_slugs:
            return []
        marks = ",".join("?" * len(artist_slugs))
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT artist_slug, track_slug, track_name FROM song_index WHERE artist_slug IN ({marks})",
                artist_slugs,
            ).fetchall()
            return [dict(row) for row in rows]

    def count_indexed_songs(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS total FROM song_index s WHERE {_VISIBLE_SONG_SQL}"
            ).fetchone()
            return int(row["total"])

    def query_indexed_songs(
        self,
        *,
        max_chords: int | None = None,
        avoid_barre: bool | None = None,
        search: str | None = None,
        limit: int | None = 50,
    ) -> list[dict[str, Any]]:
        clauses = [_VISIBLE_SONG_SQL]
        params: list[Any] = []

        if max_chords is not None:
            clauses.append("unique_chords_count <= ?")
            params.append(max_chords)
        if avoid_barre:
            clauses.append("(has_barre_chords = 0 OR best_capo_fret > 0)")
        if search:
            term = fold_text(search).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append(
                "(fold_text(artist_name) LIKE ? ESCAPE '\\' OR fold_text(track_name) LIKE ? ESCAPE '\\')"
            )
            params.extend([f"%{term}%", f"%{term}%"])

        sql = f"""
            SELECT s.* FROM song_index s
            WHERE {' AND '.join(clauses)}
            ORDER BY has_barre_chords ASC, unique_chords_count ASC, avg_changes_per_line ASC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    # --- Repertoire Methods ---

    @staticmethod
    def _repertoire_entry(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "status": row["status"],
            "preferred_semitones": row["preferred_semitones"],
            "preferred_capo": row["preferred_capo"],
        }

    def get_repertoire_map(self) -> dict[int, dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM repertoire").fetchall()
            return {int(r["song_id"]): self._repertoire_entry(r) for r in rows}

    def get_repertoire_entry(self, song_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM repertoire WHERE song_id = ?", (song_id,)).fetchone()
            return self._repertoire_entry(row) if row else None

    def set_repertoire_entry(
        self,
        song_id: int,
        status: str,
        preferred_semitones: int | None = None,
        preferred_capo: int | None = None,
    ) -> dict[str, Any] | None:
        """Create or update an entry. Preferences left as None keep their current value."""
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM song_index WHERE id = ?", (song_id,)).fetchone():
                return None
            conn.execute(
                """
                INSERT INTO repertoire (song_id, status, preferred_semitones, preferred_capo, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(song_id) DO UPDATE SET
                    status = excluded.status,
                    preferred_semitones = COALESCE(excluded.preferred_semitones, repertoire.preferred_semitones),
                    preferred_capo = COALESCE(excluded.preferred_capo, repertoire.preferred_capo),
                    updated_at = excluded.updated_at
                """,
                (song_id, status, preferred_semitones, preferred_capo, _utc_now()),
            )
            row = conn.execute("SELECT * FROM repertoire WHERE song_id = ?", (song_id,)).fetchone()
            return self._repertoire_entry(row)

    def delete_song(self, song_id: int) -> bool:
        """Remove a song with its sheet, aliases, links and repertoire entry. Study time stays."""
        with self.connect() as conn:
            return conn.execute("DELETE FROM song_index WHERE id = ?", (song_id,)).rowcount > 0

    def delete_repertoire_entry(self, song_id: int) -> bool:
        with self.connect() as conn:
            return conn.execute("DELETE FROM repertoire WHERE song_id = ?", (song_id,)).rowcount > 0

    def list_repertoire_songs(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT s.*, r.status, r.preferred_semitones, r.preferred_capo
            FROM repertoire r JOIN song_index s ON s.id = r.song_id
        """
        params: list[Any] = []
        if status:
            sql += " WHERE r.status = ?"
            params.append(status)
        sql += " ORDER BY r.updated_at DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [{**self._row_to_dict(r), "repertoire": self._repertoire_entry(r)} for r in rows]

    # --- Study Time Methods ---

    def add_study_session(
        self, local_date: str, duration_seconds: int, kind: str, song_id: int | None = None
    ) -> dict[str, Any]:
        created_at = _utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO study_sessions (local_date, duration_seconds, kind, song_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (local_date, duration_seconds, kind, song_id, created_at),
            )
            session_id = int(cursor.lastrowid)
        return {
            "id": session_id,
            "local_date": local_date,
            "duration_seconds": duration_seconds,
            "kind": kind,
            "song_id": song_id,
            "created_at": created_at,
        }

    def get_study_totals(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT local_date, SUM(duration_seconds) AS seconds FROM study_sessions GROUP BY local_date"
            ).fetchall()
            return {r["local_date"]: int(r["seconds"]) for r in rows}

    def get_study_goal(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT goal_minutes FROM study_settings WHERE id = 1").fetchone()
            return int(row["goal_minutes"]) if row else 20

    def set_study_goal(self, minutes: int) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO study_settings (id, goal_minutes) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET goal_minutes = excluded.goal_minutes
                """,
                (minutes,),
            )
        return minutes

    # --- Practice Methods ---

    def get_song_by_id(self, song_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM song_index WHERE id = ?", (song_id,)).fetchone()
            return self._row_to_dict(row) if row else None

    def get_chord_sequences(self) -> dict[int, list[str]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, chord_sequence_json FROM song_index WHERE chord_sequence_json IS NOT NULL"
            ).fetchall()
            return {int(r["id"]): json.loads(r["chord_sequence_json"]) for r in rows}

    def add_practice_session(
        self,
        chord_a: str,
        chord_b: str,
        count: int,
        duration_seconds: int,
        per_minute: float,
        bpm: int | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO practice_sessions (chord_a, chord_b, count, duration_seconds, per_minute, bpm, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (chord_a, chord_b, count, duration_seconds, per_minute, bpm, now),
            )
            session_id = int(cursor.lastrowid)
        return {
            "id": session_id,
            "count": count,
            "duration_seconds": duration_seconds,
            "per_minute": per_minute,
            "bpm": bpm,
            "created_at": now,
        }

    def get_practice_stats(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Best result, latest result and session count of every practiced change."""
        stats: dict[tuple[str, str], dict[str, Any]] = {}
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT chord_a, chord_b, per_minute FROM practice_sessions ORDER BY id"
            ).fetchall()
        for row in rows:
            entry = stats.setdefault(
                (row["chord_a"], row["chord_b"]),
                {"best_per_minute": row["per_minute"], "last_per_minute": None, "sessions": 0},
            )
            entry["best_per_minute"] = max(entry["best_per_minute"], row["per_minute"])
            entry["last_per_minute"] = row["per_minute"]
            entry["sessions"] += 1
        return stats

    def list_practice_sessions(self, chord_a: str, chord_b: str, limit: int = 30) -> list[dict[str, Any]]:
        """Latest sessions of a change, in chronological order."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT per_minute, count, duration_seconds, bpm, created_at
                FROM practice_sessions
                WHERE chord_a = ? AND chord_b = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (chord_a, chord_b, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # --- Service State ---

    def get_service_value(self, key: str) -> float | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM service_state WHERE key = ?", (key,)).fetchone()
            return float(row["value"]) if row else None

    def set_service_value(self, key: str, value: float) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO service_state (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def delete_service_value(self, key: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM service_state WHERE key = ?", (key,))

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
