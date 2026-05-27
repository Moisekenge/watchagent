"""The individual notable-event detectors.

Each detector is a pure function: it takes the new ``ReadingData``, the city's
prior ``history`` (oldest → newest, *excluding* the new reading), and a
``DetectionConfig``; it returns a list of *candidate* ``EventData``. Cooldown /
de-duplication is applied separately in ``detector.py`` so each rule stays a
clean expression of "what is notable", independent of "how often we say so".

The detectors are intentionally layered by the *kind* of signal they read,
because the challenge stresses that different fields carry different signal:

* statistical (level)      → anomaly
* statistical (change)     → rapid_change
* statistical (trajectory) → trend
* state machine            → precipitation onset / cessation
* categorical              → condition_change (WMO tiers)
* absolute safety tiers    → high_wind
* relational               → cross_city_divergence
"""

from __future__ import annotations

from app.config import ANOMALY_FIELDS, DetectionConfig
from app.detection.baselines import (
    consecutive_deltas,
    modified_zscore,
    robust_stats,
)
from app.detection.wmo import describe, severity_tier
from app.domain import EventData, EventType, ReadingData, Severity

FIELD_LABELS = {
    "temperature_2m": "Temperature",
    "apparent_temperature": "Apparent temperature",
    "wind_speed_10m": "Wind speed",
}
FIELD_UNITS = {
    "temperature_2m": "°C",
    "apparent_temperature": "°C",
    "wind_speed_10m": " km/h",
}


def _values(history: list[ReadingData], field: str) -> list[float]:
    return [r.value(field) for r in history]


# --- 1. Anomalous reading ---------------------------------------------------
def detect_anomaly(
    reading: ReadingData, history: list[ReadingData], config: DetectionConfig
) -> list[EventData]:
    """Fire when a field is a robust-z outlier vs the city's own baseline."""
    if len(history) < config.min_history:
        return []  # cold start: not enough history to define "normal"

    events: list[EventData] = []
    for field in ANOMALY_FIELDS:
        values = _values(history, field)
        med, m = robust_stats(values)
        x = reading.value(field)
        z = modified_zscore(x, values)
        if abs(z) < config.anomaly_z:
            continue
        severity = Severity.SEVERE if abs(z) >= config.anomaly_severe_z else Severity.NOTABLE
        unit = FIELD_UNITS[field]
        direction = "above" if z > 0 else "below"
        reason = (
            f"{FIELD_LABELS[field]} {x:.1f}{unit} in {reading.city} is "
            f"{abs(z):.1f}σ {direction} its {len(values)}-reading baseline "
            f"(median {med:.1f}{unit})."
        )
        events.append(
            EventData(
                city=reading.city,
                event_type=EventType.ANOMALY,
                field=field,
                severity=severity,
                reason=reason,
                reading_timestamp=reading.timestamp,
                observed_value=round(x, 2),
                baseline_value=round(med, 2),
                deviation=round(z, 2),
                context={"mad": round(m, 3), "window": len(values), "method": "modified_zscore"},
            )
        )
    return events


# --- 2. Rapid change --------------------------------------------------------
def detect_rapid_change(
    reading: ReadingData, history: list[ReadingData], config: DetectionConfig
) -> list[EventData]:
    """Fire on a step change vs the previous reading that is large both in
    absolute terms (floor) and relative to the city's own delta distribution."""
    if not history:
        return []
    prev = history[-1]
    events: list[EventData] = []

    for field in ANOMALY_FIELDS:
        x, p = reading.value(field), prev.value(field)
        delta = x - p
        if abs(delta) < config.rapid_change_floor[field]:
            continue  # too small to matter regardless of statistics

        past_deltas = consecutive_deltas(_values(history, field))
        z = None
        if len(past_deltas) >= 3:
            z = modified_zscore(delta, past_deltas)
            if abs(z) < config.rapid_change_z:
                continue  # large-ish but normal for this city's variability
        # else: cold start — the absolute floor alone justifies the event.

        severe = abs(delta) >= config.rapid_change_severe[field]
        severity = Severity.SEVERE if severe else Severity.NOTABLE
        unit = FIELD_UNITS[field]
        verb = "rose" if delta > 0 else "fell"
        reason = (
            f"{FIELD_LABELS[field]} {verb} {abs(delta):.1f}{unit} in {reading.city} "
            f"since the previous reading ({p:.1f} → {x:.1f}{unit})."
        )
        events.append(
            EventData(
                city=reading.city,
                event_type=EventType.RAPID_CHANGE,
                field=field,
                severity=severity,
                reason=reason,
                reading_timestamp=reading.timestamp,
                observed_value=round(x, 2),
                baseline_value=round(p, 2),
                deviation=round(delta, 2),
                context={
                    "delta_z": round(z, 2) if z is not None else None,
                    "previous_timestamp": prev.timestamp.isoformat(),
                },
            )
        )
    return events


