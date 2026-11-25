"""Creates a fresh database for the browser tests. Every song has its sheet saved, so no test
reaches Cifra Club. Run with the backend virtualenv: DATABASE_PATH must point to a throwaway file.
"""

import os
import sys
from pathlib import Path

db_path = Path(os.environ["DATABASE_PATH"])
db_path.parent.mkdir(parents=True, exist_ok=True)
db_path.unlink(missing_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from src.core.chord_scraper import slugify  # noqa: E402
from src.db import db  # noqa: E402

LYRIC = "Uma linha de letra para acompanhar"


def sheet(chords: list[str], verses: int = 30) -> str:
    line = "   ".join(chords)
    return "\n".join(f"{line}\n{LYRIC} {i}\n" for i in range(verses))


SONGS = [
    ("Banda Teste", "Facil em Sol", "G", ["G", "C", "D", "Em"]),
    ("Banda Teste", "Musica em Si Bemol", "Bb", ["Bb", "F", "Gm", "Eb"]),
    ("Banda Teste", "Afinada Meio Tom", "Eb", ["E", "C#m", "G#m", "A", "B"]),
    ("Banda Teste", "Para Apagar", "C", ["C", "G", "Am", "F"]),
]

for artist, name, key, chords in SONGS:
    db.upsert_song_index(
        artist_slug=slugify(artist),
        track_slug=slugify(name),
        artist_name=artist,
        track_name=name,
        cifra_url=f"https://www.cifraclub.com.br/{slugify(artist)}/{slugify(name)}/",
        musical_key=key,
        chord_frequencies={chord: 10 for chord in chords},
        has_barre_chords=False,
        avg_changes_per_line=2.0,
        has_tabs=False,
        raw_chord_sheet=sheet(chords),
    )

db.update_user_profile(known_chords=["C", "G", "D", "Em", "Am"], preferred_key="G")
print(f"Banco de teste criado em {db_path}")
