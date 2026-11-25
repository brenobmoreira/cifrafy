"""Cifra Club scraper: title cleanup, rate limiting, lookup, parsing and batch analysis."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sqlite3
import time
import unicodedata
import weakref
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from src.core.analyzer import clean_sheet_text, evaluate_song_difficulty, find_best_capo, is_barre_chord
from src.db import db

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
CIFRACLUB_BASE_URL = "https://www.cifraclub.com.br"
SOLR_URL = "https://solr.sscdn.co/cc/h2/"
SIMPLIFIED_SUFFIX = "__simplificada"
MAX_NEW_LOOKUPS_PER_BATCH = 15
SOLR_TITLE_MIN_SIMILARITY = 0.82
COOLDOWN_STATE_KEY = "cifraclub_blocked_until"

# Qualifiers that describe a release rather than the song. "live" only counts when it stands
# alone or introduces a venue, so titles such as "Live Forever" survive.
_RELEASE_QUALIFIER = re.compile(
    r"\b(?:remaster\w*|ao vivo|ac[úu]stic[oa]|acoustic|radio edit|version|vers[ãa]o|bonus"
    r"|deluxe|mono|stereo|re?mix|feat|ft|participa[çc][ãa]o)\b"
    r"|\bpart\."
    r"|\blive\s*$"
    r"|^live\s+(?:at|in|from|on|session)\b"
    r"|\blive\s+(?:at|in|from|on|version|session)\b"
    r"|^with\b"
    r"|^from\s+[\"“']"
    r"|^\d{4}\b",
    re.IGNORECASE,
)
_TRAILING_CREDIT = re.compile(r"\s+(?:feat\.?|ft\.?|part\.)\s+.*$", re.IGNORECASE)
_TITLE_SEPARATOR = re.compile(r"\s+[-–—]\s+")
_KEY_VALUE = re.compile(r"[A-G][#b]?m?")
_KEY_IN_TEXT = re.compile(r"\b(?i:tom)\s*:\s*([A-G][#b]?m?)(?![A-Za-z0-9#])")
_KEY_IN_SCRIPT = re.compile(r"[\"']tom[\"']\s*:\s*[\"']([A-G][#b]?m?)[\"']")
_SLUG = re.compile(r"[a-z0-9-]+")
_SPOTIFY_TRACK_ID = re.compile(r"[A-Za-z0-9]{22}")
_TAB_LINE = re.compile(r"^[eBGDAE]\|")
TAB_LINE_REGEX = re.compile(r"^[eBGDAE]\|[-0-9hpb/\\~x]+", re.MULTILINE)

# Artist names that contain a comma and must not be split into several artists.
_COMMA_ARTISTS = (
    "blood, sweat & tears",
    "crosby, stills, nash & young",
    "crosby, stills & nash",
    "earth, wind & fire",
    "emerson, lake & palmer",
    "tyler, the creator",
)


class CifraClubRateLimited(Exception):
    """Cifra Club refused the request, or a previous refusal is still cooling down."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"Cifra Club em espera por {retry_after_seconds}s.")
        self.retry_after_seconds = retry_after_seconds


class CifraClubUnavailable(Exception):
    """Temporary failure: the song may exist, so this must not be reported as not found."""


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max((moment - datetime.now(timezone.utc)).total_seconds(), 0.0)


