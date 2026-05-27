"""Framework-free domain objects shared across layers.

These dataclasses are the *currency* between the poller, the detection engine,
and storage. The detection engine speaks only in terms of ``ReadingData`` and
``EventData`` — it never imports SQLAlchemy — which is what lets the detection
unit tests construct sequences of readings by hand and assert on events without
a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Severity(StrEnum):
    INFO = "info"
    NOTABLE = "notable"
    SEVERE = "severe"


class EventType(StrEnum):
    ANOMALY = "anomaly"
    RAPID_CHANGE = "rapid_change"
    TREND = "trend"
    PRECIP_ONSET = "precip_onset"
    PRECIP_CESSATION = "precip_cessation"
    CONDITION_CHANGE = "condition_change"
    HIGH_WIND = "high_wind"
    CROSS_CITY_DIVERGENCE = "cross_city_divergence"


@dataclass
class ReadingData:
    """One weather observation for one city at one upstream timestamp."""

    city: str
    timestamp: datetime
    temperature_2m: float
    apparent_temperature: float
    precipitation: float
    wind_speed_10m: float
    weather_code: int

    def value(self, field_name: str) -> float:
        return float(getattr(self, field_name))


@dataclass
class EventData:
    """A notable event. Carries enough to answer what / where / when / why.

    * what  → ``event_type`` + ``field`` + ``reason``
    * where → ``city``
    * when  → ``reading_timestamp`` (the observation) and ``detected_at``
    * why   → ``observed_value`` vs ``baseline_value`` with the numeric
              ``deviation`` (z-score, delta, or spread) and a human ``reason``;
              ``context`` holds any extra machine-readable justification.
    """

    city: str
    event_type: EventType
    field: str
    severity: Severity
    reason: str
    reading_timestamp: datetime
    observed_value: float | None = None
    baseline_value: float | None = None
    deviation: float | None = None
    context: dict = field(default_factory=dict)
    detected_at: datetime | None = None

    def cooldown_key(self) -> tuple[str, str]:
        """Refractory bucket: one stream per (event_type, field)."""
        return (self.event_type.value, self.field)
