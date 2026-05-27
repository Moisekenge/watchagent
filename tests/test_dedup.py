"""Deduplication: the weather API is mocked to return the same reading twice;
only one row may be stored. Exercises the full poller path (fetch → store),
with the network call and the DB both faked/local.
"""

from __future__ import annotations

from contextlib import contextmanager

import app.poller as poller_module
from app.config import CITIES, DetectionConfig
from app.repository import count_readings

OTTAWA = next(c for c in CITIES if c.name == "Ottawa")


class FakeClient:
    """Stands in for WeatherClient — returns a fixed reading, counts calls."""

    def __init__(self, reading):
        self.reading = reading
        self.calls = 0

    def fetch_current(self, _city):
        self.calls += 1
        return self.reading

    def close(self):
        pass


def test_duplicate_reading_stored_once(monkeypatch, session_factory, make_reading):
    @contextmanager
    def scope():
        s = session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # Point the poller's transactional scope at the in-memory test database.
    monkeypatch.setattr(poller_module, "session_scope", scope)

    reading = make_reading(city="Ottawa")  # identical timestamp on both polls
    client = FakeClient(reading)
    config = DetectionConfig()

    created_first, _ = poller_module.poll_city(client, config, OTTAWA)
    created_second, _ = poller_module.poll_city(client, config, OTTAWA)

    assert created_first is True
    assert created_second is False
    assert client.calls == 2  # the API really was polled twice
    with session_factory() as session:
        assert count_readings(session) == 1  # but only one row exists