class CifraClubLimiter:
    """Process-wide limiter: one request at a time, spaced, with a cooldown after 403/429.

    The cooldown end is a wall-clock time stored in SQLite, so a server restart cannot clear it.
    """

    def __init__(self, min_interval: float, default_cooldown: float) -> None:
        self.min_interval = min_interval
        self.default_cooldown = default_cooldown
        self._blocked_until = self._load_blocked_until()
        self._last_request_at = 0.0
        self._locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
            weakref.WeakKeyDictionary()
        )

    @staticmethod
    def _load_blocked_until() -> float:
        try:
            stored = db.get_service_value(COOLDOWN_STATE_KEY)
        except sqlite3.Error as exc:
            logger.warning("Não foi possível ler o cooldown salvo: %s", exc)
            return 0.0
        return stored if stored and stored > time.time() else 0.0

    def _save_blocked_until(self) -> None:
        try:
            db.set_service_value(COOLDOWN_STATE_KEY, self._blocked_until)
        except sqlite3.Error as exc:
            logger.warning("Não foi possível salvar o cooldown: %s", exc)

    def reset(self) -> None:
        self._blocked_until = 0.0
        self._last_request_at = 0.0
        try:
            db.delete_service_value(COOLDOWN_STATE_KEY)
        except sqlite3.Error as exc:
            logger.warning("Não foi possível limpar o cooldown: %s", exc)

    def cooldown_remaining(self) -> int:
        return max(math.ceil(self._blocked_until - time.time()), 0)

    async def get(self, client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        loop = asyncio.get_running_loop()
        lock = self._locks.setdefault(loop, asyncio.Lock())
        async with lock:
            remaining = self.cooldown_remaining()
            if remaining:
                raise CifraClubRateLimited(remaining)

            wait = self._last_request_at + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await client.get(url, **kwargs)
            finally:
                self._last_request_at = time.monotonic()

            if response.status_code in (403, 429):
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                cooldown = retry_after if retry_after else self.default_cooldown
                self._blocked_until = max(self._blocked_until, time.time() + cooldown)
                self._save_blocked_until()
                logger.warning(
                    "Cifra Club respondeu %s. Novas buscas pausadas por %.0fs.",
                    response.status_code,
                    cooldown,
                )
                raise CifraClubRateLimited(math.ceil(cooldown))
            return response


cifraclub_limiter = CifraClubLimiter(
    min_interval=float(os.getenv("CIFRACLUB_MIN_INTERVAL_SECONDS", "3.0")),
    default_cooldown=float(os.getenv("CIFRACLUB_COOLDOWN_SECONDS", "900")),
)


def _build_client() -> httpx.AsyncClient:
    # The CDN answers 403 to requests without the headers a browser sends on navigation.
    # The limiter keeps the volume low: one request at a time, spaced, and a pause after any 403/429.
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    return httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=10.0)


def clean_track_name(track_name: str) -> str:
    """Strip Spotify release metadata (remaster, ao vivo, feat., part.) from a track title."""
    original = track_name.strip()

    def drop_qualifier(match: re.Match[str]) -> str:
        return "" if _RELEASE_QUALIFIER.search(match.group(1).strip()) else match.group(0)

    cleaned = re.sub(r"\s*\(([^)]*)\)", drop_qualifier, original)
    cleaned = re.sub(r"\s*\[([^\]]*)\]", drop_qualifier, cleaned)

    parts = _TITLE_SEPARATOR.split(cleaned)
    while len(parts) > 1 and _RELEASE_QUALIFIER.search(parts[-1].strip()):
        parts.pop()
    cleaned = " - ".join(parts)
    cleaned = _TRAILING_CREDIT.sub("", cleaned)
    return cleaned.strip() or original


