#!/usr/bin/env python3
"""WatchAgent dedup-audit skill.

Verifies the (city, timestamp) deduplication guarantee from the data side and
flags collection gaps. See ../SKILL.md for usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.db import make_engine  # noqa: E402
from app.models import Reading  # noqa: E402

DEFAULT_URL = "postgresql+psycopg2://watchagent:watchagent_local_dev@127.0.0.1:5432/watchagent"


def _session(url: str) -> Session:
    return sessionmaker(bind=make_engine(url), expire_on_commit=False)()


def audit(session: Session, gap_minutes: float) -> dict:
    # Duplicate (city, timestamp) groups — must be empty.
    dup_rows = session.execute(
        select(Reading.city, Reading.timestamp, func.count().label("n"))
        .group_by(Reading.city, Reading.timestamp)
        .having(func.count() > 1)
    ).all()
    duplicate_groups = [
        {"city": city, "timestamp": ts.isoformat(), "count": n} for city, ts, n in dup_rows
    ]

    per_city: dict[str, dict] = {}
    gaps: list[dict] = []
    cities = list(session.scalars(select(Reading.city).distinct()).all())
    for city in cities:
        rows = list(
            session.scalars(
                select(Reading).where(Reading.city == city).order_by(Reading.timestamp.asc())
            ).all()
        )
        city_gaps = 0
        for prev, cur in zip(rows, rows[1:], strict=False):
            delta_min = (cur.timestamp - prev.timestamp).total_seconds() / 60.0
            if delta_min > gap_minutes:
                city_gaps += 1
                gaps.append(
                    {
                        "city": city,
                        "from": prev.timestamp.isoformat(),
                        "to": cur.timestamp.isoformat(),
                        "gap_minutes": round(delta_min, 1),
                    }
                )
        per_city[city] = {
            "readings": len(rows),
            "first": rows[0].timestamp.isoformat() if rows else None,
            "last": rows[-1].timestamp.isoformat() if rows else None,
            "gaps": city_gaps,
        }

    return {
        "gap_threshold_minutes": gap_minutes,
        "duplicate_groups": duplicate_groups,
        "dedup_ok": len(duplicate_groups) == 0,
        "per_city": per_city,
        "gaps": gaps,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Audit readings for dedup anomalies and gaps.")
    p.add_argument("--database-url", default=None)
    p.add_argument("--gap-minutes", type=float, default=90.0)
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
        print(json.dumps(audit(session, args.gap_minutes), indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
