"""Test environment, set before the app is imported: never touch the real database or credentials."""

import os
import tempfile
from pathlib import Path

_TEST_DIR = Path(tempfile.mkdtemp(prefix="cifrafy-tests-"))

os.environ["DATABASE_PATH"] = str(_TEST_DIR / "test.db")
os.environ["SPOTIFY_CACHE_PATH"] = str(_TEST_DIR / "token.json")
os.environ["ALLOWED_HOSTS"] = "testserver,127.0.0.1,localhost"
os.environ["ALLOWED_ORIGINS"] = "http://127.0.0.1:3000,http://localhost:3000"
for name in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET"):
    os.environ[name] = ""
