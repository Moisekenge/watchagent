"""Event-detection tests — the direct expression of whether the logic does what
the README claims. Each detector is tested for what it *fires* on and, just as
importantly, what it stays *silent* on (the over-firing guard).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import DetectionConfig
from app.detection import detect_events
from app.detection.rules import (
    detect_anomaly,
    detect_condition_change,
    detect_cross_city_divergence,
    detect_high_wind,
    detect_precip_transition,
    detect_rapid_change,
    detect_trend,
)
from app.domain import EventType, Severity

UTC = timezone.utc
CFG = DetectionConfig()


def _series(make_reading, temps, *, apparent=15.0, wind=10.0):
    start = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
    return [
        make_reading(
            timestamp=start + timedelta(hours=i),
            temperature_2m=t,
            apparent_temperature=apparent,
            wind_speed_10m=wind,
        )
        for i, t in enumerate(temps)
    ]


# --- 1. anomaly -------------------------------------------------------------
def test_anomaly_fires_on_outlier(stable_history, make_reading):
    history = stable_history(n=10, base=20.0)
    outlier = make_reading(temperature_2m=32.0, apparent_temperature=20.0, wind_speed_10m=10.0)
    events = detect_anomaly(outlier, history, CFG)
    temp = [e for e in events if e.field == "temperature_2m"]
    assert len(temp) == 1
    assert temp[0].event_type == EventType.ANOMALY
    assert temp[0].severity == Severity.SEVERE
    assert abs(temp[0].deviation) > CFG.anomaly_z


def test_anomaly_silent_on_normal_variation(stable_history, make_reading):
    history = stable_history(n=10, base=20.0)
    normal = make_reading(temperature_2m=20.6, apparent_temperature=20.0, wind_speed_10m=10.0)
    assert detect_anomaly(normal, history, CFG) == []


def test_anomaly_disarmed_during_cold_start(stable_history, make_reading):
    history = stable_history(n=5)  # below min_history
    outlier = make_reading(temperature_2m=45.0)
    assert detect_anomaly(outlier, history, CFG) == []


# --- 2. rapid change --------------------------------------------------------
def test_rapid_change_fires_on_large_step(make_reading):
    history = _series(make_reading, [20, 21, 20, 21, 20, 21])
    reading = make_reading(
        timestamp=datetime(2026, 5, 25, 6, tzinfo=UTC),
        temperature_2m=33.0,
        apparent_temperature=15.0,
        wind_speed_10m=10.0,
    )
    events = detect_rapid_change(reading, history, CFG)
    temp = [e for e in events if e.field == "temperature_2m"]
    assert len(temp) == 1
    assert temp[0].event_type == EventType.RAPID_CHANGE
    assert temp[0].severity == Severity.SEVERE  # 12° step exceeds the severe floor


def test_rapid_change_silent_below_absolute_floor(make_reading):
    history = _series(make_reading, [20.0, 20.1, 20.2, 20.3, 20.4, 20.5])
    reading = make_reading(
        timestamp=datetime(2026, 5, 25, 6, tzinfo=UTC),
        temperature_2m=22.0,  # +1.5 step, under the 3° floor
        apparent_temperature=15.0,
        wind_speed_10m=10.0,
    )
    assert detect_rapid_change(reading, history, CFG) == []


def test_rapid_change_silent_when_normal_for_a_volatile_city(make_reading):
    # A city whose hourly swings are routinely ±10°. A 4° step is unremarkable.
    history = _series(make_reading, [10, 20, 10, 20, 10, 20])
    reading = make_reading(
        timestamp=datetime(2026, 5, 25, 6, tzinfo=UTC),
        temperature_2m=24.0,  # prev=20 → +4, clears floor but is statistically normal
        apparent_temperature=15.0,
        wind_speed_10m=10.0,
    )
    assert detect_rapid_change(reading, history, CFG) == []


# --- 3. trend ---------------------------------------------------------------
def test_trend_fires_on_monotonic_climb(make_reading):
    history = _series(make_reading, [10, 13, 16])
    reading = make_reading(
        timestamp=datetime(2026, 5, 25, 3, tzinfo=UTC),
        temperature_2m=19.0,
        apparent_temperature=15.0,
        wind_speed_10m=10.0,
    )
    events = detect_trend(reading, history, CFG)
    temp = [e for e in events if e.field == "temperature_2m"]
    assert len(temp) == 1
    assert temp[0].event_type == EventType.TREND
    assert temp[0].deviation == 9.0


def test_trend_silent_on_oscillation(make_reading):
    history = _series(make_reading, [10, 18, 11])
    reading = make_reading(
        timestamp=datetime(2026, 5, 25, 3, tzinfo=UTC),
        temperature_2m=19.0,
        apparent_temperature=15.0,
    )
    assert detect_trend(reading, history, CFG) == []


def test_trend_silent_on_small_cumulative_move(make_reading):
    history = _series(make_reading, [19, 20, 21])
    reading = make_reading(
        timestamp=datetime(2026, 5, 25, 3, tzinfo=UTC),
        temperature_2m=22.0,  # monotonic but only +3 total, under the 6° floor
        apparent_temperature=15.0,
    )
    assert detect_trend(reading, history, CFG) == []


# --- 4. precipitation -------------------------------------------------------
def test_precip_onset_fires(make_reading):
    history = [make_reading(precipitation=0.0)]
    events = detect_precip_transition(make_reading(precipitation=5.0), history, CFG)
    assert len(events) == 1
    assert events[0].event_type == EventType.PRECIP_ONSET
    assert events[0].severity == Severity.NOTABLE  # 5 mm/h = moderate


def test_precip_cessation_fires(make_reading):
    history = [make_reading(precipitation=3.0)]
    events = detect_precip_transition(make_reading(precipitation=0.0), history, CFG)
    assert len(events) == 1
    assert events[0].event_type == EventType.PRECIP_CESSATION


def test_precip_silent_while_continuing(make_reading):
    history = [make_reading(precipitation=2.0)]
    assert detect_precip_transition(make_reading(precipitation=3.0), history, CFG) == []


# --- 5. condition change ----------------------------------------------------
def test_condition_change_fires_on_thunderstorm_onset(make_reading):
    history = [make_reading(weather_code=0)]  # clear
    events = detect_condition_change(make_reading(weather_code=95), history, CFG)
    assert len(events) == 1
    assert events[0].severity == Severity.SEVERE


def test_condition_change_fires_on_clearing(make_reading):
    history = [make_reading(weather_code=61)]  # rain
    events = detect_condition_change(make_reading(weather_code=0), history, CFG)
    assert len(events) == 1
    assert events[0].severity == Severity.INFO


def test_condition_change_silent_within_benign(make_reading):
    history = [make_reading(weather_code=0)]  # clear
    assert detect_condition_change(make_reading(weather_code=3), history, CFG) == []  # overcast


# --- 6. high wind -----------------------------------------------------------
def test_high_wind_fires_crossing_strong(make_reading):
    events = detect_high_wind(make_reading(wind_speed_10m=45.0), [make_reading(wind_speed_10m=30.0)], CFG)
    assert len(events) == 1
    assert events[0].severity == Severity.NOTABLE


def test_high_wind_fires_crossing_gale(make_reading):
    events = detect_high_wind(make_reading(wind_speed_10m=65.0), [make_reading(wind_speed_10m=45.0)], CFG)
    assert len(events) == 1
    assert events[0].severity == Severity.SEVERE


def test_high_wind_silent_when_already_high(make_reading):
    # Stays within the gale tier — no *new* crossing, so no repeat event.
    assert detect_high_wind(make_reading(wind_speed_10m=70.0), [make_reading(wind_speed_10m=65.0)], CFG) == []


# --- 7. cross-city divergence ----------------------------------------------
def test_cross_city_divergence_fires_for_extreme_city(make_reading):
    ottawa = make_reading(city="Ottawa", temperature_2m=-5.0)
    peers = {
        "Vancouver": make_reading(city="Vancouver", temperature_2m=14.0),
        "Toronto": make_reading(city="Toronto", temperature_2m=2.0),
    }
    events = detect_cross_city_divergence(ottawa, peers, CFG)
    assert len(events) == 1
    assert events[0].event_type == EventType.CROSS_CITY_DIVERGENCE
    assert events[0].deviation == 19.0


def test_cross_city_silent_for_middle_city(make_reading):
    toronto = make_reading(city="Toronto", temperature_2m=2.0)
    peers = {
        "Ottawa": make_reading(city="Ottawa", temperature_2m=-5.0),
        "Vancouver": make_reading(city="Vancouver", temperature_2m=14.0),
    }
    assert detect_cross_city_divergence(toronto, peers, CFG) == []


def test_cross_city_silent_on_small_spread(make_reading):
    ottawa = make_reading(city="Ottawa", temperature_2m=10.0)
    peers = {
        "Vancouver": make_reading(city="Vancouver", temperature_2m=14.0),
        "Toronto": make_reading(city="Toronto", temperature_2m=12.0),
    }
    assert detect_cross_city_divergence(ottawa, peers, CFG) == []


# --- cooldown / noise control ----------------------------------------------
def test_cooldown_suppresses_repeat_then_re_arms(stable_history, make_reading):
    history = stable_history(n=10, base=20.0)
    outlier = make_reading(temperature_2m=32.0, apparent_temperature=20.0, wind_speed_10m=10.0)
    t0 = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)

    first = detect_events(outlier, history, now=t0)
    assert [e for e in first if e.event_type == EventType.ANOMALY and e.field == "temperature_2m"]

    cooldown = {("anomaly", "temperature_2m"): t0}
    soon = detect_events(outlier, history, last_event_at=cooldown, now=t0 + timedelta(hours=1))
    assert not [e for e in soon if e.event_type == EventType.ANOMALY and e.field == "temperature_2m"]

    later = detect_events(outlier, history, last_event_at=cooldown, now=t0 + timedelta(hours=4))
    assert [e for e in later if e.event_type == EventType.ANOMALY and e.field == "temperature_2m"]
