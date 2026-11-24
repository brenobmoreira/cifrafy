"""Cifrafy FastMCP Server — mounted at `/mcp` inside FastAPI (and runnable via stdio)."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastmcp import FastMCP

from src.core import library, practice, study
from src.core.analyzer import evaluate_song_difficulty
from src.core.chord_scraper import analyze_tracks, fetch_chord_sheet_async, rank_recommendations
from src.core.spotify_client import fetch_playlist_tracks, fetch_user_playlists
from src.db import db

mcp = FastMCP("Cifrafy")


@mcp.tool()
async def list_user_playlists(limit: int = 20) -> list[dict[str, Any]]:
    """Retorna as playlists do usuário autenticado no Spotify e as playlists importadas."""
    return await fetch_user_playlists(limit=limit)


@mcp.tool()
async def get_playlist_tracks(playlist_id: str, limit: int = 30) -> list[dict[str, Any]]:
    """Busca as faixas de uma playlist específica do Spotify."""
    return await fetch_playlist_tracks(playlist_id=playlist_id, limit=limit)


@mcp.tool()
async def fetch_chord_sheet(artist: str, track_name: str, simplified: bool = False) -> dict[str, Any]:
    """Busca a cifra da música no Cifra Club (normal ou simplificada) e extrai os acordes e métricas."""
    result = await fetch_chord_sheet_async(
        artist=artist,
        track_name=track_name,
        simplified=simplified,
        require_sheet=True,
    )
    return {
        "status": result["status"],
        "error": result.get("error"),
        "retry_after_seconds": result.get("retry_after_seconds"),
        "url": result["url"],
        "artist": result["artist"],
        "track_name": result["track_name"],
        "is_simplified": result.get("is_simplified", False),
        "unique_chords": result["unique_chords"],
        "chord_frequencies": result.get("chord_frequencies", {}),
        "avg_changes_per_line": result.get("avg_changes_per_line", 0.0),
        "has_tabs": result.get("has_tabs", False),
        "raw_chord_sheet": result.get("raw_chord_sheet", ""),
        "sheet_available": bool(result.get("raw_chord_sheet")),
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
    simplified: bool = False,
) -> list[dict[str, Any]]:
    """Avalia as faixas de uma playlist, da mais fácil para a mais difícil.

    Músicas já indexadas não geram acesso ao Cifra Club. No máximo 15 faixas novas são buscadas
    por chamada. As demais voltam no fim da lista com `status` indicando o motivo.
    """
    tracks = await fetch_playlist_tracks(playlist_id=playlist_id, limit=limit)
    profile = db.get_user_profile()
    results = await analyze_tracks(
        tracks,
        simplified=simplified,
        known_chords=profile["known_chords"],
        avoid_barre=avoid_barre,
    )
    return rank_recommendations(results, target_level)


@mcp.tool()
def update_known_chords(add: list[str] | None = None, remove: list[str] | None = None) -> dict[str, Any]:
    """Adiciona ou remove acordes que o violonista já domina e devolve o perfil atualizado."""
    return library.update_known_chords(add, remove)


@mcp.tool()
async def suggest_next_chords(playlist_id: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Indica os acordes que, se aprendidos, deixam mais músicas indexadas fáceis. Não acessa o Cifra Club."""
    return await library.next_chords_to_learn(playlist_id, limit)


@mcp.tool()
def set_song_status(song_id: int, status: str) -> dict[str, Any]:
    """Marca uma música indexada como want (quero aprender), practicing (praticando) ou mastered (dominada)."""
    return library.set_song_status(song_id, status)


@mcp.tool()
def list_repertoire(status: str | None = None) -> list[dict[str, Any]]:
    """Lista o repertório, opcionalmente filtrado por want, practicing ou mastered."""
    return library.list_repertoire(status)


@mcp.tool()
async def get_learning_trail(playlist_id: str | None = None, limit: int = 5) -> dict[str, Any]:
    """Monta a ordem de acordes para aprender que mais libera músicas. Não altera o perfil nem acessa o Cifra Club."""
    return await practice.learning_trail(playlist_id, limit)


@mcp.tool()
async def get_practice_changes(
    playlist_id: str | None = None, song_id: int | None = None, limit: int = 8
) -> dict[str, Any]:
    """Lista as trocas de acordes mais importantes para treinar, priorizando o repertório em andamento."""
    return await practice.list_changes(playlist_id, song_id, limit)


@mcp.tool()
def log_practice_change(
    from_chord: str, to_chord: str, count: int, duration_seconds: int = 60, bpm: int | None = None
) -> dict[str, Any]:
    """Registra um treino cronometrado de troca entre dois acordes e devolve a evolução."""
    return practice.log_change(from_chord, to_chord, count, duration_seconds, bpm)


@mcp.tool()
async def build_practice_session(minutes: int = 20, playlist_id: str | None = None) -> dict[str, Any]:
    """Monta uma sessão de prática pronta com aquecimento, música dominada, música em andamento e acorde novo."""
    return await practice.build_session(minutes, playlist_id)


@mcp.tool()
async def get_listening_recommendations(
    source: str = "top", time_range: str = "short_term", limit: int = 20
) -> dict[str, Any]:
    """Recomenda o que tocar a partir das músicas mais ouvidas (top) ou recentes (recent) no Spotify."""
    if source not in ("top", "recent"):
        raise ValueError("source deve ser top ou recent.")
    if time_range not in ("short_term", "medium_term", "long_term"):
        raise ValueError("time_range deve ser short_term, medium_term ou long_term.")
    return await library.listening_recommendations(source, time_range, max(1, min(limit, 50)))


@mcp.tool()
def log_study_time(minutes: float, local_date: str | None = None, kind: str = "free") -> dict[str, Any]:
    """Registra minutos de estudo em um dia (YYYY-MM-DD, padrão hoje) e devolve o resumo."""
    return study.log_session(round(minutes * 60), local_date or date.today().isoformat(), kind)


@mcp.tool()
def get_study_summary(today: str | None = None) -> dict[str, Any]:
    """Mostra o tempo estudado hoje, na semana, no mês, a sequência de dias e a meta diária."""
    return study.summary(today or date.today().isoformat())


if __name__ == "__main__":
    mcp.run()
