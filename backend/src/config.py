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


def resolve_service_path(value: str) -> Path:
    """Relative paths always start at backend/, whatever folder the server runs from."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else (SERVICE_ROOT / path).resolve()


def _csv_env(name: str, default: str) -> list[str]:
    return [item.strip().rstrip("/") for item in os.getenv(name, default).split(",") if item.strip()]


class Settings:
    SPOTIFY_CLIENT_ID: str = os.getenv("SPOTIFY_CLIENT_ID") or os.getenv("SPOTIPY_CLIENT_ID", "")
    SPOTIFY_CLIENT_SECRET: str = os.getenv("SPOTIFY_CLIENT_SECRET") or os.getenv("SPOTIPY_CLIENT_SECRET", "")
    SPOTIFY_REDIRECT_URI: str = (
        os.getenv("SPOTIFY_REDIRECT_URI")
        or os.getenv("SPOTIPY_REDIRECT_URI")
        or "http://127.0.0.1:8000/api/spotify/callback"
    )
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://127.0.0.1:3000").rstrip("/")

    DEFAULT_KNOWN_CHORDS: list[str] = [
        c.strip()
        for c in os.getenv("DEFAULT_KNOWN_CHORDS", "C,G,D,A,E,Am,Em,Dm").split(",")
        if c.strip()
    ]

    DATABASE_PATH: Path = resolve_service_path(os.getenv("DATABASE_PATH") or "data/guitar.db")
    SPOTIFY_CACHE_PATH: str = str(
        resolve_service_path(os.getenv("SPOTIFY_CACHE_PATH") or str(DATABASE_PATH.parent / ".spotify_token.json"))
    )

    # Browser origins allowed to call the API, and Host headers the server answers to.
    ALLOWED_ORIGINS: list[str] = _csv_env("ALLOWED_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000")
    ALLOWED_HOSTS: list[str] = _csv_env("ALLOWED_HOSTS", "127.0.0.1,localhost")

    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))


settings = Settings()
