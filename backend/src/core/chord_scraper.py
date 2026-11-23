"""Cifra Club Scraper, Spotify Title Sanitizer, Solr Search Fallback & SQLite Cache."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

import httpx
from bs4 import BeautifulSoup

from src.core.analyzer import find_best_capo, is_barre_chord
from src.db import db

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Noise patterns commonly found in Spotify track titles
SPOTIFY_NOISE_PATTERNS = [
    r"\s*-\s*.*?(remaster|ao vivo|live|acoustic|acústico|radio edit|version|versão|bonus|mono|stereo|deluxe|mix).*$",
    r"\s*\([^)]*(remaster|ao vivo|live|acoustic|acústico|feat\.?|ft\.?|part\.?|version|versão|bonus|deluxe)[^)]*\)",
    r"\s*\[[^\]]*(remaster|ao vivo|live|acoustic|acústico|feat\.?|ft\.?|part\.?|version|versão|bonus|deluxe)[^\]]*\]",
    r"\s+(feat\.?|ft\.?|part\.?)\s+.*$",
]

TAB_LINE_REGEX = re.compile(r"^[eBGDAE]\|[-0-9hpb/\\~x]+", re.MULTILINE)


def clean_track_name(track_name: str) -> str:
    """Strip Spotify metadata noise (- Remastered, (Ao Vivo), feat., etc.) from track title."""
    cleaned = track_name.strip()
    for pattern in SPOTIFY_NOISE_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    # Also strip trailing ' - Live at ...' or ' - 2011'
    cleaned = re.sub(r"\s*-\s*\d{4}.*$", "", cleaned)
    return cleaned.strip() or track_name.strip()


def slugify(text: str) -> str:
    """Convert artist or track name to Cifra Club URL slug."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    ascii_text = re.sub(r"['\".,!?()\[\]]", "", ascii_text)
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text)
    return ascii_text.strip("-")


def parse_cifraclub_html(html: str, url: str) -> dict[str, Any] | None:
    """Extract chords, frequencies, line density, key, and raw chord sheet from Cifra Club HTML."""
    soup = BeautifulSoup(html, "html.parser")
    pre = soup.find("pre")
    if not pre:
        return None

    # Extract musical key (Tom)
    key_span = soup.find(id="cifra_tom")
    musical_key: str | None = None
    if key_span:
        key_link = key_span.find("a")
        musical_key = key_link.get_text(strip=True) if key_link else key_span.get_text(strip=True)
        musical_key = musical_key.replace("tom:", "").strip() or None

    # Extract all chords inside <pre><b>...</b></pre> in order of appearance
    chord_tags = pre.find_all("b")
    chord_frequencies: dict[str, int] = {}
    for tag in chord_tags:
        ch = tag.get_text(strip=True)
        if ch and re.match(r"^[A-G][#b]?", ch):
            chord_frequencies[ch] = chord_frequencies.get(ch, 0) + 1

    raw_sheet = pre.get_text()
    if not chord_frequencies and not raw_sheet.strip():
        return None

    # Calculate harmonic density (average chord changes per active lyric/chord section)
    lines = [line for line in raw_sheet.splitlines() if line.strip() and not line.strip().startswith("[")]
    non_tab_lines = [line for line in lines if not re.match(r"^[eBGDAE]\|", line.strip())]
    total_events = sum(chord_frequencies.values())
    # Roughly half the non-tab lines are chord lines and half are lyric lines
    lyric_line_estimate = max(len(non_tab_lines) / 2.0, 1.0)
    avg_changes_per_line = round(total_events / lyric_line_estimate, 2)

    has_tabs = bool(TAB_LINE_REGEX.search(raw_sheet)) or soup.find("span", class_="tablatura") is not None

    title_el = soup.find("h1", class_="t1")
    artist_el = soup.find("h2", class_="t3") or soup.find("h3", class_="t3")

    return {
        "url": url,
        "artist": artist_el.get_text(strip=True) if artist_el else None,
        "track_name": title_el.get_text(strip=True) if title_el else None,
        "key": musical_key,
        "unique_chords": list(chord_frequencies.keys()),
        "chord_frequencies": chord_frequencies,
        "total_chord_events": total_events,
        "avg_changes_per_line": avg_changes_per_line,
        "has_tabs": has_tabs,
        "raw_chord_sheet": raw_sheet,
    }


