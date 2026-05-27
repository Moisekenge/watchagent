"""WMO weather-code interpretation.

Open-Meteo reports conditions as a WMO integer. For event detection we care
about two derived things:

* a human description (for the ``reason`` string), and
* a *severity tier* (an ordinal) so we can detect categorical transitions —
  "clear → thunderstorm" is notable regardless of any numeric magnitude.

Tier scale:
    0  benign      clear / cloudy
    1  marginal    fog, light drizzle
    2  significant rain, snow, dense drizzle
    3  heavy       heavy rain/snow, violent showers, freezing
    4  severe      thunderstorm, heavy freezing rain
"""

from __future__ import annotations

# code: (description, category, severity_tier)
_WMO: dict[int, tuple[str, str, int]] = {
    0: ("Clear sky", "clear", 0),
    1: ("Mainly clear", "clear", 0),
    2: ("Partly cloudy", "clouds", 0),
    3: ("Overcast", "clouds", 0),
    45: ("Fog", "fog", 1),
    48: ("Depositing rime fog", "fog", 1),
    51: ("Light drizzle", "drizzle", 1),
    53: ("Moderate drizzle", "drizzle", 1),
    55: ("Dense drizzle", "drizzle", 2),
    56: ("Light freezing drizzle", "freezing", 3),
    57: ("Dense freezing drizzle", "freezing", 3),
    61: ("Slight rain", "rain", 2),
    63: ("Moderate rain", "rain", 2),
    65: ("Heavy rain", "rain", 3),
    66: ("Light freezing rain", "freezing", 3),
    67: ("Heavy freezing rain", "freezing", 4),
    71: ("Slight snowfall", "snow", 2),
    73: ("Moderate snowfall", "snow", 2),
    75: ("Heavy snowfall", "snow", 3),
    77: ("Snow grains", "snow", 2),
    80: ("Slight rain showers", "rain", 2),
    81: ("Moderate rain showers", "rain", 2),
    82: ("Violent rain showers", "rain", 3),
    85: ("Slight snow showers", "snow", 2),
    86: ("Heavy snow showers", "snow", 3),
    95: ("Thunderstorm", "thunderstorm", 4),
    96: ("Thunderstorm with slight hail", "thunderstorm", 4),
    99: ("Thunderstorm with heavy hail", "thunderstorm", 4),
}


def describe(code: int) -> str:
    return _WMO.get(int(code), (f"Unknown (code {code})", "unknown", 0))[0]


def category(code: int) -> str:
    return _WMO.get(int(code), (f"Unknown (code {code})", "unknown", 0))[1]


def severity_tier(code: int) -> int:
    return _WMO.get(int(code), (f"Unknown (code {code})", "unknown", 0))[2]