def slugify(text: str) -> str:
    """Convert artist or track name to Cifra Club URL slug."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    ascii_text = re.sub(r"['\".,!?()\[\]]", "", ascii_text)
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text)
    return ascii_text.strip("-")


def split_artists(artist: str) -> list[str]:
    """Split "A, B" into artists, main artist first, keeping names that contain a comma."""
    text = " ".join(artist.replace("\xa0", " ").split())
    protected: dict[str, str] = {}
    for idx, name in enumerate(_COMMA_ARTISTS):
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        match = pattern.search(text)
        if match:
            token = f"\x00{idx}\x00"
            protected[token] = match.group(0)
            text = pattern.sub(token, text, count=1)

    artists: list[str] = []
    for part in text.split(","):
        part = part.strip()
        for token, name in protected.items():
            part = part.replace(token, name)
        if part:
            artists.append(part)
    return artists or [artist.strip()]


def _strip_article(slug: str) -> str:
    return re.sub(r"^(?:the|os|as|o|a)-", "", slug)


def _same_artist(left: str, right: str) -> bool:
    return bool(left and right) and _strip_article(left) == _strip_article(right)


def _variant_slug(track_slug: str, simplified: bool) -> str:
    return f"{track_slug}{SIMPLIFIED_SUFFIX}" if simplified else track_slug


def _base_slug(track_slug: str) -> str:
    return track_slug.removesuffix(SIMPLIFIED_SUFFIX)


def _is_simplified_song(song: dict[str, Any]) -> bool:
    return song["track_slug"].endswith(SIMPLIFIED_SUFFIX) and bool(song.get("has_simplified"))


def _linkable_track_id(track_id: str | None) -> str | None:
    """Embed fallback ids are position based, so only real Spotify ids can be linked."""
    return track_id if track_id and _SPOTIFY_TRACK_ID.fullmatch(track_id) else None


def _artist_lookup_slugs(artist: str) -> list[str]:
    slugs = [slugify(split_artists(artist)[0]), slugify(artist)]
    return [slug for idx, slug in enumerate(slugs) if slug and slug not in slugs[:idx]]


def _extract_key(soup: BeautifulSoup, pre: Any, html: str) -> str | None:
    """Read the musical key from the known element, then from safe text and script fallbacks."""
    element = (
        soup.find(id="cifra_tom")
        or soup.find(attrs={"data-tom": True})
        or soup.find(attrs={"data-key": True})
        or soup.find(class_="cifra_tom")
    )
    if element:
        for attr in ("data-tom", "data-key"):
            value = str(element.get(attr) or "").strip()
            if _KEY_VALUE.fullmatch(value):
                return value
        link = element.find("a")
        text = (link or element).get_text(" ", strip=True)
        text = re.sub(r"(?i)^\s*tom\s*:?\s*", "", text).strip()
        if _KEY_VALUE.fullmatch(text):
            return text

    for text in (pre.get_text(" "), soup.get_text(" ")):
        match = _KEY_IN_TEXT.search(text)
        if match:
            return match.group(1)

    match = _KEY_IN_SCRIPT.search(html)
    return match.group(1) if match else None


def _changes_per_lyric_line(pre: Any) -> float:
    """Average chords on chord lines directly above a lyric, ignoring intro, solos and tabs."""
    fragment = BeautifulSoup(str(pre), "html.parser")
    for tab in fragment.find_all(class_="tablatura"):
        tab.decompose()
    for chord in fragment.find_all("b"):
        chord.replace_with("\x00")
    lines = [line.strip() for line in fragment.get_text().splitlines() if line.strip()]

    def is_chord_line(line: str) -> bool:
        return (
            "\x00" in line
            and not line.startswith("[")
            and not any(ch.isalpha() for ch in line.replace("\x00", ""))
        )

    def is_lyric_line(line: str) -> bool:
        return (
            "\x00" not in line
            and not line.startswith("[")
            and not _TAB_LINE.match(line)
            and any(ch.isalpha() for ch in line)
        )

    counts = [
        line.count("\x00")
        for idx, line in enumerate(lines[:-1])
        if is_chord_line(line) and is_lyric_line(lines[idx + 1])
    ]
    if not counts:
        counts = [line.count("\x00") for line in lines if is_chord_line(line)]
    return round(sum(counts) / len(counts), 2) if counts else 0.0


def parse_cifraclub_html(html: str, url: str) -> dict[str, Any] | None:
    """Extract chords, frequencies, line density, key, and raw chord sheet from Cifra Club HTML."""
    soup = BeautifulSoup(html, "html.parser")
    pre = soup.find("pre")
    if not pre:
        return None

    chord_frequencies: dict[str, int] = {}
    for tag in pre.find_all("b"):
        ch = tag.get_text(strip=True)
        if ch and re.match(r"^[A-G][#b]?", ch):
            chord_frequencies[ch] = chord_frequencies.get(ch, 0) + 1

    raw_sheet = clean_sheet_text(pre.get_text())
    if not chord_frequencies and not raw_sheet.strip():
        return None

    has_tabs = bool(TAB_LINE_REGEX.search(raw_sheet)) or soup.find("span", class_="tablatura") is not None
    title_el = soup.find("h1", class_="t1")
    artist_el = soup.find("h2", class_="t3") or soup.find("h3", class_="t3")

    return {
        "url": url,
        "artist": artist_el.get_text(strip=True) if artist_el else None,
        "track_name": title_el.get_text(strip=True) if title_el else None,
        "key": _extract_key(soup, pre, html),
        "unique_chords": list(chord_frequencies.keys()),
        "chord_frequencies": chord_frequencies,
        "total_chord_events": sum(chord_frequencies.values()),
        "avg_changes_per_line": _changes_per_lyric_line(pre),
        "has_tabs": has_tabs,
        "raw_chord_sheet": raw_sheet,
    }


async def _solr_docs(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    """Raw results of the Cifra Club autocomplete index."""
    try:
        resp = await cifraclub_limiter.get(client, SOLR_URL, params={"q": query}, timeout=6.0)
    except httpx.HTTPError as exc:
        raise CifraClubUnavailable(f"Falha na busca do Cifra Club: {exc}") from exc
    if resp.status_code != 200:
        raise CifraClubUnavailable(f"Busca do Cifra Club respondeu {resp.status_code}.")

    text = resp.text.strip()
    if "(" in text and text.endswith(")"):
        text = text[text.find("(") + 1 : -1]
    try:
        docs = json.loads(text).get("response", {}).get("docs", [])
    except (ValueError, AttributeError) as exc:
        raise CifraClubUnavailable("Resposta inesperada da busca do Cifra Club.") from exc
    return [doc for doc in docs if isinstance(doc, dict)] if isinstance(docs, list) else []


async def search_songs(query: str, limit: int = 10) -> list[dict[str, str]]:
    """Songs on Cifra Club matching free text. Artist-only results are left out.

    Raises CifraClubRateLimited or CifraClubUnavailable.
    """
    query = " ".join(query.split())
    if not query:
        return []
    async with _build_client() as client:
        docs = await _solr_docs(client, query)

    results: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for doc in docs:
        artist_slug = str(doc.get("d") or doc.get("dns") or "").strip("/")
        track_slug = str(doc.get("u") or doc.get("url") or "").strip("/")
        artist = str(doc.get("a") or "").strip()
        track_name = str(doc.get("m") or "").strip()
        if not (_SLUG.fullmatch(artist_slug) and _SLUG.fullmatch(track_slug) and artist and track_name):
            continue
        if (artist_slug, track_slug) in seen:
            continue
        seen.add((artist_slug, track_slug))
        results.append({"artist": artist, "track_name": track_name})
        if len(results) >= limit:
            break
    return results


async def _search_cifraclub_solr(
    client: httpx.AsyncClient, artists: list[str], track_name: str
) -> tuple[str, str] | None:
    """Search the autocomplete index, accepting only a result that matches artist and title."""
    docs = await _solr_docs(client, f"{artists[0]} {track_name}".strip())
    wanted_artists = [slugify(name) for name in artists]
    wanted_title = slugify(track_name)
    best: tuple[float, str, str] | None = None
    for doc in docs:
        artist_slug = str(doc.get("d") or doc.get("dns") or "").strip("/")
        track_slug = str(doc.get("u") or doc.get("url") or "").strip("/")
        if not _SLUG.fullmatch(artist_slug) or not _SLUG.fullmatch(track_slug):
            continue
        doc_artists = (artist_slug, slugify(str(doc.get("a") or "")))
        if not any(_same_artist(a, w) for a in doc_artists for w in wanted_artists):
            continue
        titles = (track_slug, slugify(str(doc.get("m") or "")))
        score = max(SequenceMatcher(None, title, wanted_title).ratio() for title in titles)
        if score >= SOLR_TITLE_MIN_SIMILARITY and (best is None or score > best[0]):
            best = (score, artist_slug, track_slug)
    return (best[1], best[2]) if best else None


async def _fetch_page(
    client: httpx.AsyncClient, artist_slug: str, track_slug: str, simplified: bool
) -> dict[str, Any] | None:
    suffix = "simplificada.html" if simplified else ""
    url = f"{CIFRACLUB_BASE_URL}/{artist_slug}/{track_slug}/{suffix}"
    try:
        resp = await cifraclub_limiter.get(client, url)
    except httpx.HTTPError as exc:
        raise CifraClubUnavailable(f"Falha ao acessar o Cifra Club: {exc}") from exc

    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise CifraClubUnavailable(f"Cifra Club respondeu {resp.status_code}.")

    final_url = str(resp.url)
    path = [part for part in urlparse(final_url).path.split("/") if part]
    if len(path) < 2 or not _same_artist(path[0], artist_slug):
        return None

    parsed = parse_cifraclub_html(resp.text, final_url)
    if not parsed or not parsed.get("unique_chords"):
        return None
    parsed["artist_slug"] = path[0]
    parsed["track_slug"] = path[1]
    parsed["is_simplified"] = "simplificada.html" in path[2:]
    return parsed


async def _resolve_page(
    client: httpx.AsyncClient, artist_slug: str, track_slug: str, simplified: bool
) -> dict[str, Any] | None:
    if simplified:
        parsed = await _fetch_page(client, artist_slug, track_slug, True)
        if parsed:
            return parsed
    return await _fetch_page(client, artist_slug, track_slug, False)


def find_cached_song(
    artist: str,
    track_name: str,
    *,
    simplified: bool = False,
    spotify_track_id: str | None = None,
    include_sheet: bool = False,
    fallback_to_normal: bool = False,
) -> dict[str, Any] | None:
    """Find an indexed song by Spotify id, slug or alias without touching the network."""
    artist_slugs = _artist_lookup_slugs(artist)
    track_slug = slugify(clean_track_name(track_name))
    track_id = _linkable_track_id(spotify_track_id)
    for variant in ([True, False] if simplified and fallback_to_normal else [simplified]):
        song = db.find_song(
            artist_slugs,
            _variant_slug(track_slug, variant),
            spotify_track_id=track_id,
            simplified=variant,
            include_sheet=include_sheet,
        )
        if song:
            return song
        # The Cifra Club URL can differ from the title ("que-pais-e-esse" for "Que País É Este").
        for row in db.songs_by_artist_slugs(artist_slugs):
            if (
                row["track_slug"].endswith(SIMPLIFIED_SUFFIX) == variant
                and slugify(clean_track_name(row["track_name"])) == track_slug
            ):
                return db.get_song_by_slugs(row["artist_slug"], row["track_slug"], include_sheet=include_sheet)
    return None


def _failure(
    status: str,
    artist: str,
    track_name: str,
    message: str,
    retry_after_seconds: int | None = None,
) -> dict[str, Any]:
    artist_slug = slugify(split_artists(artist)[0])
    result: dict[str, Any] = {
        "status": status,
        "url": f"{CIFRACLUB_BASE_URL}/{artist_slug}/{slugify(track_name)}/",
        "artist": artist,
        "track_name": track_name,
        "unique_chords": [],
        "chord_frequencies": {},
        "raw_chord_sheet": "",
        "key": None,
        "cached": False,
        "found": False,
        "error": message,
    }
    if retry_after_seconds is not None:
        result["retry_after_seconds"] = retry_after_seconds
    return result


def _keep_indexed(cached: dict[str, Any] | None, failure: dict[str, Any]) -> dict[str, Any]:
    """When the text of an indexed song cannot be downloaded, still return its indexed data."""
    if not cached:
        return failure
    kept = {k: failure[k] for k in ("status", "error", "retry_after_seconds") if k in failure}
    return {
        **cached,
        **kept,
        "raw_chord_sheet": "",
        "cached": True,
        "found": True,
        "is_simplified": _is_simplified_song(cached),
    }


async def fetch_chord_sheet_async(
    artist: str,
    track_name: str,
    *,
    simplified: bool = False,
    spotify_track_id: str | None = None,
    require_sheet: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return a chord sheet from the local database, or from Cifra Club through the limiter.

    Every page downloaded has its full text stored. With `require_sheet=False`, an indexed song
    without text counts as cached, so a sync never downloads it again; opening it does.

    The result carries `status`: cached, synced, not_found, rate_limited or error.
    """
    cleaned_track = clean_track_name(track_name)
    artists = split_artists(artist)
    artist_slug = slugify(artists[0])
    track_slug = slugify(cleaned_track)
    if not artist_slug or not track_slug:
        return _failure("not_found", artist, cleaned_track, "Artista ou música inválidos.")

    cached = None
    if not force_refresh:
        cached = find_cached_song(
            artist,
            track_name,
            simplified=simplified,
            spotify_track_id=spotify_track_id,
            include_sheet=require_sheet,
        )
        if cached and (not require_sheet or cached.get("raw_chord_sheet")):
            return {
                **cached,
                "status": "cached",
                "cached": True,
                "found": True,
                "is_simplified": _is_simplified_song(cached),
            }

    target_artist = cached["artist_slug"] if cached else artist_slug
    target_track = _base_slug(cached["track_slug"]) if cached else track_slug
    # An alias to the normal version means the simplified page is already known to be missing.
    try_simplified = simplified and (cached is None or _is_simplified_song(cached))

    try:
        async with _build_client() as client:
            parsed = await _resolve_page(client, target_artist, target_track, try_simplified)
            if not parsed and not cached:
                found_slugs = await _search_cifraclub_solr(client, artists, cleaned_track)
                if found_slugs:
                    parsed = await _resolve_page(client, *found_slugs, simplified)
    except CifraClubRateLimited as exc:
        return _keep_indexed(
            cached,
            _failure(
                "rate_limited",
                artist,
                cleaned_track,
                f"Cifra Club limitou o acesso. Tente novamente em {exc.retry_after_seconds}s.",
                exc.retry_after_seconds,
            ),
        )
    except CifraClubUnavailable as exc:
        logger.warning("Falha ao buscar '%s - %s': %s", artist, cleaned_track, exc)
        return _keep_indexed(cached, _failure("error", artist, cleaned_track, str(exc)))

    if not parsed:
        return _keep_indexed(
            cached,
            _failure(
                "not_found",
                artist,
                cleaned_track,
                f"Cifra não encontrada para '{artist} - {cleaned_track}'.",
            ),
        )

    chord_freqs = parsed["chord_frequencies"]
    has_barre = any(is_barre_chord(ch) for ch in chord_freqs)
    best_capo = find_best_capo(chord_freqs) if has_barre else {"capo_fret": 0, "transposed_chords": []}
    is_simplified = bool(parsed["is_simplified"])
    stored_track_slug = _variant_slug(parsed["track_slug"], is_simplified)
    track_id = _linkable_track_id(spotify_track_id)

    song_id = db.upsert_song_index(
        artist_slug=parsed["artist_slug"],
        track_slug=stored_track_slug,
        artist_name=parsed.get("artist") or artists[0],
        track_name=parsed.get("track_name") or cleaned_track,
        cifra_url=parsed["url"],
        musical_key=parsed["key"],
        chord_frequencies=chord_freqs,
        has_barre_chords=has_barre,
        avg_changes_per_line=parsed["avg_changes_per_line"],
        has_tabs=parsed["has_tabs"],
        best_capo_fret=best_capo["capo_fret"],
        best_capo_chords=best_capo["transposed_chords"],
        has_simplified=is_simplified,
        spotify_track_id=track_id,
        raw_chord_sheet=parsed["raw_chord_sheet"],
    )

    aliases = {(slug, _variant_slug(track_slug, simplified)) for slug in _artist_lookup_slugs(artist)}
    if simplified and not is_simplified:
        aliases.add((parsed["artist_slug"], _variant_slug(parsed["track_slug"], True)))
    aliases.discard((parsed["artist_slug"], stored_track_slug))
    db.link_song(song_id, aliases, spotify_track_id=track_id, simplified=simplified)

    return {
        "status": "synced",
        "url": parsed["url"],
        "artist": parsed.get("artist") or artists[0],
        "track_name": parsed.get("track_name") or cleaned_track,
        "artist_slug": parsed["artist_slug"],
        "track_slug": stored_track_slug,
        "is_simplified": is_simplified,
        "unique_chords": parsed["unique_chords"],
        "chord_frequencies": chord_freqs,
        "total_chord_events": parsed["total_chord_events"],
        "avg_changes_per_line": parsed["avg_changes_per_line"],
        "has_tabs": parsed["has_tabs"],
        "best_capo_fret": best_capo["capo_fret"],
        "best_capo_chords": best_capo["transposed_chords"],
        "raw_chord_sheet": parsed["raw_chord_sheet"] if require_sheet else "",
        "key": parsed["key"],
        "cached": False,
        "found": True,
    }


