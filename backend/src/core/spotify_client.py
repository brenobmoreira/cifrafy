"""Direct Async Spotify Web API Client using httpx (no Spotipy dependency).

Implements official Spotify Web API endpoints:
- OAuth 2.0 Authorization Code Flow + Refresh Token (`https://accounts.spotify.com/api/token`)
- Client Credentials Flow (`https://accounts.spotify.com/api/token`) for public playlists
- `GET https://api.spotify.com/v1/me/playlists`
- `GET https://api.spotify.com/v1/playlists/{playlist_id}/items`
"""

from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from src.config import settings
from src.db import db

logger = logging.getLogger(__name__)

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_SCOPE = (
    "playlist-read-private playlist-read-collaborative user-library-read "
    "user-top-read user-read-recently-played"
)
SPOTIFY_MAX_PAGE_SIZE = 50

OAUTH_STATE_TTL_SECONDS = 600

# Pending OAuth states, valid for a single callback.
_pending_oauth_states: dict[str, float] = {}


def _build_client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(**kwargs)


def _playlist_total(playlist: dict[str, Any]) -> int | None:
    """Read the track count from the current `items` field or the deprecated `tracks` field."""
    for field in ("items", "tracks"):
        section = playlist.get(field)
        if isinstance(section, dict) and section.get("total") is not None:
            return int(section["total"])
    return None


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


def _create_oauth_state() -> str:
    now = time.time()
    for state, expires_at in list(_pending_oauth_states.items()):
        if expires_at <= now:
            del _pending_oauth_states[state]
    state = secrets.token_urlsafe(24)
    _pending_oauth_states[state] = now + OAUTH_STATE_TTL_SECONDS
    return state


def consume_oauth_state(state: str | None) -> bool:
    """Accept a state generated here, once and before it expires."""
    if not state:
        return False
    expires_at = _pending_oauth_states.pop(state, None)
    return expires_at is not None and expires_at > time.time()


def get_spotify_authorize_url() -> str:
    """Build the OAuth2 Authorization URL for user login."""
    params = {
        "state": _create_oauth_state(),
        "client_id": settings.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": settings.SPOTIFY_REDIRECT_URI,
        "scope": SPOTIFY_SCOPE,
    }
    return f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Exchange OAuth2 authorization `code` for access_token and refresh_token."""
    async with _build_client(timeout=10.0) as client:
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

    async with _build_client(timeout=10.0) as client:
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
        "Sessão de usuário do Spotify não iniciada. Conecte sua conta pelo botão de login do Spotify no app."
    )


def extract_playlist_id(url_or_id: str) -> str:
    """Extract Spotify playlist ID from a full URL, URI, or raw ID."""
    cleaned = url_or_id.strip()
    match = re.search(r"playlist[/:]([a-zA-Z0-9]+)", cleaned)
    if match:
        return match.group(1)
    return cleaned.split("?")[0]


def is_spotify_user_logged_in() -> bool:
    cached = _load_cached_token()
    return bool(cached and cached.get("refresh_token"))


async def _fetch_public_playlist_via_embed(playlist_id: str) -> dict[str, Any]:
    """Fetch public Spotify playlist metadata and tracks via Spotify Embed endpoint (works without API keys!)."""
    embed_url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    async with _build_client(headers=headers, follow_redirects=True, timeout=12.0) as client:
        resp = await client.get(embed_url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    next_data_script = soup.find("script", id="__NEXT_DATA__")
    if not next_data_script or not next_data_script.string:
        raise RuntimeError("Não foi possível ler os dados da playlist pública do Spotify.")

    payload = json.loads(next_data_script.string)
    entity = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("data", {})
        .get("entity", {})
    )
    if not entity:
        raise RuntimeError("Playlist não encontrada ou privada no Spotify.")

    name = entity.get("name") or entity.get("title") or f"Playlist {playlist_id}"
    track_list = entity.get("trackList") or []
    tracks: list[dict[str, Any]] = []
    for idx, item in enumerate(track_list):
        uri = item.get("uri") or ""
        t_id = uri.split(":")[-1] if ":" in uri else f"{playlist_id}_{idx}"
        tracks.append(
            {
                "track_id": t_id,
                "name": item.get("title") or "",
                "artist": (item.get("subtitle") or "Desconhecido").replace("\xa0", " "),
                "album": "",
            }
        )
    return {
        "id": playlist_id,
        "name": name,
        "total_tracks": len(tracks),
        "tracks": tracks,
    }


async def add_playlist_by_url_or_id(url_or_id: str) -> dict[str, Any]:
    """Import a playlist by Spotify URL or ID and persist it in SQLite."""
    playlist_id = extract_playlist_id(url_or_id)
    if not playlist_id:
        raise ValueError("URL ou ID de playlist inválido.")

    # Try official Web API first if credentials are configured
    if is_spotify_configured():
        try:
            token = await get_valid_access_token(allow_client_credentials=True)
            async with _build_client(timeout=10.0) as client:
                resp = await client.get(
                    f"{SPOTIFY_API_BASE}/playlists/{playlist_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if resp.status_code == 200:
                data = resp.json()
                total = _playlist_total(data)
                # Playlists from other users may come without a track count.
                if total is not None:
                    return db.upsert_saved_playlist(
                        playlist_id=data["id"],
                        name=data.get("name") or f"Playlist {playlist_id}",
                        total_tracks=total,
                        source_url=url_or_id,
                    )
                logger.info("Spotify não informou o total da playlist %s; usando embed.", playlist_id)
            else:
                logger.warning("Spotify respondeu %s ao ler a playlist %s.", resp.status_code, playlist_id)
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
            logger.warning("Falha na API do Spotify para a playlist %s: %s", playlist_id, exc)

    # Fallback: fetch public playlist via Spotify Embed (works even with ZERO credentials configured!)
    embed_data = await _fetch_public_playlist_via_embed(playlist_id)
    return db.upsert_saved_playlist(
        playlist_id=playlist_id,
        name=embed_data["name"],
        total_tracks=embed_data["total_tracks"],
        source_url=url_or_id,
    )


async def fetch_user_playlists(limit: int = 20) -> list[dict[str, Any]]:
    """Return the user's saved playlists plus the Spotify account playlists."""
    saved = db.list_saved_playlists()
    seen_ids = {p["id"] for p in saved}
    playlists: list[dict[str, Any]] = list(saved)

    if is_spotify_configured() and is_spotify_user_logged_in():
        try:
            token = await get_valid_access_token(allow_client_credentials=False)
            async with _build_client(timeout=10.0) as client:
                resp = await client.get(
                    f"{SPOTIFY_API_BASE}/me/playlists",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"limit": min(limit, SPOTIFY_MAX_PAGE_SIZE)},
                )
                resp.raise_for_status()
                data = resp.json()

            for item in data.get("items", []):
                if not item or item["id"] in seen_ids:
                    continue
                seen_ids.add(item["id"])
                playlists.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "total_tracks": _playlist_total(item) or 0,
                    }
                )
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
            logger.warning("Falha ao listar playlists da conta Spotify: %s", exc)

    return playlists[:limit]


