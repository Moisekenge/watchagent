"""The poller process (``python -m app.poller``).

Runs as its own container, separate from the API, so the two have independent
lifecycles — the API restarting never interrupts collection, and a poller crash
never takes the API down. Both share the same image, code, and database.

Per cycle, for each city: fetch → dedupe-store → (only on a genuinely new
reading) detect → persist events. Failures are contained per city: a fetch
failure or unexpected error is logged and the loop moves on. A single bad city
or a transient Open-Meteo outage never stops the service.
"""

from __future__ import annotations

import logging
import signal
import time
from types import FrameType

from app.config import CITIES, City, DetectionConfig, build_detection_config, settings
from app.db import init_db, session_scope
from app.detection import detect_events
from app.logging_config import configure_logging
from app.repository import (
    get_history,
    last_event_times,
    latest_reading_per_city,
    store_events,
    store_reading,
)
from app.weather_client import WeatherAPIError, WeatherClient

logger = logging.getLogger("watchagent.poller")

_stop = False


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    global _stop
    logger.info("shutdown signal received", extra={"signal": signum})
    _stop = True


def _install_signal_handlers() -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):  # not on main thread / unsupported platform
            pass


def _interruptible_sleep(seconds: float) -> None:
    """Sleep in short slices so a shutdown signal is honoured promptly."""
    deadline = time.monotonic() + seconds
    while not _stop and time.monotonic() < deadline:
        time.sleep(min(1.0, deadline - time.monotonic()))


def poll_city(client: WeatherClient, config: DetectionConfig, city: City) -> tuple[bool, int]:
    """Fetch one city, store if new, run detection. Returns (stored_new, n_events).

    The network fetch happens *outside* the DB transaction so we never hold a
    transaction open across I/O.
    """
    reading = client.fetch_current(city)  # may raise WeatherAPIError

    with session_scope() as session:
        _, created = store_reading(session, reading)
        if not created:
            logger.debug(
                "duplicate reading skipped",
                extra={"city": city.name, "timestamp": reading.timestamp.isoformat()},
            )
            return False, 0

        history = get_history(session, city.name, reading.timestamp, config.rolling_window)
        peers = latest_reading_per_city(session, exclude_city=city.name)
        cooldown = last_event_times(session, city.name)
        events = detect_events(reading, history, peers, cooldown, config)
        store_events(session, events)

        logger.info(
            "stored reading",
            extra={
                "city": city.name,
                "timestamp": reading.timestamp.isoformat(),
                "events_detected": len(events),
                "event_types": [e.event_type.value for e in events],
            },
        )
        return True, len(events)


def run_cycle(client: WeatherClient, config: DetectionConfig) -> tuple[int, int]:
    stored = detected = 0
    for city in CITIES:
        try:
            created, n = poll_city(client, config, city)
            stored += int(created)
            detected += n
        except WeatherAPIError as exc:
            logger.warning(
                "skipping city after fetch failure",
                extra={"city": exc.city, "http_status": exc.status, "attempts": exc.attempts},
            )
        except Exception:
            logger.exception("unexpected error polling city", extra={"city": city.name})
    return stored, detected


def main() -> None:
    configure_logging(settings.log_level)
    _install_signal_handlers()
    logger.info(
        "poller starting",
        extra={
            "interval_seconds": settings.poll_interval_seconds,
            "cities": [c.name for c in CITIES],
        },
    )
    init_db()

    client = WeatherClient(
        base_url=settings.weather_api_base_url,
        timeout=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_seconds=settings.retry_backoff_seconds,
    )
    config = build_detection_config(settings)

    try:
        while not _stop:
            stored, detected = run_cycle(client, config)
            logger.info(
                "poll cycle complete",
                extra={"readings_stored": stored, "events_detected": detected},
            )
            _interruptible_sleep(settings.poll_interval_seconds)
    finally:
        client.close()
        logger.info("poller stopped")


if __name__ == "__main__":
    main()
