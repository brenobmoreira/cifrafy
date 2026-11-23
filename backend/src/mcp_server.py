"""Cifrafy FastMCP Server — mounted at `/mcp` inside FastAPI (and runnable via stdio)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import FastMCP

from src.core.analyzer import evaluate_song_difficulty
from src.core.chord_scraper import fetch_chord_sheet_async
from src.core.spotify_client import fetch_playlist_tracks, fetch_user_playlists
from src.db import db

mcp = FastMCP("Cifrafy")


@mcp.tool()
async def list_user_playlists(limit: int = 20) -> list[dict[str, Any]]:
    """Retorna as playlists do usuário autenticado no Spotify (ou playlists demo se não configurado)."""
    return await fetch_user_playlists(limit=limit)


@mcp.tool()
async def get_playlist_tracks(playlist_id: str, limit: int = 30) -> list[dict[str, Any]]:
    """Busca as faixas de uma playlist específica do Spotify."""
    return await fetch_playlist_tracks(playlist_id=playlist_id, limit=limit)


@mcp.tool()
async def fetch_chord_sheet(artist: str, track_name: str) -> dict[str, Any]:
    """Busca a cifra da música no Cifra Club (com cache local SQLite) e extrai os acordes e métricas."""
    result = await fetch_chord_sheet_async(artist=artist, track_name=track_name, save_raw_sheet=True)
    return {
        "url": result["url"],
        "artist": result["artist"],
        "track_name": result["track_name"],
        "unique_chords": result["unique_chords"],
        "chord_frequencies": result.get("chord_frequencies", {}),
        "avg_changes_per_line": result.get("avg_changes_per_line", 0.0),
        "has_tabs": result.get("has_tabs", False),
        "raw_chord_sheet": result.get("raw_chord_sheet", ""),
        "key": result.get("key"),
        "cached": result.get("cached", False),
    }


@mcp.tool()
def evaluate_difficulty(
    unique_chords: list[str],
    known_chords: list[str] | None = None,
    avoid_barre: bool = True,
    chord_frequencies: dict[str, int] | None = None,
    avg_changes_per_line: float = 2.0,
) -> dict[str, Any]:
    """Avalia se a música é amigável para o nível atual do violonista, detectando pestanas, acordes intrusos e sugerindo capotraste."""
    profile = db.get_user_profile()
    effective_known = known_chords if known_chords is not None else profile["known_chords"]
    return evaluate_song_difficulty(
        unique_chords=unique_chords,
        known_chords=effective_known,
        avoid_barre=avoid_barre,
        chord_frequencies=chord_frequencies,
        avg_changes_per_line=avg_changes_per_line,
    )


@mcp.tool()
async def recommend_playlist_tracks(
    playlist_id: str,
    limit: int = 15,
    target_level: str | None = None,
    avoid_barre: bool = True,
) -> list[dict[str, Any]]:
    """Indexa (em modo leve ~200 bytes/música) e avalia as músicas de uma playlist, retornando ordenadas das mais fáceis para as mais difíceis."""
    tracks = await fetch_playlist_tracks(playlist_id=playlist_id, limit=limit)
    profile = db.get_user_profile()
    known_chords = profile["known_chords"]

    semaphore = asyncio.Semaphore(4)

    async def _analyze_track(t: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            sheet = await fetch_chord_sheet_async(
                artist=t["artist"],
                track_name=t["name"],
                spotify_track_id=t.get("track_id"),
                save_raw_sheet=False,
            )
            if not sheet.get("unique_chords"):
                return {
                    "track_id": t.get("track_id"),
                    "artist": t["artist"],
                    "name": t["name"],
                    "found": False,
                }
            evaluation = evaluate_song_difficulty(
                unique_chords=sheet["unique_chords"],
                known_chords=known_chords,
                avoid_barre=avoid_barre,
                chord_frequencies=sheet.get("chord_frequencies"),
                avg_changes_per_line=sheet.get("avg_changes_per_line", 2.0),
                has_tabs=sheet.get("has_tabs", False),
            )
            return {
                "track_id": t.get("track_id"),
                "artist": sheet["artist"],
                "name": sheet["track_name"],
                "track_name": sheet["track_name"],
                "url": sheet["url"],
                "key": sheet["key"],
                "unique_chords": sheet["unique_chords"],
                "chord_frequencies": sheet.get("chord_frequencies", {}),
                "found": True,
                **evaluation,
            }

    results = await asyncio.gather(*[_analyze_track(t) for t in tracks])
    valid = [r for r in results if r.get("found")]

    if target_level:
        target_norm = target_level.strip().lower()
        valid = [r for r in valid if r["level"].lower() == target_norm]

    valid.sort(key=lambda x: x["score"], reverse=True)
    return valid


if __name__ == "__main__":
    mcp.run()