def _parse_playlist_entry(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert a playlist entry, accepting the current `item` and the legacy `track` field."""
    track = (entry or {}).get("item") or (entry or {}).get("track")
    if not track or track.get("type") == "episode" or not track.get("name"):
        return None
    artists = track.get("artists") or []
    return {
        "track_id": track.get("id") or "",
        "name": track["name"],
        "artist": artists[0]["name"] if artists else "Desconhecido",
        "album": (track.get("album") or {}).get("name", ""),
    }


async def fetch_listening_tracks(
    source: str = "top", time_range: str = "short_term", limit: int = 20
) -> dict[str, Any]:
    """Read the user's top or recently played tracks, without repeating a track."""
    if not (is_spotify_configured() and is_spotify_user_logged_in()):
        return {"connected": False, "needs_reconnect": False, "tracks": []}

    try:
        token = await get_valid_access_token(allow_client_credentials=False)
    except (RuntimeError, httpx.HTTPError) as exc:
        logger.warning("Sessão do Spotify inválida: %s", exc)
        return {"connected": True, "needs_reconnect": True, "tracks": []}

    if source == "recent":
        url = f"{SPOTIFY_API_BASE}/me/player/recently-played"
        # Repeated plays are removed below, so read the whole window.
        params: dict[str, Any] = {"limit": SPOTIFY_MAX_PAGE_SIZE}
    else:
        url = f"{SPOTIFY_API_BASE}/me/top/tracks"
        params = {"limit": min(limit, SPOTIFY_MAX_PAGE_SIZE), "time_range": time_range}

    async with _build_client(timeout=10.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
    if resp.status_code in (401, 403):
        logger.warning("Spotify respondeu %s ao ler o histórico de escuta.", resp.status_code)
        return {"connected": True, "needs_reconnect": True, "tracks": []}
    resp.raise_for_status()

    tracks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in resp.json().get("items") or []:
        parsed = _parse_playlist_entry(entry if source == "recent" else {"item": entry})
        if not parsed:
            continue
        key = parsed["track_id"] or f"{parsed['artist']}|{parsed['name']}".casefold()
        if key not in seen:
            seen.add(key)
            tracks.append(parsed)
    return {"connected": True, "needs_reconnect": False, "tracks": tracks[:limit]}


async def _fetch_playlist_items(playlist_id: str, token: str, limit: int) -> list[dict[str, Any]]:
    """Page through the official endpoint, keeping what was read if a later page fails."""
    tracks: list[dict[str, Any]] = []
    offset = 0
    async with _build_client(timeout=10.0) as client:
        while len(tracks) < limit:
            resp = await client.get(
                f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/items",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": min(limit - len(tracks), SPOTIFY_MAX_PAGE_SIZE), "offset": offset},
            )
            if resp.status_code != 200:
                logger.warning(
                    "Spotify respondeu %s ao ler as faixas da playlist %s (offset %s).",
                    resp.status_code,
                    playlist_id,
                    offset,
                )
                break
            data = resp.json()
            entries = data.get("items") or []
            tracks.extend(t for t in map(_parse_playlist_entry, entries) if t)
            offset += len(entries)
            if not entries or not data.get("next"):
                break
    return tracks[:limit]


async def fetch_playlist_tracks(playlist_id: str, limit: int = 30) -> list[dict[str, Any]]:
    """Return playlist tracks via the Web API (own/collaborative) or the public Embed fallback."""
    playlist_id = extract_playlist_id(playlist_id)

    # The items endpoint is documented for the current user, so it needs a user session.
    if is_spotify_configured() and is_spotify_user_logged_in():
        try:
            token = await get_valid_access_token(allow_client_credentials=False)
            tracks = await _fetch_playlist_items(playlist_id, token, limit)
            if tracks:
                return tracks
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
            logger.warning("Falha na API do Spotify para a playlist %s: %s", playlist_id, exc)

    # Fallback: read public playlist tracks directly from Spotify Embed (no API keys required)
    embed_data = await _fetch_public_playlist_via_embed(playlist_id)
    return embed_data["tracks"][:limit]
