"""Pure REST Backend API (FastAPI) for Guitar Practice Assistant.

Completely decoupled from both the MCP adapter and the Web Frontend.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from src.config import settings
from src.core import library, practice, study
from src.core.analyzer import evaluate_song_difficulty, structure_sheet, transpose_chord, transpose_sheet
from src.core.chord_scraper import (
    analyze_tracks,
    cifraclub_limiter,
    fetch_chord_sheet_async,
    rank_recommendations,
    summarize_batch,
)
from src.core.spotify_client import (
    add_playlist_by_url_or_id,
    consume_oauth_state,
    exchange_code_for_token,
    fetch_playlist_tracks,
    fetch_user_playlists,
    get_spotify_authorize_url,
    is_spotify_configured,
    is_spotify_user_logged_in,
)
from src.db import db
from src.mcp_server import mcp

logger = logging.getLogger(__name__)

mcp_http_app = mcp.http_app(path="/")

app = FastAPI(
    title="Cifrafy API & MCP Server",
    description="Backend unificado do Cifrafy expondo a REST API em `/api` e o servidor MCP (Model Context Protocol) em `/mcp`.",
    version="0.2.0",
    lifespan=getattr(mcp_http_app, "lifespan", None),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "Authorization", "Mcp-Session-Id", "Mcp-Protocol-Version", "Last-Event-ID"],
    expose_headers=["Mcp-Session-Id"],
)
# Added last so it runs first, also for /mcp. Blocks DNS rebinding from malicious sites.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

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


class AddPlaylistRequest(BaseModel):
    url_or_id: str = Field(..., description="Link da playlist do Spotify ou ID")


class TrackSyncItem(BaseModel):
    track_id: str | None = None
    artist: str
    name: str


class BatchSyncRequest(BaseModel):
    tracks: list[TrackSyncItem] = Field(..., max_length=15, description="Até 15 faixas selecionadas por lote")
    simplified: bool = False
    avoid_barre: bool = True


class RepertoireUpdateRequest(BaseModel):
    status: Literal["want", "practicing", "mastered"]
    preferred_semitones: int | None = Field(None, ge=-11, le=11)
    preferred_capo: int | None = Field(None, ge=0, le=11)


class PracticeChangeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_chord: str = Field(..., alias="from")
    to_chord: str = Field(..., alias="to")
    count: int = Field(..., ge=practice.MIN_COUNT, le=practice.MAX_COUNT)
    duration_seconds: int = Field(60, ge=practice.MIN_DURATION, le=practice.MAX_DURATION)
    bpm: int | None = Field(None, ge=practice.MIN_BPM, le=practice.MAX_BPM)


class StudySessionRequest(BaseModel):
    duration_seconds: int = Field(..., ge=study.MIN_SECONDS, le=study.MAX_SECONDS)
    local_date: str = Field(..., description="Data local do navegador, YYYY-MM-DD")
    kind: Literal["free", "song", "changes", "session"] = "free"
    song_id: int | None = None


class StudyGoalRequest(BaseModel):
    minutes: int = Field(..., ge=study.MIN_GOAL, le=study.MAX_GOAL)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "cifrafy-backend"}


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    profile = db.get_user_profile()
    indexed_count = db.count_indexed_songs()
    return {
        "spotify_configured": is_spotify_configured(),
        "spotify_logged_in": is_spotify_user_logged_in(),
        "indexed_songs_count": indexed_count,
        "cifraclub_cooldown_seconds": cifraclub_limiter.cooldown_remaining(),
        "profile": profile,
    }


@app.get("/api/spotify/auth-url")
def get_spotify_auth_url() -> dict[str, Any]:
    if not is_spotify_configured():
        return {"configured": False, "auth_url": None}
    return {"configured": True, "auth_url": get_spotify_authorize_url()}


@app.get("/api/spotify/callback")
async def spotify_oauth_callback(
    code: str | None = Query(None),
    error: str | None = Query(None),
    state: str | None = Query(None),
) -> RedirectResponse:
    """Finish the Spotify login and send the user back to the app."""
    if not consume_oauth_state(state):
        result = "error"
    elif error == "access_denied":
        result = "denied"
    elif error or not code:
        result = "error"
    else:
        try:
            await exchange_code_for_token(code)
            result = "connected"
        except Exception as exc:
            logger.warning("Falha ao concluir o login do Spotify: %s", type(exc).__name__)
            result = "error"
    return RedirectResponse(f"{settings.FRONTEND_URL}/?spotify={result}", status_code=303)


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


@app.post("/api/playlists")
async def api_add_playlist(payload: AddPlaylistRequest) -> dict[str, Any]:
    """Adiciona qualquer playlist do Spotify colando o link (https://open.spotify.com/playlist/...) ou ID."""
    try:
        return await add_playlist_by_url_or_id(payload.url_or_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/playlists/{playlist_id}")
def api_delete_playlist(playlist_id: str) -> dict[str, str]:
    db.delete_saved_playlist(playlist_id)
    return {"status": "deleted", "id": playlist_id}


@app.get("/api/playlists/{playlist_id}/tracks")
async def api_get_playlist_tracks(
    playlist_id: str,
    limit: int = Query(100, ge=1, le=200),
    simplified: bool = Query(False),
) -> list[dict[str, Any]]:
    """Lista todas as faixas da playlist (rápido, sem bater no Cifra Club) e cruza com o que já está no SQLite."""
    try:
        return await library.playlist_tracks_with_status(playlist_id, limit=limit, simplified=simplified)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/playlists/sync-selected")
async def api_sync_selected_tracks(payload: BatchSyncRequest) -> dict[str, Any]:
    """Analisa os acordes das faixas selecionadas (máx 15) e informa o resultado de cada uma."""
    profile = db.get_user_profile()
    results = await analyze_tracks(
        [item.model_dump() for item in payload.tracks],
        simplified=payload.simplified,
        known_chords=profile["known_chords"],
        avoid_barre=payload.avoid_barre,
    )
    return {
        "results": results,
        "summary": summarize_batch(results),
        "retry_after_seconds": cifraclub_limiter.cooldown_remaining(),
    }


@app.get("/api/playlists/{playlist_id}/recommendations")
async def api_recommend_playlist_tracks(
    playlist_id: str,
    limit: int = Query(15, ge=1, le=50),
    target_level: str | None = Query(None, description="Filtrar por Fácil, Médio ou Difícil"),
    avoid_barre: bool = Query(True),
    simplified: bool = Query(False, description="Priorizar versão simplificada do Cifra Club"),
) -> list[dict[str, Any]]:
    """Avalia a playlist usando o índice local e no máximo 15 novas buscas no Cifra Club."""
    try:
        tracks = await fetch_playlist_tracks(playlist_id=playlist_id, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    profile = db.get_user_profile()
    results = await analyze_tracks(
        tracks,
        simplified=simplified,
        known_chords=profile["known_chords"],
        avoid_barre=avoid_barre,
    )
    return rank_recommendations(results, target_level)


@app.get("/api/chords")
async def api_fetch_chord_sheet(
    artist: str = Query(..., description="Nome do artista"),
    track_name: str = Query(..., description="Nome da música"),
    semitones: int = Query(0, ge=-11, le=11, description="Transpor cifra em N semitons"),
    simplified: bool = Query(False, description="Buscar versão simplificada (simplificada.html)"),
) -> dict[str, Any]:
    sheet = await fetch_chord_sheet_async(
        artist=artist,
        track_name=track_name,
        simplified=simplified,
        require_sheet=True,
    )
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
    raw_sheet = sheet.get("raw_chord_sheet") or ""
    song_id = sheet.get("id")
    if song_id is None and sheet.get("found") and sheet.get("artist_slug"):
        stored = db.get_song_by_slugs(sheet["artist_slug"], sheet["track_slug"], include_sheet=False)
        song_id = stored["id"] if stored else None
    return {
        **sheet,
        "song_id": song_id,
        "repertoire": db.get_repertoire_entry(song_id) if song_id is not None else None,
        "raw_chord_sheet": transpose_sheet(raw_sheet, semitones) if semitones and raw_sheet else raw_sheet,
        "sheet_lines": structure_sheet(raw_sheet, semitones),
        "sheet_available": bool(raw_sheet),
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
    with_capo: bool = Query(False, description="Com level=Fácil, inclui músicas que ficam fáceis com capotraste"),
    search: str | None = Query(None, description="Busca por artista ou música"),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Consulta as músicas indexadas sem acessar o Cifra Club."""
    return library.search_catalog(
        max_chords=max_chords,
        avoid_barre=avoid_barre,
        level=level,
        with_capo=with_capo,
        search=search,
        limit=limit,
    )


@app.get("/api/learning/next-chords")
async def api_next_chords(
    playlist_id: str | None = Query(None, description="Considerar só as músicas indexadas desta playlist"),
    limit: int = Query(5, ge=1, le=20),
) -> list[dict[str, Any]]:
    """Indica quais acordes aprender para deixar mais músicas fáceis, sem acessar o Cifra Club."""
    try:
        return await library.next_chords_to_learn(playlist_id, limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/repertoire")
def api_list_repertoire(
    status: Literal["want", "practicing", "mastered"] | None = Query(None),
) -> list[dict[str, Any]]:
    return library.list_repertoire(status)


@app.put("/api/repertoire/{song_id}")
def api_set_repertoire(song_id: int, payload: RepertoireUpdateRequest) -> dict[str, Any]:
    try:
        return library.set_song_status(
            song_id, payload.status, payload.preferred_semitones, payload.preferred_capo
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/repertoire/{song_id}")
def api_delete_repertoire(song_id: int) -> dict[str, Any]:
    if not db.delete_repertoire_entry(song_id):
        raise HTTPException(status_code=404, detail=f"Música {song_id} não está no repertório.")
    return {"status": "deleted", "song_id": song_id}


@app.get("/api/practice/trail")
async def api_practice_trail(
    playlist_id: str | None = Query(None, description="Considerar só as músicas indexadas desta playlist"),
    limit: int = Query(5, ge=1, le=20),
) -> dict[str, Any]:
    """Monta a ordem de acordes que mais libera músicas, sem alterar o perfil nem acessar o Cifra Club."""
    try:
        return await practice.learning_trail(playlist_id, limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/practice/changes")
async def api_practice_changes(
    playlist_id: str | None = Query(None, description="Considerar só as músicas desta playlist"),
    song_id: int | None = Query(None, description="Considerar só esta música (tem prioridade sobre a playlist)"),
    limit: int = Query(8, ge=1, le=50),
) -> dict[str, Any]:
    """Lista as trocas de acordes mais importantes para treinar agora."""
    try:
        return await practice.list_changes(playlist_id, song_id, limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/practice/changes")
def api_log_practice_change(payload: PracticeChangeRequest) -> dict[str, Any]:
    try:
        return practice.log_change(
            payload.from_chord, payload.to_chord, payload.count, payload.duration_seconds, payload.bpm
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/practice/session")
async def api_practice_session(
    minutes: int = Query(20, ge=practice.MIN_SESSION_MINUTES, le=practice.MAX_SESSION_MINUTES),
    playlist_id: str | None = Query(None, description="Considerar só as músicas desta playlist"),
) -> dict[str, Any]:
    """Monta uma sessão de prática pronta para o tempo disponível."""
    try:
        return await practice.build_session(minutes, playlist_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/arrangement")
async def api_arrangement(
    artist: str = Query(...),
    track_name: str = Query(...),
    simplified: bool = Query(False),
) -> dict[str, Any]:
    """Adapta a música ao nível do usuário, com capotraste e substituições conservadoras."""
    return await library.adapted_arrangement(artist, track_name, simplified)


@app.get("/api/recommendations/listening")
async def api_listening_recommendations(
    source: Literal["top", "recent"] = Query("top"),
    time_range: Literal["short_term", "medium_term", "long_term"] = Query("short_term"),
    limit: int = Query(20, ge=1, le=50),
) -> dict[str, Any]:
    """Recomenda a partir do que o usuário ouve no Spotify, sem acessar o Cifra Club."""
    try:
        return await library.listening_recommendations(source, time_range, limit)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Não foi possível ler seu histórico no Spotify.") from exc


@app.post("/api/study/sessions")
def api_log_study_session(payload: StudySessionRequest) -> dict[str, Any]:
    try:
        return study.log_session(payload.duration_seconds, payload.local_date, payload.kind, payload.song_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/study/summary")
def api_study_summary(
    today: str = Query(..., description="Data local do navegador, YYYY-MM-DD"),
    days: int = Query(30, ge=study.MIN_DAYS, le=study.MAX_DAYS),
) -> dict[str, Any]:
    try:
        return study.summary(today, days)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/study/goal")
def api_set_study_goal(payload: StudyGoalRequest) -> dict[str, int]:
    return study.set_goal(payload.minutes)


@app.get("/api/practice/changes/history")
def api_practice_change_history(
    from_chord: str = Query(..., alias="from"),
    to_chord: str = Query(..., alias="to"),
    limit: int = Query(30, ge=1, le=200),
) -> dict[str, Any]:
    try:
        return practice.change_history(from_chord, to_chord, limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
