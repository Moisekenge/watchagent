#!/usr/bin/env python3
"""Generate a reproducible, representative weather dataset for demos and tuning.

Open-Meteo only refreshes hourly, so accumulating enough *real* history to show
interesting events takes a day or more. This script synthesizes a deterministic
(seeded) multi-day dataset with realistic diurnal temperature cycles per city
plus a handful of injected weather episodes (a heat wave, a cold front, a storm,
sustained high wind, rain onset/cessation). It runs the readings through the
real storage + detection pipeline, so the resulting database is exactly what the
poller would have produced.

Use it to (a) populate the API/skills with data without waiting for live weather,
and (b) reproduce the tuning numbers quoted in the README:

    python scripts/generate_demo_data.py --database-url sqlite:///demo.db --reset
    python .cursor/skills/replay-detection/scripts/replay.py --database-url sqlite:///demo.db
    python .cursor/skills/replay-detection/scripts/replay.py --database-url sqlite:///demo.db --no-cooldown

The maritime/continental amplitude difference (Vancouver vs Ottawa) is real in
the generated data, which is what lets the per-city calibration demonstrably
flag swings in Vancouver that it correctly ignores in Ottawa.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import DetectionConfig  # noqa: E402
from app.db import init_db, make_engine  # noqa: E402
from app.detection import detect_events  # noqa: E402
from app.domain import ReadingData  # noqa: E402
from app.models import Event, Reading  # noqa: E402
from app.repository import (  # noqa: E402
    get_history,
    last_event_times,
    latest_reading_per_city,
    store_events,
    store_reading,
)

DEFAULT_URL = "postgresql+psycopg2://watchagent:watchagent_local_dev@127.0.0.1:5432/watchagent"

# Per-city diurnal model: (mean °C, daily amplitude °C). Vancouver's small
# amplitude is the maritime signal that makes its baseline MAD small.
CITY_CLIMATE = {
    "Ottawa": (18.0, 7.0),
    "Toronto": (16.0, 6.0),
    "Vancouver": (14.0, 3.0),
}


def _diurnal(mean: float, amp: float, hour_of_day: int, rng: random.Random) -> float:
    # Coldest ~5am, warmest ~3pm.
    base = mean + amp * math.sin(2 * math.pi * (hour_of_day - 9) / 24.0)
    return base + rng.gauss(0, 0.7)


def generate(hours: int, seed: int) -> dict[str, list[ReadingData]]:
    rng = random.Random(seed)
    start = datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc)
    out: dict[str, list[ReadingData]] = {c: [] for c in CITY_CLIMATE}

    for city, (mean, amp) in CITY_CLIMATE.items():
        for h in range(hours):
            ts = start + timedelta(hours=h)
            temp = _diurnal(mean, amp, ts.hour, rng)
            precip, wind, code = 0.0, rng.uniform(5, 18), 0

            # --- injected episodes (deterministic) -------------------------
            if city == "Ottawa":
                # A sustained heat wave (hours 30-36): level stays extreme, so
                # without cooldown an anomaly would fire every hour.
                if 30 <= h <= 36:
                    temp += 11.0
                # A thunderstorm with high wind (hours 50-53).
                if 50 <= h <= 53:
                    code, wind, precip = 95, rng.uniform(55, 70), rng.uniform(3, 9)
            elif city == "Toronto":
                # A cold front: a steady multi-hour drop (trend + rapid_change).
                if 40 <= h <= 46:
                    temp -= 2.6 * (h - 39)
                # Rain onset then cessation.
                if 44 <= h <= 47:
                    code, precip = 63, rng.uniform(1, 5)
            elif city == "Vancouver":
                # A modest +6 spike at a calm hour. In maritime Vancouver this
                # is a clear anomaly; the same swing in Ottawa is within normal.
                if h == 33:
                    temp += 6.0
                # A long drizzle spell.
                if 20 <= h <= 26:
                    code, precip = 51, rng.uniform(0.2, 1.5)

            out[city].append(
                ReadingData(
                    city=city,
                    timestamp=ts,
                    temperature_2m=round(temp, 1),
                    apparent_temperature=round(temp - 1.2, 1),
                    precipitation=round(precip, 1),
                    wind_speed_10m=round(wind, 1),
                    weather_code=code,
                )
            )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate representative demo data.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_URL))
    parser.add_argument("--hours", type=int, default=72, help="Hours of history per city.")
    parser.add_argument("--seed", type=int, default=7, help="RNG seed for reproducibility.")
    parser.add_argument("--reset", action="store_true", help="Delete existing rows first.")
    args = parser.parse_args(argv)

    engine = make_engine(args.database_url)
    init_db(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    if args.reset:
        with Session() as s:
            s.query(Event).delete()
            s.query(Reading).delete()
            s.commit()

    by_city = generate(args.hours, args.seed)
    # Interleave by timestamp to mimic real polling order across cities.
    rows = sorted(
        (r for rs in by_city.values() for r in rs),
        key=lambda r: (r.timestamp, r.city),
    )

    config = DetectionConfig()
    readings = events = 0
    for r in rows:
        with Session() as s:
            _, created = store_reading(s, r)
            if created:
                readings += 1
                hist = get_history(s, r.city, r.timestamp, config.rolling_window)
                peers = latest_reading_per_city(s, exclude_city=r.city)
                cd = last_event_times(s, r.city)
                evs = detect_events(r, hist, peers, cd, config, now=r.timestamp)
                store_events(s, evs)
                events += len(evs)
            s.commit()

    print(f"generated readings={readings} events={events} into {args.database_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
