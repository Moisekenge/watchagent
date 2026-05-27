#!/usr/bin/env python3
"""WatchAgent detection-replay skill.

Replays stored readings through the live detection engine and reports what
would fire. See ../SKILL.md for usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.config import DetectionConfig  # noqa: E402
from app.db import make_engine  # noqa: E402
from app.detection import detect_events  # noqa: E402
from app.domain import ReadingData  # noqa: E402
from app.models import Reading  # noqa: E402
from app.repository import to_reading_data  # noqa: E402

DEFAULT_URL = "postgresql+psycopg2://watchagent:watchagent_local_dev@127.0.0.1:5432/watchagent"


def _session(url: str) -> Session:
    return sessionmaker(bind=make_engine(url), expire_on_commit=False)()


def _peers_at(rows_by_city: dict[str, list[Reading]], city: str, ts: datetime) -> dict[str, ReadingData]:
    peers: dict[str, ReadingData] = {}
    for other, rows in rows_by_city.items():
        if other == city:
            continue
        prior = [r for r in rows if r.timestamp <= ts]
        if prior:
            peers[other] = to_reading_data(prior[-1])
    return peers


def replay(session: Session, args) -> dict:
    cities = list(session.scalars(select(Reading.city).distinct()).all())
    if args.city:
        cities = [c for c in cities if c == args.city]

    rows_by_city: dict[str, list[Reading]] = {}
    for city in cities:
        rows = list(
            session.scalars(
                select(Reading).where(Reading.city == city).order_by(Reading.timestamp.asc())
            ).all()
        )
        rows_by_city[city] = rows

    config = DetectionConfig()
    window = args.window if args.window is not None else config.rolling_window

    by_type: Counter = Counter()
    by_city: Counter = Counter()
    samples: list[dict] = []
    total_replayed = 0

    for city, rows in rows_by_city.items():
        if args.limit is not None:
            rows = rows[-args.limit:]
        last_event_at: dict[tuple[str, str], datetime] = {}
        for i, row in enumerate(rows):
            reading = to_reading_data(row)
            history = [to_reading_data(r) for r in rows[max(0, i - window):i]]
            peers = _peers_at(rows_by_city, city, reading.timestamp)
            cooldown_state = {} if args.no_cooldown else last_event_at
            events = detect_events(
                reading, history, peers, cooldown_state, config, now=reading.timestamp
            )
            if not args.no_cooldown:
                for e in events:
                    last_event_at[e.cooldown_key()] = reading.timestamp
            total_replayed += 1
            for e in events:
                by_type[e.event_type.value] += 1
                by_city[e.city] += 1
                if len(samples) < 25:
                    samples.append(
                        {
                            "city": e.city,
                            "type": e.event_type.value,
                            "severity": e.severity.value,
                            "reason": e.reason,
                        }
                    )

    return {
        "cooldown_applied": not args.no_cooldown,
        "history_window": window,
        "readings_replayed": total_replayed,
        "events_fired": sum(by_type.values()),
        "events_by_type": dict(by_type),
        "events_by_city": dict(by_city),
        "sample_reasons": samples,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Replay stored readings through detection.")
    p.add_argument("--database-url", default=None)
    p.add_argument("--limit", type=int, default=None, help="Last N readings per city.")
    p.add_argument("--window", type=int, default=None, help="History window for detectors.")
    p.add_argument("--no-cooldown", action="store_true", help="Show raw candidate volume.")
    p.add_argument("--city", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    url = args.database_url or os.environ.get("DATABASE_URL") or DEFAULT_URL
    try:
        session = _session(url)
    except Exception as exc:
        print(json.dumps({"error": "could not connect to database", "detail": str(exc)}, indent=2))
        return 1
    try:
        print(json.dumps(replay(session, args), indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
