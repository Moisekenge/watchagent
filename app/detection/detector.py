"""Detection orchestrator.

``detect_events`` is the single public entry point. It runs every detector,
stamps ``detected_at``, then applies the cooldown (refractory) filter that turns
raw candidates into a *selective* event stream — the sensitivity-vs-noise
balance the challenge cares about.

Kept pure and side-effect free: no database, no clock except the injectable
``now``. The poller supplies real history/peers/cooldown-state from storage;
unit tests supply them by hand.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.config import DetectionConfig
from app.detection.rules import (
    detect_anomaly,
    detect_condition_change,
    detect_cross_city_divergence,
    detect_high_wind,
    detect_precip_transition,
    detect_rapid_change,
    detect_trend,
)
from app.domain import EventData, EventType, ReadingData


def _aware(dt: datetime) -> datetime:
    """Coerce naive datetimes (e.g. from SQLite) to UTC for safe comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _cooldown_hours(event: EventData, config: DetectionConfig) -> float:
    # Trends evolve slowly; a longer refractory period avoids re-announcing the
    # same multi-hour movement.
    if event.event_type == EventType.TREND:
        return config.trend_cooldown_hours
    return config.cooldown_hours


def suppress_by_cooldown(
    candidates: list[EventData],
    last_event_at: dict[tuple[str, str], datetime],
    config: DetectionConfig,
    now: datetime,
) -> list[EventData]:
    """Drop candidates whose (event_type, field) stream fired too recently.

    ``last_event_at`` maps a cooldown key → the most recent prior ``detected_at``
    for this city. The input dict is not mutated; a local copy also absorbs
    duplicates *within* the same batch.
    """
    state = dict(last_event_at)
    kept: list[EventData] = []
    for event in candidates:
        key = event.cooldown_key()
        window = _cooldown_hours(event, config)
        last = state.get(key)
        if last is not None:
            elapsed_h = (_aware(now) - _aware(last)).total_seconds() / 3600.0
            if elapsed_h < window:
                continue
        kept.append(event)
        state[key] = now
    return kept


def detect_events(
    reading: ReadingData,
    history: list[ReadingData],
    peers: dict[str, ReadingData] | None = None,
    last_event_at: dict[tuple[str, str], datetime] | None = None,
    config: DetectionConfig | None = None,
    now: datetime | None = None,
) -> list[EventData]:
    """Run all detectors for one new reading and return the events to persist.

    Args:
        reading: the newly stored reading for one city.
        history: that city's prior readings, oldest → newest, excluding ``reading``.
        peers: latest reading for each *other* monitored city (for divergence).
        last_event_at: per-city map (event_type, field) → last detected_at, for cooldown.
        config: detection thresholds; defaults to the balanced ``DetectionConfig``.
        now: detection timestamp; defaults to current UTC (injectable for tests).
    """
    config = config or DetectionConfig()
    peers = peers or {}
    last_event_at = last_event_at or {}
    # Normalize to tz-aware UTC so cooldown math works even when `now` comes from
    # a tz-naive source (e.g. a SQLite-backed replay).
    now = _aware(now or datetime.now(timezone.utc))

    candidates: list[EventData] = []
    candidates += detect_anomaly(reading, history, config)
    candidates += detect_rapid_change(reading, history, config)
    candidates += detect_trend(reading, history, config)
    candidates += detect_precip_transition(reading, history, config)
    candidates += detect_condition_change(reading, history, config)
    candidates += detect_high_wind(reading, history, config)
    candidates += detect_cross_city_divergence(reading, peers, config)

    for event in candidates:
        event.detected_at = now

    return suppress_by_cooldown(candidates, last_event_at, config, now)