# --- 3. Sustained trend -----------------------------------------------------
def detect_trend(
    reading: ReadingData, history: list[ReadingData], config: DetectionConfig
) -> list[EventData]:
    """Fire on a monotonic run over the window whose cumulative move is large.

    Catches gradual fronts that no single hour-over-hour delta would flag."""
    window = config.trend_window
    seq = [*history[-(window - 1):], reading]
    if len(seq) < window:
        return []

    events: list[EventData] = []
    for field in ANOMALY_FIELDS:
        vals = [r.value(field) for r in seq]
        deltas = consecutive_deltas(vals)
        rising = all(d >= 0 for d in deltas) and any(d > 0 for d in deltas)
        falling = all(d <= 0 for d in deltas) and any(d < 0 for d in deltas)
        if not (rising or falling):
            continue
        cumulative = vals[-1] - vals[0]
        if abs(cumulative) < config.trend_min_cumulative[field]:
            continue
        unit = FIELD_UNITS[field]
        verb = "climbed" if cumulative > 0 else "dropped"
        reason = (
            f"{FIELD_LABELS[field]} {verb} steadily in {reading.city} over "
            f"{window} readings ({vals[0]:.1f} → {vals[-1]:.1f}{unit}, "
            f"{cumulative:+.1f}{unit})."
        )
        events.append(
            EventData(
                city=reading.city,
                event_type=EventType.TREND,
                field=field,
                severity=Severity.NOTABLE,
                reason=reason,
                reading_timestamp=reading.timestamp,
                observed_value=round(vals[-1], 2),
                baseline_value=round(vals[0], 2),
                deviation=round(cumulative, 2),
                context={"window": window, "direction": "rising" if rising else "falling"},
            )
        )
    return events


# --- 4. Precipitation onset / cessation ------------------------------------
def _precip_intensity(mm: float, config: DetectionConfig) -> str:
    if mm >= config.precip_heavy_mm:
        return "heavy"
    if mm >= config.precip_moderate_mm:
        return "moderate"
    return "light"


def detect_precip_transition(
    reading: ReadingData, history: list[ReadingData], config: DetectionConfig
) -> list[EventData]:
    """Precipitation is zero-inflated, so a z-score is meaningless. Treat it as
    a state machine: dry → wet (onset) and wet → dry (cessation)."""
    if not history:
        return []
    prev, cur = history[-1].precipitation, reading.precipitation

    if prev == 0 and cur > 0:
        intensity = _precip_intensity(cur, config)
        severity = {
            "heavy": Severity.SEVERE,
            "moderate": Severity.NOTABLE,
            "light": Severity.INFO,
        }[intensity]
        return [
            EventData(
                city=reading.city,
                event_type=EventType.PRECIP_ONSET,
                field="precipitation",
                severity=severity,
                reason=f"Precipitation began in {reading.city}: {cur:.1f} mm/h ({intensity}).",
                reading_timestamp=reading.timestamp,
                observed_value=round(cur, 2),
                baseline_value=0.0,
                deviation=round(cur, 2),
                context={"intensity": intensity},
            )
        ]
    if prev > 0 and cur == 0:
        return [
            EventData(
                city=reading.city,
                event_type=EventType.PRECIP_CESSATION,
                field="precipitation",
                severity=Severity.INFO,
                reason=f"Precipitation stopped in {reading.city} (was {prev:.1f} mm/h).",
                reading_timestamp=reading.timestamp,
                observed_value=0.0,
                baseline_value=round(prev, 2),
                deviation=round(-prev, 2),
                context={},
            )
        ]
    return []


