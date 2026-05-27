"""API-shape tests against a seeded SQLite dataset.

Asserts the exact response *contracts* for /health, /readings, /events —
keys present, ordering (most recent first), and the optional city/limit filters.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domain import EventData, EventType, ReadingData, Severity
from app.repository import store_events, store_reading

UTC = timezone.utc
BASE = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)

READING_FIELDS = {
    "id", "city", "timestamp", "temperature_2m", "apparent_temperature",
    "precipitation", "wind_speed_10m", "weather_code", "created_at",
}
EVENT_FIELDS = {
    "id", "city", "event_type", "field", "severity", "observed_value",
    "baseline_value", "deviation", "reason", "context",
    "reading_timestamp", "detected_at",
}


def _seed(session_factory):
    session = session_factory()
    for i in range(3):  # Ottawa: 3 readings, rising temp and time
        store_reading(
            session,
            ReadingData("Ottawa", BASE + timedelta(hours=i), 20.0 + i, 20.0 + i, 0.0, 10.0, 0),
        )
    store_reading(session, ReadingData("Toronto", BASE, 18.0, 18.0, 0.0, 12.0, 0))
    store_events(
        session,
        [
            EventData(
                city="Ottawa",
                event_type=EventType.ANOMALY,
                field="temperature_2m",
                severity=Severity.NOTABLE,
                reason="seeded test event",
                reading_timestamp=BASE,
                observed_value=30.0,
                baseline_value=20.0,
                deviation=4.2,
                detected_at=BASE,
            )
        ],
    )
    session.commit()
    session.close()


def test_health_reports_counts(client, session_factory):
    _seed(session_factory)
    body = client.get("/health").json()
    assert body == {"status": "ok", "readings_stored": 4, "events_stored": 1}


def test_readings_shape_ordering_and_filters(client, session_factory):
    _seed(session_factory)

    body = client.get("/readings").json()
    assert set(body) == {"readings"}
    assert len(body["readings"]) == 4
    assert READING_FIELDS <= set(body["readings"][0])

    ottawa = client.get("/readings?city=Ottawa").json()["readings"]
    assert len(ottawa) == 3
    assert all(r["city"] == "Ottawa" for r in ottawa)
    # most recent first → temperatures descend (20→22 over time)
    assert ottawa[0]["temperature_2m"] >= ottawa[-1]["temperature_2m"]

    assert len(client.get("/readings?limit=1").json()["readings"]) == 1


def test_events_shape_and_filters(client, session_factory):
    _seed(session_factory)

    body = client.get("/events").json()
    assert set(body) == {"events"}
    assert len(body["events"]) == 1
    assert EVENT_FIELDS <= set(body["events"][0])
    assert body["events"][0]["reason"] == "seeded test event"

    assert client.get("/events?city=Vancouver").json()["events"] == []
    assert client.get("/events?city=Ottawa").json()["events"][0]["city"] == "Ottawa"
