"""Direct Async Spotify Web API Client using httpx (no Spotipy dependency).

Implements official Spotify Web API endpoints:
- OAuth 2.0 Authorization Code Flow + Refresh Token (`https://accounts.spotify.com/api/token`)
- Client Credentials Flow (`https://accounts.spotify.com/api/token`) for public playlists
- `GET https://api.spotify.com/v1/me/playlists`
- `GET https://api.spotify.com/v1/playlists/{playlist_id}/tracks`
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from src.config import settings

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_SCOPE = "playlist-read-private playlist-read-collaborative user-library-read"

# Demo playlists available immediately before Spotify OAuth credentials are configured
DEMO_PLAYLISTS: list[dict[str, Any]] = [
    {
        "id": "demo_rock_nacional",
        "name": "🎸 Rock Nacional & MPB Acústico (Demo)",
        "total_tracks": 8,
        "is_demo": True,
    },
    {
        "id": "demo_pop_rock_intl",
        "name": "🌍 Acoustic Pop & Classic Rock (Demo)",
        "total_tracks": 6,
        "is_demo": True,
    },
]

DEMO_PLAYLIST_TRACKS: dict[str, list[dict[str, Any]]] = {
    "demo_rock_nacional": [
        {"track_id": "demo_1", "name": "Tempo Perdido", "artist": "Legião Urbana", "album": "Dois"},
        {"track_id": "demo_2", "name": "Que País É Este", "artist": "Legião Urbana", "album": "Que País É Este"},
        {"track_id": "demo_3", "name": "Pais e Filhos", "artist": "Legião Urbana", "album": "As Quatro Estações"},
        {"track_id": "demo_4", "name": "Primeiros Erros (Chove) - Ao Vivo", "artist": "Capital Inicial", "album": "Acústico MTV"},
        {"track_id": "demo_5", "name": "À Sua Maneira", "artist": "Capital Inicial", "album": "Rosas e Vinho Tinto"},
        {"track_id": "demo_6", "name": "Anna Júlia", "artist": "Los Hermanos", "album": "Los Hermanos"},
        {"track_id": "demo_7", "name": "Meu Erro", "artist": "Os Paralamas do Sucesso", "album": "O Passo do Lui"},
        {"track_id": "demo_8", "name": "Anunciação", "artist": "Alceu Valença", "album": "Cinco Sentidos"},
    ],
    "demo_pop_rock_intl": [
        {"track_id": "demo_10", "name": "Knockin' On Heaven's Door", "artist": "Bob Dylan", "album": "Pat Garrett & Billy the Kid"},
        {"track_id": "demo_11", "name": "Wonderwall - Remastered", "artist": "Oasis", "album": "(What's the Story) Morning Glory?"},
        {"track_id": "demo_12", "name": "Zombie", "artist": "The Cranberries", "album": "No Need to Argue"},
        {"track_id": "demo_13", "name": "Wish You Were Here", "artist": "Pink Floyd", "album": "Wish You Were Here"},
        {"track_id": "demo_14", "name": "Creep", "artist": "Radiohead", "album": "Pablo Honey"},
        {"track_id": "demo_15", "name": "The Scientist", "artist": "Coldplay", "album": "A Rush of Blood to the Head"},
    ],
}


def is_spotify_configured() -> bool:
    return bool(
        settings.SPOTIFY_CLIENT_ID
        and settings.SPOTIFY_CLIENT_SECRET
        and settings.SPOTIFY_CLIENT_ID != "seu_client_id"
    )


def _basic_auth_header() -> str:
    raw = f"{settings.SPOTIFY_CLIENT_ID}:{settings.SPOTIFY_CLIENT_SECRET}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _load_cached_token() -> dict[str, Any] | None:
    path = Path(settings.SPOTIFY_CACHE_PATH)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cached_token(token_data: dict[str, Any]) -> dict[str, Any]:
    path = Path(settings.SPOTIFY_CACHE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    if "expires_in" in token_data and "expires_at" not in token_data:
        token_data["expires_at"] = int(time.time()) + int(token_data["expires_in"]) - 30
    path.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
    return token_data


def get_spotify_authorize_url() -> str:
    """Build the OAuth2 Authorization URL for user login."""
    params = {
        "client_id": settings.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": settings.SPOTIFY_REDIRECT_URI,
        "scope": SPOTIFY_SCOPE,
    }
    return f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Exchange OAuth2 authorization `code` for access_token and refresh_token."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            SPOTIFY_TOKEN_URL,
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.SPOTIFY_REDIRECT_URI,
            },
        )
        resp.raise_for_status()
        return _save_cached_token(resp.json())


async def get_valid_access_token(allow_client_credentials: bool = True) -> str:
    """Return a valid Spotify Bearer token (refreshing user token or falling back to Client Credentials)."""
    if not is_spotify_configured():
        raise RuntimeError(
            "Spotify não configurado. Defina SPOTIFY_CLIENT_ID e SPOTIFY_CLIENT_SECRET no .env."
        )

    cached = _load_cached_token()
    now = int(time.time())

    if cached and cached.get("access_token") and cached.get("expires_at", 0) > now:
        return str(cached["access_token"])

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. If we have a refresh_token, refresh the user OAuth session automatically
        if cached and cached.get("refresh_token"):
            resp = await client.post(
                SPOTIFY_TOKEN_URL,
                headers={
                    "Authorization": _basic_auth_header(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": cached["refresh_token"],
                },
            )
            if resp.status_code == 200:
                new_data = resp.json()
                if "refresh_token" not in new_data:
                    new_data["refresh_token"] = cached["refresh_token"]
                saved = _save_cached_token(new_data)
                return str(saved["access_token"])

        # 2. Fallback to Client Credentials tutorial flow (works for any public playlist without user login!)
        if allow_client_credentials:
            resp = await client.post(
                SPOTIFY_TOKEN_URL,
                headers={
                    "Authorization": _basic_auth_header(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials"},
            )
            resp.raise_for_status()
            token_payload = resp.json()
            return str(token_payload["access_token"])

    raise RuntimeError(
        "Sessão de usuário do Spotify não iniciada. Acesse /api/spotify/login para autenticar."
    )


async def fetch_user_playlists(limit: int = 20) -> list[dict[str, Any]]:
    """Return user's Spotify playlists via `GET /v1/me/playlists`, or demo playlists if unauthenticated."""
    if not is_spotify_configured():
        return DEMO_PLAYLISTS[:limit]

    cached = _load_cached_token()
    if not cached or not cached.get("refresh_token"):
        # Configured client_id/secret, but user hasn't logged in via OAuth for private playlists yet
        return DEMO_PLAYLISTS[:limit]

    token = await get_valid_access_token(allow_client_credentials=False)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{SPOTIFY_API_BASE}/me/playlists",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": limit},
        )
        resp.raise_for_status()
        data = resp.json()

    playlists: list[dict[str, Any]] = []
    for item in data.get("items", []):
        if not item:
            continue
        playlists.append(
            {
                "id": item["id"],
                "name": item["name"],
                "total_tracks": (item.get("tracks") or {}).get("total", 0),
                "is_demo": False,
            }
        )
    return playlists


async def fetch_playlist_tracks(playlist_id: str, limit: int = 30) -> list[dict[str, Any]]:
    """Return tracks for a specific Spotify playlist via `GET /v1/playlists/{id}/tracks`."""
    if playlist_id in DEMO_PLAYLIST_TRACKS:
        return DEMO_PLAYLIST_TRACKS[playlist_id][:limit]

    # Note: public playlists work directly with Client Credentials flow!
    token = await get_valid_access_token(allow_client_credentials=True)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/tracks",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "limit": limit,
                "fields": "items(track(id,name,album(name),artists(name)))",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    tracks: list[dict[str, Any]] = []
    for item in data.get("items", []):
        track = (item or {}).get("track")
        if not track:
            continue
        artists = track.get("artists") or []
        artist_name = artists[0]["name"] if artists else "Desconhecido"
        tracks.append(
            {
                "track_id": track.get("id") or "",
                "name": track.get("name") or "",
                "artist": artist_name,
                "album": (track.get("album") or {}).get("name", ""),
            }
        )
    return tracks