# --- 5. Weather condition change (WMO tier transition) ----------------------
def detect_condition_change(
    reading: ReadingData, history: list[ReadingData], config: DetectionConfig
) -> list[EventData]:
    """Fire on categorical transitions into/out of/within significant weather.

    A change of *kind* (clear → thunderstorm) is notable independent of any
    numeric magnitude. "Significant" means WMO tier >= 2 (rain/snow and worse).
    """
    if not history:
        return []
    prev_code, cur_code = history[-1].weather_code, reading.weather_code
    pt, ct = severity_tier(prev_code), severity_tier(cur_code)
    prev_sig, cur_sig = pt >= 2, ct >= 2
    desc_prev, desc_cur = describe(prev_code), describe(cur_code)

    reason = severity = None
    if cur_sig and not prev_sig:
        severity = Severity.SEVERE if ct >= 4 else Severity.NOTABLE
        reason = f"Weather in {reading.city} turned to {desc_cur.lower()} (was {desc_prev.lower()})."
    elif prev_sig and not cur_sig:
        severity = Severity.INFO
        reason = f"Weather in {reading.city} cleared to {desc_cur.lower()} (was {desc_prev.lower()})."
    elif cur_sig and prev_sig and ct > pt:
        severity = Severity.SEVERE if ct >= 4 else Severity.NOTABLE
        reason = f"Weather in {reading.city} escalated: {desc_prev.lower()} → {desc_cur.lower()}."

    if reason is None:
        return []
    return [
        EventData(
            city=reading.city,
            event_type=EventType.CONDITION_CHANGE,
            field="weather_code",
            severity=severity,
            reason=reason,
            reading_timestamp=reading.timestamp,
            observed_value=float(cur_code),
            baseline_value=float(prev_code),
            deviation=float(ct - pt),
            context={"from": desc_prev, "to": desc_cur, "from_tier": pt, "to_tier": ct},
        )
    ]


# --- 6. High wind (absolute safety tiers) -----------------------------------
def detect_high_wind(
    reading: ReadingData, history: list[ReadingData], config: DetectionConfig
) -> list[EventData]:
    """Wind has hard, human-meaningful thresholds independent of local norms.
    Fire only when a tier is *newly* crossed upward (the anomaly detector
    separately covers "unusually windy for this city")."""

    def tier(speed: float) -> int:
        if speed >= config.wind_gale_kmh:
            return 2
        if speed >= config.wind_strong_kmh:
            return 1
        return 0

    cur = reading.wind_speed_10m
    prev = history[-1].wind_speed_10m if history else 0.0
    ct, pt = tier(cur), tier(prev)
    if ct <= pt or ct == 0:
        return []

    if ct == 2:
        severity, label, threshold = Severity.SEVERE, "gale-force", config.wind_gale_kmh
    else:
        severity, label, threshold = Severity.NOTABLE, "strong", config.wind_strong_kmh
    return [
        EventData(
            city=reading.city,
            event_type=EventType.HIGH_WIND,
            field="wind_speed_10m",
            severity=severity,
            reason=f"Wind in {reading.city} reached {label} levels: {cur:.0f} km/h.",
            reading_timestamp=reading.timestamp,
            observed_value=round(cur, 2),
            baseline_value=round(threshold, 2),
            deviation=round(cur - threshold, 2),
            context={"tier": label, "threshold_kmh": threshold},
        )
    ]


# --- 7. Cross-city divergence -----------------------------------------------
def detect_cross_city_divergence(
    reading: ReadingData,
    peers: dict[str, ReadingData],
    config: DetectionConfig,
) -> list[EventData]:
    """Fire when the temperature spread across monitored cities is extreme.

    Attributed only to the city at an extreme (warmest or coldest), so a single
    divergent moment produces one event, not one per city."""
    temps = {reading.city: reading.temperature_2m}
    for name, r in peers.items():
        temps[name] = r.temperature_2m
    if len(temps) < 2:
        return []

    hi_city = max(temps, key=temps.get)
    lo_city = min(temps, key=temps.get)
    spread = temps[hi_city] - temps[lo_city]
    if spread < config.cross_city_spread_c:
        return []
    if reading.city not in (hi_city, lo_city):
        return []  # not an extreme — let the extreme city own the event

    severity = Severity.SEVERE if spread >= config.cross_city_severe_c else Severity.NOTABLE
    role = "warmest" if reading.city == hi_city else "coldest"
    other = lo_city if reading.city == hi_city else hi_city
    reason = (
        f"{reading.city} is the {role} monitored city at {temps[reading.city]:.1f}°C "
        f"vs {other} at {temps[other]:.1f}°C — a {spread:.1f}°C spread."
    )
    return [
        EventData(
            city=reading.city,
            event_type=EventType.CROSS_CITY_DIVERGENCE,
            field="temperature_2m",
            severity=severity,
            reason=reason,
            reading_timestamp=reading.timestamp,
            observed_value=round(temps[reading.city], 2),
            baseline_value=round(temps[other], 2),
            deviation=round(spread, 2),
            context={
                "temps": {k: round(v, 1) for k, v in temps.items()},
                "warmest": hi_city,
                "coldest": lo_city,
            },
        )
    ]
