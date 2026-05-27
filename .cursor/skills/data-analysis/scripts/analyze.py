#!/usr/bin/env python3
"""WatchAgent data-analysis skill.

Queries the stored readings and events and prints a structured JSON answer.
Reuses the application's own models, repository, and robust-statistics helpers
so the analysis stays consistent with how the service itself reasons about data.

See ../SKILL.md for usage. Runnable standalone:

    python .cursor/skills/data-analysis/scripts/analyze.py overview
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make the repo root importable so `import app...` works when run directly.
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.db import make_engine  # noqa: E402
from app.detection.baselines import mad, median  # noqa: E402
from app.models import Event, Reading  # noqa: E402
from app.repository import latest_reading_per_city  # noqa: E402

DEFAULT_URL = "postgresql+psycopg2://watchagent:watchagent_local_dev@localhost:5432/watchagent"
NUMERIC_FIELDS = ("temperature_2m", "apparent_temperature", "wind_speed_10m", "precipitation")


def _session(url: str) -> Session:
    engine = make_engine(url)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cutoff(hours: float | None) -> datetime | None:
    return None if hours is None else _now() - timedelta(hours=hours)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    med = median(values)
    return {
        "count": len(values),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "median": round(med, 2),
        "mad": round(mad(values, med), 3),
        "mean": round(sum(values) / len(values), 2),
    }


# --- commands ---------------------------------------------------------------
def cmd_overview(session: Session, _args) -> dict:
    total_readings = session.scalar(select(func.count()).select_from(Reading)) or 0
    total_events = session.scalar(select(func.count()).select_from(Event)) or 0

    per_city = {
        city: count
        for city, count in session.execute(
            select(Reading.city, func.count()).group_by(Reading.city)
        ).all()
    }
    latest = {
        city: _iso(r.timestamp) for city, r in latest_reading_per_city(session).items()
    }
    by_type = {
        etype: count
        for etype, count in session.execute(
            select(Event.event_type, func.count()).group_by(Event.event_type)
        ).all()
    }
    by_severity = {
        sev: count
        for sev, count in session.execute(
            select(Event.severity, func.count()).group_by(Event.severity)
        ).all()
    }
    return {
        "readings_stored": total_readings,
        "events_stored": total_events,
        "readings_per_city": per_city,
        "latest_reading_per_city": latest,
        "events_by_type": by_type,
        "events_by_severity": by_severity,
    }


def cmd_city(session: Session, args) -> dict:
    cutoff = _cutoff(args.hours)
    stmt = select(Reading).where(Reading.city == args.city)
    if cutoff is not None:
        stmt = stmt.where(Reading.timestamp >= cutoff)
    rows = list(session.scalars(stmt.order_by(Reading.timestamp.asc())).all())

    field_stats = {
        field: _stats([getattr(r, field) for r in rows]) for field in NUMERIC_FIELDS
    }
    latest = rows[-1] if rows else None
    recent_events = list(
        session.scalars(
            select(Event)
            .where(Event.city == args.city)
            .order_by(Event.detected_at.desc())
            .limit(5)
        ).all()
    )
    return {
        "city": args.city,
        "window_hours": args.hours,
        "readings_in_window": len(rows),
        "latest_reading": None
        if latest is None
        else {
            "timestamp": _iso(latest.timestamp),
            "temperature_2m": latest.temperature_2m,
            "apparent_temperature": latest.apparent_temperature,
            "precipitation": latest.precipitation,
            "wind_speed_10m": latest.wind_speed_10m,
            "weather_code": latest.weather_code,
        },
        "field_stats": field_stats,
        "recent_events": [
            {"type": e.event_type, "severity": e.severity, "reason": e.reason}
            for e in recent_events
        ],
    }


def cmd_compare(session: Session, args) -> dict:
    cutoff = _cutoff(args.hours)
    averages: dict[str, float] = {}
    for city, r in latest_reading_per_city(session).items():
        stmt = select(Reading.temperature_2m).where(Reading.city == city)
        if cutoff is not None:
            stmt = stmt.where(Reading.timestamp >= cutoff)
        temps = list(session.scalars(stmt).all())
        if temps:
            averages[city] = round(sum(temps) / len(temps), 2)

    current = {
        city: round(r.temperature_2m, 2)
        for city, r in latest_reading_per_city(session).items()
    }
    spread = None
    if len(current) >= 2:
        warmest = max(current, key=current.get)
        coldest = min(current, key=current.get)
        spread = {
            "warmest": warmest,
            "coldest": coldest,
            "spread_c": round(current[warmest] - current[coldest], 2),
        }
    return {
        "window_hours": args.hours,
        "avg_temperature_by_city": averages,
        "current_temperature_by_city": current,
        "current_spread": spread,
    }


def cmd_events(session: Session, args) -> dict:
    stmt = select(Event)
    if args.city:
        stmt = stmt.where(Event.city == args.city)
    if args.type:
        stmt = stmt.where(Event.event_type == args.type)
    if args.severity:
        stmt = stmt.where(Event.severity == args.severity)
    cutoff = _cutoff(args.hours)
    if cutoff is not None:
        stmt = stmt.where(Event.detected_at >= cutoff)
    rows = list(
        session.scalars(stmt.order_by(Event.detected_at.desc()).limit(args.limit)).all()
    )
    return {
        "filters": {
            "city": args.city,
            "type": args.type,
            "severity": args.severity,
            "hours": args.hours,
            "limit": args.limit,
        },
        "count": len(rows),
        "events": [
            {
                "city": e.city,
                "type": e.event_type,
                "field": e.field,
                "severity": e.severity,
                "deviation": e.deviation,
                "reason": e.reason,
                "reading_timestamp": _iso(e.reading_timestamp),
            }
            for e in rows
        ],
    }


def cmd_trend(session: Session, args) -> dict:
    if args.field not in NUMERIC_FIELDS:
        return {"error": f"unknown field '{args.field}'", "valid_fields": list(NUMERIC_FIELDS)}
    cutoff = _cutoff(args.hours)
    stmt = select(Reading).where(Reading.city == args.city)
    if cutoff is not None:
        stmt = stmt.where(Reading.timestamp >= cutoff)
    rows = list(session.scalars(stmt.order_by(Reading.timestamp.asc())).all())
    if len(rows) < 2:
        return {
            "city": args.city,
            "field": args.field,
            "window_hours": args.hours,
            "note": "not enough readings in window to compute a trend",
            "readings_in_window": len(rows),
        }
    first, last = rows[0], rows[-1]
    change = getattr(last, args.field) - getattr(first, args.field)
    span_hours = max((last.timestamp - first.timestamp).total_seconds() / 3600.0, 1e-9)
    direction = "rising" if change > 0 else "falling" if change < 0 else "flat"
    return {
        "city": args.city,
        "field": args.field,
        "window_hours": args.hours,
        "readings_in_window": len(rows),
        "start_value": round(getattr(first, args.field), 2),
        "end_value": round(getattr(last, args.field), 2),
        "change": round(change, 2),
        "slope_per_hour": round(change / span_hours, 3),
        "direction": direction,
    }


COMMANDS = {
    "overview": cmd_overview,
    "city": cmd_city,
    "compare": cmd_compare,
    "events": cmd_events,
    "trend": cmd_trend,
}


def build_parser() -> argparse.ArgumentParser:
    # --database-url lives on a shared parent so it is accepted either before or
    # after the subcommand (e.g. both `analyze.py --database-url X overview` and
    # `analyze.py overview --database-url X` work).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--database-url", default=None, help="Override DB URL.")

    parser = argparse.ArgumentParser(
        description="Analyze WatchAgent stored data.", parents=[common]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("overview", parents=[common], help="Counts and latest timestamps.")

    p_city = sub.add_parser("city", parents=[common], help="Per-city stats and recent events.")
    p_city.add_argument("city")
    p_city.add_argument("--hours", type=float, default=48)

    p_cmp = sub.add_parser("compare", parents=[common], help="Cross-city temperature comparison.")
    p_cmp.add_argument("--hours", type=float, default=24)

    p_ev = sub.add_parser("events", parents=[common], help="Filtered event list with reasons.")
    p_ev.add_argument("--city", default=None)
    p_ev.add_argument("--type", default=None)
    p_ev.add_argument("--severity", default=None, choices=["info", "notable", "severe"])
    p_ev.add_argument("--hours", type=float, default=None)
    p_ev.add_argument("--limit", type=int, default=20)

    p_tr = sub.add_parser("trend", parents=[common], help="Direction and slope over a window.")
    p_tr.add_argument("field")
    p_tr.add_argument("city")
    p_tr.add_argument("--hours", type=float, default=12)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    url = args.database_url or os.environ.get("DATABASE_URL") or DEFAULT_URL
    try:
        session = _session(url)
    except Exception as exc:  # connection/config failure → structured error
        print(json.dumps({"error": "could not connect to database", "detail": str(exc)}, indent=2))
        return 1
    try:
        result = COMMANDS[args.command](session, args)
        print(json.dumps({"command": args.command, "result": result}, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
