"""Study timer tests. Nothing reaches the network."""

import pytest
from fastapi.testclient import TestClient

import src.api as api
from src.db import db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "db_path", tmp_path / "test.db")
    db._init_schema()


@pytest.fixture
def client():
    return TestClient(api.app)


def log(client, seconds, day, **extra):
    return client.post("/api/study/sessions", json={"duration_seconds": seconds, "local_date": day, **extra})


def add_song():
    return db.upsert_song_index(
        artist_slug="a", track_slug="s", artist_name="A", track_name="S", cifra_url="u", musical_key="C",
        chord_frequencies={"C": 2}, has_barre_chords=False, avg_changes_per_line=2.0, has_tabs=False,
    )


def test_summary_totals_days_and_goal(client):
    assert client.put("/api/study/goal", json={"minutes": 10}).json() == {"goal_minutes": 10}
    log(client, 300, "2026-11-23")
    response = log(client, 300, "2026-11-23", kind="changes")
    assert response.status_code == 200
    assert response.json()["session"]["kind"] == "changes"
    log(client, 120, "2026-11-17")
    log(client, 60, "2026-11-16")
    log(client, 30, "2026-10-01")

    data = client.get("/api/study/summary", params={"today": "2026-11-23", "days": 8}).json()
    assert data["today_seconds"] == 600
    assert data["week_seconds"] == 720
    assert data["month_seconds"] == 780
    assert data["total_seconds"] == 810
    assert data["goal_met_today"] is True
    assert [d["date"] for d in data["days"]][0] == "2026-11-16"
    assert data["days"][-1] == {"date": "2026-11-23", "seconds": 600}
    assert len(data["days"]) == 8
    assert data["days"][1]["seconds"] == 120
    assert data["days"][2]["seconds"] == 0


def test_streak_ends_today_or_yesterday(client):
    for day in ("2026-11-20", "2026-11-21", "2026-11-22"):
        log(client, 60, day)
    get = lambda today: client.get("/api/study/summary", params={"today": today}).json()["streak_days"]
    assert get("2026-11-23") == 3
    log(client, 60, "2026-11-23")
    assert get("2026-11-23") == 4
    assert get("2026-11-25") == 0


def test_streak_crosses_month_boundary(client):
    log(client, 60, "2026-10-31")
    log(client, 60, "2026-11-01")
    assert client.get("/api/study/summary", params={"today": "2026-11-01"}).json()["streak_days"] == 2


@pytest.mark.parametrize("day", ["2026-13-01", "2026-02-30", "23/11/2026", "2026-1-1", ""])
def test_invalid_dates_return_422(client, day):
    assert log(client, 60, day).status_code == 422
    assert client.get("/api/study/summary", params={"today": day}).status_code == 422


def test_input_limits(client):
    assert log(client, 0, "2026-11-23").status_code == 422
    assert log(client, 43201, "2026-11-23").status_code == 422
    assert log(client, 60, "2026-11-23", kind="outro").status_code == 422
    assert client.put("/api/study/goal", json={"minutes": 0}).status_code == 422
    assert client.get("/api/study/summary", params={"today": "2026-11-23", "days": 366}).status_code == 422


def test_unknown_song_is_404_and_deleted_song_keeps_time(client):
    assert log(client, 60, "2026-11-23", song_id=999).status_code == 404
    song_id = add_song()
    assert log(client, 60, "2026-11-23", kind="song", song_id=song_id).status_code == 200
    with db.connect() as conn:
        conn.execute("DELETE FROM song_index WHERE id = ?", (song_id,))
        assert conn.execute("SELECT song_id FROM study_sessions").fetchone()["song_id"] is None
    assert client.get("/api/study/summary", params={"today": "2026-11-23"}).json()["today_seconds"] == 60
