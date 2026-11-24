"""Daily study time, shared by the REST API and the MCP server."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from src.db import db

STUDY_KINDS = ("free", "song", "changes", "session")
MIN_SECONDS, MAX_SECONDS = 1, 43200
MIN_GOAL, MAX_GOAL = 1, 600
MIN_DAYS, MAX_DAYS = 1, 365
WEEK_DAYS = 7
MONTH_DAYS = 30

_DATE_FORMAT = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_local_date(value: str) -> date:
    if not _DATE_FORMAT.match(value or ""):
        raise ValueError("Data inválida. Use o formato YYYY-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Data inválida. Use o formato YYYY-MM-DD.") from exc


def _sum_last(totals: dict[str, int], today: date, days: int) -> int:
    return sum(totals.get((today - timedelta(days=n)).isoformat(), 0) for n in range(days))


def _streak(totals: dict[str, int], today: date) -> int:
    day = today if totals.get(today.isoformat(), 0) > 0 else today - timedelta(days=1)
    streak = 0
    while totals.get(day.isoformat(), 0) > 0:
        streak += 1
        day -= timedelta(days=1)
    return streak


def summary(today: str, days: int = 30) -> dict[str, Any]:
    current = parse_local_date(today)
    if not MIN_DAYS <= days <= MAX_DAYS:
        raise ValueError(f"days deve estar entre {MIN_DAYS} e {MAX_DAYS}.")

    totals = db.get_study_totals()
    goal = db.get_study_goal()
    today_seconds = totals.get(current.isoformat(), 0)
    return {
        "today_seconds": today_seconds,
        "week_seconds": _sum_last(totals, current, WEEK_DAYS),
        "month_seconds": _sum_last(totals, current, MONTH_DAYS),
        "total_seconds": sum(s for d, s in totals.items() if d <= current.isoformat()),
        "streak_days": _streak(totals, current),
        "goal_minutes": goal,
        "goal_met_today": today_seconds >= goal * 60,
        "days": [
            {"date": day, "seconds": totals.get(day, 0)}
            for day in ((current - timedelta(days=n)).isoformat() for n in range(days - 1, -1, -1))
        ],
    }


def log_session(
    duration_seconds: int, local_date: str, kind: str = "free", song_id: int | None = None
) -> dict[str, Any]:
    """Save study time. Raises ValueError for bad input and LookupError for an unknown song."""
    parse_local_date(local_date)
    if not MIN_SECONDS <= duration_seconds <= MAX_SECONDS:
        raise ValueError(f"duration_seconds deve estar entre {MIN_SECONDS} e {MAX_SECONDS}.")
    if kind not in STUDY_KINDS:
        raise ValueError(f"Tipo inválido. Use um destes: {', '.join(STUDY_KINDS)}.")
    if song_id is not None and db.get_song_by_id(song_id) is None:
        raise LookupError(f"Música {song_id} não encontrada.")
    session = db.add_study_session(local_date, duration_seconds, kind, song_id)
    return {"session": session, "summary": summary(local_date)}


def set_goal(minutes: int) -> dict[str, int]:
    if not MIN_GOAL <= minutes <= MAX_GOAL:
        raise ValueError(f"A meta deve estar entre {MIN_GOAL} e {MAX_GOAL} minutos.")
    return {"goal_minutes": db.set_study_goal(minutes)}
