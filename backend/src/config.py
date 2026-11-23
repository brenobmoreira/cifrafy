"""Configuration loader for Guitar Practice Assistant."""

import os
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVICE_ROOT.parent
ENV_FILE = REPO_ROOT / ".env" if (REPO_ROOT / ".env").exists() else SERVICE_ROOT / ".env"

try:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
except ImportError:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


class Settings:
    SPOTIFY_CLIENT_ID: str = os.getenv("SPOTIFY_CLIENT_ID") or os.getenv("SPOTIPY_CLIENT_ID", "")
    SPOTIFY_CLIENT_SECRET: str = os.getenv("SPOTIFY_CLIENT_SECRET") or os.getenv("SPOTIPY_CLIENT_SECRET", "")
    SPOTIFY_REDIRECT_URI: str = (
        os.getenv("SPOTIFY_REDIRECT_URI")
        or os.getenv("SPOTIPY_REDIRECT_URI")
        or "http://127.0.0.1:8000/api/spotify/callback"
    )
    SPOTIFY_CACHE_PATH: str = os.getenv(
        "SPOTIFY_CACHE_PATH", str(REPO_ROOT / "data" / ".spotify_token.json")
    )

    DEFAULT_KNOWN_CHORDS: list[str] = [
        c.strip()
        for c in os.getenv("DEFAULT_KNOWN_CHORDS", "C,G,D,A,E,Am,Em,Dm").split(",")
        if c.strip()
    ]

    DATABASE_PATH: Path = Path(
        os.getenv("DATABASE_PATH", str(REPO_ROOT / "data" / "guitar.db"))
    )
    MAX_CACHED_SHEETS: int = int(os.getenv("MAX_CACHED_SHEETS", "200"))

    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))


settings = Settings()