async def analyze_tracks(
    tracks: list[dict[str, Any]],
    *,
    simplified: bool,
    known_chords: list[str],
    avoid_barre: bool,
    max_new_lookups: int = MAX_NEW_LOOKUPS_PER_BATCH,
) -> list[dict[str, Any]]:
    """Index and evaluate tracks in order, returning one status per track.

    Indexed songs cost no request. During a cooldown the remaining uncached tracks fail fast.
    """
    results: list[dict[str, Any]] = []
    new_lookups = 0

    for track in tracks:
        artist = track["artist"]
        name = track["name"]
        track_id = track.get("track_id")
        base = {
            "track_id": track_id,
            "artist": artist,
            "name": name,
            "track_name": name,
            "is_synced": False,
            "found": False,
        }
        try:
            if new_lookups >= max_new_lookups and not find_cached_song(
                artist, name, simplified=simplified, spotify_track_id=track_id
            ):
                results.append(
                    {
                        **base,
                        "status": "rate_limited",
                        "error": f"Limite de {max_new_lookups} novas buscas por lote atingido.",
                    }
                )
                continue

            sheet = await fetch_chord_sheet_async(
                artist=artist,
                track_name=name,
                simplified=simplified,
                spotify_track_id=track_id,
                require_sheet=False,
            )
            if sheet["status"] != "cached":
                new_lookups += 1
            if not sheet.get("found"):
                failure = {**base, "status": sheet["status"], "error": sheet.get("error")}
                if "retry_after_seconds" in sheet:
                    failure["retry_after_seconds"] = sheet["retry_after_seconds"]
                results.append(failure)
                continue

            evaluation = evaluate_song_difficulty(
                unique_chords=sheet["unique_chords"],
                known_chords=known_chords,
                avoid_barre=avoid_barre,
                chord_frequencies=sheet.get("chord_frequencies"),
                avg_changes_per_line=sheet.get("avg_changes_per_line", 2.0),
                has_tabs=sheet.get("has_tabs", False),
            )
            results.append(
                {
                    **base,
                    "status": sheet["status"],
                    "artist": sheet["artist"],
                    "name": sheet["track_name"],
                    "track_name": sheet["track_name"],
                    "url": sheet["url"],
                    "key": sheet["key"],
                    "unique_chords": sheet["unique_chords"],
                    "chord_frequencies": sheet.get("chord_frequencies", {}),
                    "is_synced": True,
                    "found": True,
                    **evaluation,
                }
            )
        except Exception as exc:
            logger.exception("Erro ao analisar '%s - %s'", artist, name)
            results.append({**base, "status": "error", "error": f"Erro ao analisar a faixa: {exc}"})
    return results


def summarize_batch(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"total": len(results), "synced": 0, "cached": 0, "not_found": 0, "rate_limited": 0, "error": 0}
    for result in results:
        summary[result["status"]] += 1
    return summary


def rank_recommendations(
    results: list[dict[str, Any]], target_level: str | None = None
) -> list[dict[str, Any]]:
    """Sort analyzed songs easiest first, keeping tracks that could not be analyzed at the end."""
    found = [r for r in results if r.get("found")]
    if target_level:
        wanted = target_level.strip().lower()
        found = [r for r in found if r["level"].lower() == wanted]
    found.sort(key=lambda r: r["score"], reverse=True)
    return found + [r for r in results if not r.get("found")]
