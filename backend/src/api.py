"""Pure REST Backend API (FastAPI) for Guitar Practice Assistant.

Completely decoupled from both the MCP adapter and the Web Frontend.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.core.analyzer import evaluate_song_difficulty, transpose_chord
from src.core.chord_scraper import fetch_chord_sheet_async
from src.core.spotify_client import (
    exchange_code_for_token,
    fetch_playlist_tracks,
    fetch_user_playlists,
    get_spotify_authorize_url,
    is_spotify_configured,
)
from src.db import db
from src.mcp_server import mcp, recommend_playlist_tracks

mcp_http_app = mcp.http_app(path="/")

app = FastAPI(
    title="Cifrafy API & MCP Server",
    description="Backend unificado do Cifrafy expondo a REST API em `/api` e o servidor MCP (Model Context Protocol) em `/mcp`.",
    version="0.2.0",
    lifespan=getattr(mcp_http_app, "lifespan", None),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount MCP directly at /mcp inside the same FastAPI service!
app.mount("/mcp", mcp_http_app)


class EvaluateRequest(BaseModel):
    unique_chords: list[str] = Field(..., description="Lista de acordes únicos da música")
    known_chords: list[str] | None = Field(None, description="Lista de acordes que o usuário já domina")
    avoid_barre: bool = Field(True, description="Penalizar pestanas na avaliação")
    chord_frequencies: dict[str, int] | None = Field(
        None, description="Frequência de aparição de cada acorde na música"
    )
    avg_changes_per_line: float = Field(2.0, description="Média de trocas de acorde por linha")


class ProfileUpdateRequest(BaseModel):
    known_chords: list[str] | None = None
    learning_chords: list[str] | None = None
    avoid_barre: bool | None = None


class TransposeRequest(BaseModel):
    chords: list[str]
    semitones: int = Field(..., ge=-11, le=11)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "guitar-api"}


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    profile = db.get_user_profile()
    indexed_count = len(db.query_indexed_songs(limit=10000))
    return {
        "spotify_configured": is_spotify_configured(),
        "indexed_songs_count": indexed_count,
        "profile": profile,
    }


@app.get("/api/spotify/auth-url")
def get_spotify_auth_url() -> dict[str, Any]:
    if not is_spotify_configured():
        return {"configured": False, "auth_url": None}
    return {"configured": True, "auth_url": get_spotify_authorize_url()}


@app.get("/api/spotify/callback")
async def spotify_oauth_callback(code: str = Query(...)) -> dict[str, Any]:
    """OAuth 2.0 callback endpoint that exchanges authorization `code` for Spotify access & refresh tokens."""
    try:
        token_data = await exchange_code_for_token(code)
        return {
            "status": "authenticated",
            "scope": token_data.get("scope"),
            "expires_in": token_data.get("expires_in"),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/profile")
def get_profile() -> dict[str, Any]:
    return db.get_user_profile()


@app.put("/api/profile")
def update_profile(payload: ProfileUpdateRequest) -> dict[str, Any]:
    return db.update_user_profile(
        known_chords=payload.known_chords,
        learning_chords=payload.learning_chords,
        avoid_barre=payload.avoid_barre,
    )


@app.get("/api/playlists")
async def api_list_playlists(limit: int = Query(20, ge=1, le=50)) -> list[dict[str, Any]]:
    try:
        return await fetch_user_playlists(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/playlists/{playlist_id}/tracks")
async def api_get_playlist_tracks(
    playlist_id: str, limit: int = Query(30, ge=1, le=100)
) -> list[dict[str, Any]]:
    try:
        return await fetch_playlist_tracks(playlist_id=playlist_id, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/playlists/{playlist_id}/recommendations")
async def api_recommend_playlist_tracks(
    playlist_id: str,
    limit: int = Query(15, ge=1, le=50),
    target_level: str | None = Query(None, description="Filtrar por Fácil, Médio ou Difícil"),
    avoid_barre: bool = Query(True),
) -> list[dict[str, Any]]:
    """Index (in lightweight ~200B mode) and evaluate playlist tracks concurrently."""
    try:
        return await recommend_playlist_tracks(
            playlist_id=playlist_id,
            limit=limit,
            target_level=target_level,
            avoid_barre=avoid_barre,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/chords")
async def api_fetch_chord_sheet(
    artist: str = Query(..., description="Nome do artista"),
    track_name: str = Query(..., description="Nome da música"),
    semitones: int = Query(0, ge=-11, le=11, description="Transpor cifra em N semitons"),
) -> dict[str, Any]:
    sheet = await fetch_chord_sheet_async(artist=artist, track_name=track_name, save_raw_sheet=True)
    profile = db.get_user_profile()

    chord_freqs: dict[str, int] = sheet.get("chord_frequencies", {})
    unique_chords: list[str] = sheet.get("unique_chords", [])

    if semitones != 0 and chord_freqs:
        transposed_freqs: dict[str, int] = {}
        for ch, count in chord_freqs.items():
            t_ch = transpose_chord(ch, semitones)
            transposed_freqs[t_ch] = transposed_freqs.get(t_ch, 0) + count
        chord_freqs = transposed_freqs
        unique_chords = list(transposed_freqs.keys())

    evaluation = evaluate_song_difficulty(
        unique_chords=unique_chords,
        known_chords=profile["known_chords"],
        avoid_barre=profile["avoid_barre"],
        chord_frequencies=chord_freqs,
        avg_changes_per_line=sheet.get("avg_changes_per_line", 2.0),
        has_tabs=sheet.get("has_tabs", False),
    )
    return {
        **sheet,
        "unique_chords": unique_chords,
        "chord_frequencies": chord_freqs,
        "semitones": semitones,
        "evaluation": evaluation,
    }


@app.post("/api/evaluate")
def api_evaluate_difficulty(payload: EvaluateRequest) -> dict[str, Any]:
    profile = db.get_user_profile()
    effective_known = (
        payload.known_chords if payload.known_chords is not None else profile["known_chords"]
    )
    return evaluate_song_difficulty(
        unique_chords=payload.unique_chords,
        known_chords=effective_known,
        avoid_barre=payload.avoid_barre,
        chord_frequencies=payload.chord_frequencies,
        avg_changes_per_line=payload.avg_changes_per_line,
    )


@app.post("/api/transpose")
def api_transpose_chords(payload: TransposeRequest) -> dict[str, Any]:
    transposed = [transpose_chord(c, payload.semitones) for c in payload.chords]
    profile = db.get_user_profile()
    evaluation = evaluate_song_difficulty(
        unique_chords=transposed,
        known_chords=profile["known_chords"],
        avoid_barre=profile["avoid_barre"],
    )
    return {
        "original_chords": payload.chords,
        "semitones": payload.semitones,
        "transposed_chords": transposed,
        "evaluation": evaluation,
    }


@app.get("/api/catalog")
def api_search_indexed_catalog(
    max_chords: int | None = Query(None, description="Máximo de acordes únicos"),
    avoid_barre: bool | None = Query(None, description="Apenas sem pestana ou com capotraste fácil"),
    level: str | None = Query(None, description="Filtrar por Fácil, Médio ou Difícil"),
    search: str | None = Query(None, description="Busca por artista ou música"),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Instant SQL query (<2ms) over already-indexed songs without hitting Cifra Club."""
    songs = db.query_indexed_songs(
        max_chords=max_chords,
        avoid_barre=avoid_barre,
        search=search,
        limit=limit,
    )
    profile = db.get_user_profile()
    enriched: list[dict[str, Any]] = []
    for song in songs:
        ev = evaluate_song_difficulty(
            unique_chords=song["unique_chords"],
            known_chords=profile["known_chords"],
            avoid_barre=profile["avoid_barre"],
            chord_frequencies=song["chord_frequencies"],
            avg_changes_per_line=song["avg_changes_per_line"],
            has_tabs=song["has_tabs"],
        )
        item = {**song, **ev}
        if level and item["level"].lower() != level.strip().lower():
            continue
        enriched.append(item)

    enriched.sort(key=lambda x: x["score"], reverse=True)
    return enriched
