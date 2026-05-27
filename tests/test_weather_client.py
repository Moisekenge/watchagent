"""Weather-client tests. All HTTP is served by httpx.MockTransport — no real
network, satisfying the testing rule. Covers UTC normalization, the success
path, and the retry-then-raise behaviour.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import CITIES
from app.weather_client import WeatherAPIError, WeatherClient, parse_current

OTTAWA = next(c for c in CITIES if c.name == "Ottawa")


def test_parse_current_normalizes_to_utc():
    payload = {
        "utc_offset_seconds": -14400,  # UTC-04:00
        "current": {
            "time": "2026-05-26T08:00",
            "temperature_2m": 21.5,
            "apparent_temperature": 20.0,
            "precipitation": 0.0,
            "wind_speed_10m": 12.0,
            "weather_code": 3,
        },
    }
    reading = parse_current(payload, OTTAWA)
    assert reading.timestamp.tzinfo is not None
    assert reading.timestamp.hour == 12  # 08:00 local (-04:00) → 12:00 UTC
    assert reading.temperature_2m == 21.5
    assert reading.weather_code == 3


def test_fetch_success_returns_reading():
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "utc_offset_seconds": 0,
                "current": {
                    "time": "2026-05-26T12:00",
                    "temperature_2m": 10.0,
                    "apparent_temperature": 9.0,
                    "precipitation": 0.0,
                    "wind_speed_10m": 5.0,
                    "weather_code": 0,
                },
            },
        )

    client = WeatherClient(
        "https://example.test/forecast",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    reading = client.fetch_current(OTTAWA)
    assert reading.city == "Ottawa"
    assert reading.temperature_2m == 10.0


def test_fetch_retries_then_raises():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(503)

    client = WeatherClient(
        "https://example.test/forecast",
        max_retries=3,
        backoff_seconds=0,  # no real sleeping in tests
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(WeatherAPIError) as exc_info:
        client.fetch_current(OTTAWA)
    assert calls["n"] == 3
    assert exc_info.value.status == 503
    assert exc_info.value.city == "Ottawa"
