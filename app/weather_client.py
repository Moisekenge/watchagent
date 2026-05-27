"""Open-Meteo client.

Fetches the ``current`` block for a city and normalizes it into a
``ReadingData``. Two details matter:

* **Timestamps are converted to UTC.** Open-Meteo (with ``timezone=auto``)
  returns local wall-clock time plus ``utc_offset_seconds``. We store UTC so
  readings from cities in different time zones are directly comparable — which
  the cross-city detector relies on.
* **Transient failures are retried with linear backoff** and logged at WARNING
  with the city, HTTP status, and attempt count. After exhausting retries the
  client raises ``WeatherAPIError`` (which the poller catches and survives).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from app.config import City
from app.domain import ReadingData

logger = logging.getLogger("watchagent.weather")

_CURRENT_FIELDS = (
    "temperature_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code"
)


class WeatherAPIError(Exception):
    """Raised when a city's reading cannot be fetched after all retries."""

    def __init__(self, city: str, message: str, status: int | None = None, attempts: int = 0):
        super().__init__(message)
        self.city = city
        self.status = status
        self.attempts = attempts


def build_params(city: City) -> dict[str, object]:
    return {
        "latitude": city.latitude,
        "longitude": city.longitude,
        "current": _CURRENT_FIELDS,
        "wind_speed_unit": "kmh",
        "timezone": "auto",
    }


def parse_current(payload: dict, city: City) -> ReadingData:
    """Turn an Open-Meteo response into a UTC-normalized ReadingData."""
    current = payload["current"]
    offset = int(payload.get("utc_offset_seconds", 0))
    local = datetime.fromisoformat(current["time"])  # naive local wall-clock
    ts_utc = (local - timedelta(seconds=offset)).replace(tzinfo=timezone.utc)
    return ReadingData(
        city=city.name,
        timestamp=ts_utc,
        temperature_2m=float(current["temperature_2m"]),
        apparent_temperature=float(current["apparent_temperature"]),
        precipitation=float(current["precipitation"]),
        wind_speed_10m=float(current["wind_speed_10m"]),
        weather_code=int(current["weather_code"]),
    )


class WeatherClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_seconds: float = 2.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._client = client or httpx.Client(timeout=timeout)

    def fetch_current(self, city: City) -> ReadingData:
        params = build_params(city)
        last_status: int | None = None
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._client.get(self.base_url, params=params)
                resp.raise_for_status()
                return parse_current(resp.json(), city)
            except httpx.HTTPStatusError as exc:
                last_status = exc.response.status_code
                last_error = exc
                logger.warning(
                    "weather fetch returned error status",
                    extra={
                        "city": city.name,
                        "http_status": last_status,
                        "attempt": attempt,
                        "max_retries": self.max_retries,
                    },
                )
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "weather fetch failed",
                    extra={
                        "city": city.name,
                        "error": type(exc).__name__,
                        "attempt": attempt,
                        "max_retries": self.max_retries,
                    },
                )
            if attempt < self.max_retries:
                time.sleep(self.backoff_seconds * attempt)

        raise WeatherAPIError(
            city=city.name,
            message=f"failed to fetch {city.name} after {self.max_retries} attempts",
            status=last_status,
            attempts=self.max_retries,
        ) from last_error

    def close(self) -> None:
        self._client.close()