async def _search_cifraclub_solr(
    client: httpx.AsyncClient, artist: str, track_name: str
) -> tuple[str, str] | None:
    """Fallback search using Studio Sol / Cifra Club's Solr autocomplete index."""
    query = f"{artist} {track_name}".strip()
    try:
        resp = await client.get(
            "https://solr.sscdn.co/cc/h2/",
            params={"q": query},
            timeout=6.0,
        )
        if resp.status_code != 200:
            return None

        text = resp.text.strip()
        # Solr endpoint wraps JSON in JSONP callback: `suggest_callback({...})`
        if text.startswith("suggest_callback(") and text.endswith(")"):
            text = text[len("suggest_callback(") : -1]
        elif "(" in text and text.endswith(")"):
            text = text[text.find("(") + 1 : -1]

        payload = json.loads(text)
        docs = payload.get("response", {}).get("docs", [])
        for doc in docs:
            # 'u' is song slug, 'd' is artist slug in Cifra Club Solr docs
            a_slug = doc.get("d") or doc.get("dns")
            t_slug = doc.get("u") or doc.get("url")
            if a_slug and t_slug:
                return str(a_slug).strip("/"), str(t_slug).strip("/")
    except Exception:
        return None
    return None


async def fetch_chord_sheet_async(
    artist: str,
    track_name: str,
    *,
    spotify_track_id: str | None = None,
    save_raw_sheet: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Fetch chord sheet from local SQLite cache or Cifra Club (direct slug + Solr search fallback)."""
    cleaned_track = clean_track_name(track_name)
    artist_slug = slugify(artist)
    track_slug = slugify(cleaned_track)

    if not force_refresh:
        cached = db.get_song_by_slugs(artist_slug, track_slug, include_sheet=save_raw_sheet)
        if cached and (not save_raw_sheet or cached.get("raw_chord_sheet")):
            cached["cached"] = True
            return cached

    headers = {"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=10.0) as client:
        direct_url = f"https://www.cifraclub.com.br/{artist_slug}/{track_slug}/"
        parsed: dict[str, Any] | None = None

        try:
            resp = await client.get(direct_url)
            # Ensure it didn't redirect back to the artist index page
            if resp.status_code == 200 and f"/{artist_slug}/" in str(resp.url):
                parsed = parse_cifraclub_html(resp.text, str(resp.url))
        except httpx.HTTPError:
            parsed = None

        # Step 2: Solr Search fallback if direct slug didn't match
        if not parsed or not parsed.get("unique_chords"):
            found_slugs = await _search_cifraclub_solr(client, artist, cleaned_track)
            if found_slugs:
                artist_slug, track_slug = found_slugs
                fallback_url = f"https://www.cifraclub.com.br/{artist_slug}/{track_slug}/"
                try:
                    resp = await client.get(fallback_url)
                    if resp.status_code == 200:
                        parsed = parse_cifraclub_html(resp.text, str(resp.url))
                except httpx.HTTPError:
                    parsed = None

    if not parsed or not parsed.get("unique_chords"):
        return {
            "url": f"https://www.cifraclub.com.br/{artist_slug}/{track_slug}/",
            "artist": artist,
            "track_name": cleaned_track,
            "unique_chords": [],
            "chord_frequencies": {},
            "raw_chord_sheet": "",
            "key": None,
            "cached": False,
            "found": False,
            "error": f"Cifra não encontrada para '{artist} - {cleaned_track}'.",
        }

    chord_freqs = parsed["chord_frequencies"]
    has_barre = any(is_barre_chord(ch) for ch in chord_freqs)
    best_capo = find_best_capo(chord_freqs) if has_barre else {"capo_fret": 0, "transposed_chords": []}

    db.upsert_song_index(
        artist_slug=artist_slug,
        track_slug=track_slug,
        artist_name=parsed.get("artist") or artist,
        track_name=parsed.get("track_name") or cleaned_track,
        cifra_url=parsed["url"],
        musical_key=parsed["key"],
        chord_frequencies=chord_freqs,
        has_barre_chords=has_barre,
        avg_changes_per_line=parsed["avg_changes_per_line"],
        has_tabs=parsed["has_tabs"],
        best_capo_fret=best_capo["capo_fret"],
        best_capo_chords=best_capo["transposed_chords"],
        spotify_track_id=spotify_track_id,
        raw_chord_sheet=parsed["raw_chord_sheet"] if save_raw_sheet else None,
    )

    return {
        "url": parsed["url"],
        "artist": parsed.get("artist") or artist,
        "track_name": parsed.get("track_name") or cleaned_track,
        "artist_slug": artist_slug,
        "track_slug": track_slug,
        "unique_chords": parsed["unique_chords"],
        "chord_frequencies": chord_freqs,
        "total_chord_events": parsed["total_chord_events"],
        "avg_changes_per_line": parsed["avg_changes_per_line"],
        "has_tabs": parsed["has_tabs"],
        "best_capo_fret": best_capo["capo_fret"],
        "best_capo_chords": best_capo["transposed_chords"],
        "raw_chord_sheet": parsed["raw_chord_sheet"] if save_raw_sheet else "",
        "key": parsed["key"],
        "cached": False,
        "found": True,
    }
