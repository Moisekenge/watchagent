"""Shared test fixtures.

Everything runs against an in-memory SQLite database (StaticPool so the single
connection is shared across sessions/threads). No Postgres, no network — exactly
what the testing rule requires.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.db import init_db, make_engine
from app.domain import ReadingData

UTC = timezone.utc


@pytest.fixture
def engine():
    eng = make_engine("sqlite:///:memory:")
    init_db(eng)
    return eng


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def session(session_factory) -> Iterator[Session]:
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client(session_factory):
    """FastAPI TestClient with the DB dependency pointed at SQLite.

    Instantiated without the context-manager form so the app's lifespan (which
    would call init_db on the real Postgres engine) does not run.
    """
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app

    def _override() -> Iterator[Session]:
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


# --- builders ---------------------------------------------------------------
@pytest.fixture
def make_reading():
    """Factory for ReadingData with sensible defaults."""

    base_time = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)

    def _make(
        city: str = "Ottawa",
        timestamp: datetime | None = None,
        temperature_2m: float = 20.0,
        apparent_temperature: float | None = None,
        precipitation: float = 0.0,
        wind_speed_10m: float = 10.0,
        weather_code: int = 0,
    ) -> ReadingData:
        return ReadingData(
            city=city,
            timestamp=timestamp or base_time,
            temperature_2m=temperature_2m,
            apparent_temperature=(
                apparent_temperature if apparent_temperature is not None else temperature_2m
            ),
            precipitation=precipitation,
            wind_speed_10m=wind_speed_10m,
            weather_code=weather_code,
        )

    return _make


@pytest.fixture
def stable_history(make_reading):
    """A warm-up baseline: ``n`` readings alternating ±0.5°C around ``base``.

    The small alternation gives a non-zero MAD (~0.5) so the modified z-score is
    well defined, while keeping wind/apparent constant so only temperature has a
    baseline to deviate from.
    """

    def _build(n: int = 10, base: float = 20.0, city: str = "Ottawa") -> list[ReadingData]:
        start = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
        out = []
        for i in range(n):
            temp = base + (0.5 if i % 2 else -0.5)
            out.append(
                make_reading(
                    city=city,
                    timestamp=start + timedelta(hours=i),
                    temperature_2m=temp,
                    apparent_temperature=base,  # constant → no apparent-temp signal
                    wind_speed_10m=10.0,  # constant → no wind signal
                )
            )
        return out

    return _build
