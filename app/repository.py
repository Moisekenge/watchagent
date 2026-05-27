"""Data-access layer: the only module that talks to the ORM directly.

Everything here takes an explicit ``Session`` so the same code runs against
Postgres in production and SQLite in tests. Conversions between ORM rows and the
framework-free domain dataclasses live here too, keeping the detection engine
and the poller ignorant of SQLAlchemy.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain import EventData, ReadingData
from app.models import Event, Reading


# --- conversions ------------------------------------------------------------
def to_reading_data(row: Reading) -> ReadingData:
    return ReadingData(
        city=row.city,
        timestamp=row.timestamp,
        temperature_2m=row.temperature_2m,
        apparent_temperature=row.apparent_temperature,
        precipitation=row.precipitation,
        wind_speed_10m=row.wind_speed_10m,
        weather_code=row.weather_code,
    )


def _find_reading(session: Session, city: str, timestamp: datetime) -> Reading | None:
    return session.scalar(
        select(Reading).where(Reading.city == city, Reading.timestamp == timestamp)
    )


# --- writes -----------------------------------------------------------------
def store_reading(session: Session, data: ReadingData) -> tuple[Reading | None, bool]:
    """Insert a reading unless one already exists for (city, timestamp).

    Returns ``(row, created)``. Deduplication is enforced two ways: a pre-insert
    lookup for the common case, and the UNIQUE(city, timestamp) constraint as the
    hard guarantee against any race.
    """
    existing = _find_reading(session, data.city, data.timestamp)
    if existing is not None:
        return existing, False

    row = Reading(
        city=data.city,
        timestamp=data.timestamp,
        temperature_2m=data.temperature_2m,
        apparent_temperature=data.apparent_temperature,
        precipitation=data.precipitation,
        wind_speed_10m=data.wind_speed_10m,
        weather_code=data.weather_code,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return _find_reading(session, data.city, data.timestamp), False
    return row, True


def store_events(session: Session, events: list[EventData]) -> int:
    for e in events:
        session.add(
            Event(
                city=e.city,
                event_type=e.event_type.value,
                field=e.field,
                severity=e.severity.value,
                observed_value=e.observed_value,
                baseline_value=e.baseline_value,
                deviation=e.deviation,
                reason=e.reason,
                context=e.context,
                reading_timestamp=e.reading_timestamp,
                detected_at=e.detected_at,
            )
        )
    session.flush()
    return len(events)


# --- reads for the detection pipeline --------------------------------------
def get_history(
    session: Session, city: str, before: datetime, window: int
) -> list[ReadingData]:
    """The city's ``window`` most recent readings strictly *before* ``before``,
    returned oldest → newest (the order the detectors expect)."""
    rows = session.scalars(
        select(Reading)
        .where(Reading.city == city, Reading.timestamp < before)
        .order_by(Reading.timestamp.desc())
        .limit(window)
    ).all()
    return [to_reading_data(r) for r in reversed(rows)]


def latest_reading_per_city(
    session: Session, exclude_city: str | None = None
) -> dict[str, ReadingData]:
    """Most recent reading for each city (optionally excluding one)."""
    cities = session.scalars(select(Reading.city).distinct()).all()
    out: dict[str, ReadingData] = {}
    for city in cities:
        if city == exclude_city:
            continue
        row = session.scalar(
            select(Reading)
            .where(Reading.city == city)
            .order_by(Reading.timestamp.desc())
            .limit(1)
        )
        if row is not None:
            out[city] = to_reading_data(row)
    return out


def last_event_times(session: Session, city: str) -> dict[tuple[str, str], datetime]:
    """Per-city map (event_type, field) → latest ``detected_at``, for cooldown."""
    rows = session.execute(
        select(Event.event_type, Event.field, func.max(Event.detected_at))
        .where(Event.city == city)
        .group_by(Event.event_type, Event.field)
    ).all()
    return {(etype, fld): ts for etype, fld, ts in rows if ts is not None}


# --- reads for the API ------------------------------------------------------
def count_readings(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Reading)) or 0


def count_events(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Event)) or 0


def get_recent_readings(
    session: Session, city: str | None = None, limit: int = 50
) -> list[Reading]:
    stmt = select(Reading)
    if city:
        stmt = stmt.where(Reading.city == city)
    return list(session.scalars(stmt.order_by(Reading.timestamp.desc()).limit(limit)).all())


def get_recent_events(
    session: Session, city: str | None = None, limit: int = 50
) -> list[Event]:
    stmt = select(Event)
    if city:
        stmt = stmt.where(Event.city == city)
    return list(session.scalars(stmt.order_by(Event.detected_at.desc()).limit(limit)).all())
