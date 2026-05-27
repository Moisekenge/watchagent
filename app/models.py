"""SQLAlchemy ORM models.

Deliberately DB-agnostic (standard column types, generic ``JSON``) so the same
models run on Postgres in production *and* on SQLite in the test suite — which
is why CI's test job needs no database service.

Deduplication is enforced at the schema level by a UNIQUE constraint on
``(city, timestamp)``; the repository also checks before inserting. Belt and
suspenders: even a race between concurrent pollers cannot store a duplicate.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        UniqueConstraint("city", "timestamp", name="uq_reading_city_timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    city: Mapped[str] = mapped_column(String(64), index=True)
    # The upstream observation time. Dedup key together with city.
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    temperature_2m: Mapped[float] = mapped_column(Float)
    apparent_temperature: Mapped[float] = mapped_column(Float)
    precipitation: Mapped[float] = mapped_column(Float)
    wind_speed_10m: Mapped[float] = mapped_column(Float)
    weather_code: Mapped[int] = mapped_column(Integer)

    # When our service first persisted this reading.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    city: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    # The measurement that triggered the event ("temperature_2m", "multi", ...).
    field: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16), index=True)

    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Numeric justification: z-score, delta, or spread depending on event_type.
    deviation: Mapped[float | None] = mapped_column(Float, nullable=True)

    reason: Mapped[str] = mapped_column(Text)
    # Extra machine-readable justification (window size, MAD, tier, peers, ...).
    context: Mapped[dict] = mapped_column(JSON, default=dict)

    # The observation this event describes (joins back to Reading.timestamp).
    reading_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
