"""Runtime configuration.

Two distinct config objects live here on purpose:

* ``Settings`` reads the *environment* (12-factor style) and owns operational
  concerns: database URL, poll interval, logging, HTTP timeouts.
* ``DetectionConfig`` is a plain, frozen dataclass holding every threshold the
  detection engine uses. It is deliberately decoupled from the environment so
  the detectors stay pure and unit-testable — a test constructs a
  ``DetectionConfig`` directly without touching env vars or the database.

``build_detection_config()`` bridges the two: it projects the env-derived
``Settings`` onto a ``DetectionConfig``, applying the shipped "balanced"
defaults for everything not overridden.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Monitored cities -------------------------------------------------------
# Fixed by the challenge spec. Kept in code (not env) because they define the
# system's identity, not its deployment.


@dataclass(frozen=True)
class City:
    name: str
    latitude: float
    longitude: float


CITIES: tuple[City, ...] = (
    City("Ottawa", 45.42, -75.69),
    City("Toronto", 43.70, -79.42),
    City("Vancouver", 49.25, -123.12),
)

# Numeric fields for which a per-city statistical baseline is meaningful.
# precipitation is excluded (zero-inflated → handled by a state machine) and
# weather_code is excluded (categorical → handled by tier transitions).
ANOMALY_FIELDS: tuple[str, ...] = (
    "temperature_2m",
    "apparent_temperature",
    "wind_speed_10m",
)


class Settings(BaseSettings):
    """Operational settings sourced from the environment / ``.env`` file."""

    # Storage
    database_url: str = (
        "postgresql+psycopg2://watchagent:watchagent_local_dev@db:5432/watchagent"
    )

    # Poller
    poll_interval_seconds: int = 300
    weather_api_base_url: str = "https://api.open-meteo.com/v1/forecast"
    request_timeout_seconds: float = 10.0
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0

    # Logging
    log_level: str = "INFO"

    # Detection tuning (optional overrides; see DetectionConfig for the meaning)
    rolling_window: int = 48
    min_history: int = 8
    anomaly_z: float = 3.5
    cooldown_hours: float = 3.0
    cross_city_spread_c: float = 18.0

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


@dataclass(frozen=True)
class DetectionConfig:
    """Every knob the detection engine turns. Pure data, no I/O.

    Defaults encode the shipped *balanced* posture: fire on genuinely notable
    events, stay quiet on ordinary weather.
    """

    # History / warm-up
    rolling_window: int = 48  # readings retained per city for baselines (~2 days hourly)
    min_history: int = 8  # statistical detectors stay disarmed below this

    # Anomalous reading (robust modified z-score vs the city's own baseline)
    anomaly_z: float = 3.5
    anomaly_severe_z: float = 5.0

    # Rapid change (hour-over-hour delta vs the city's delta distribution)
    rapid_change_z: float = 3.0
    # Absolute floors stop tiny deltas from firing when a series is so flat that
    # its delta-MAD is near zero. Units match the field.
    rapid_change_floor: dict[str, float] = field(
        default_factory=lambda: {
            "temperature_2m": 3.0,
            "apparent_temperature": 3.0,
            "wind_speed_10m": 20.0,
        }
    )
    rapid_change_severe: dict[str, float] = field(
        default_factory=lambda: {
            "temperature_2m": 7.0,
            "apparent_temperature": 8.0,
            "wind_speed_10m": 40.0,
        }
    )

    # Sustained trend (monotonic run with cumulative magnitude)
    trend_window: int = 4
    trend_min_cumulative: dict[str, float] = field(
        default_factory=lambda: {
            "temperature_2m": 6.0,
            "apparent_temperature": 7.0,
            "wind_speed_10m": 30.0,
        }
    )

    # High wind (absolute safety tiers, km/h)
    wind_strong_kmh: float = 40.0
    wind_gale_kmh: float = 62.0

    # Precipitation intensity tiers (mm/h), standard rainfall-rate classes
    precip_moderate_mm: float = 2.5
    precip_heavy_mm: float = 7.6

    # Cross-city divergence (max-min temperature spread across monitored cities)
    cross_city_spread_c: float = 18.0
    cross_city_severe_c: float = 28.0

    # Noise control: refractory period per (city, event_type, field)
    cooldown_hours: float = 3.0
    trend_cooldown_hours: float = 6.0


def build_detection_config(settings: Settings) -> DetectionConfig:
    """Project env-derived ``Settings`` onto a ``DetectionConfig``."""
    return DetectionConfig(
        rolling_window=settings.rolling_window,
        min_history=settings.min_history,
        anomaly_z=settings.anomaly_z,
        cooldown_hours=settings.cooldown_hours,
        cross_city_spread_c=settings.cross_city_spread_c,
    )


settings = Settings()
