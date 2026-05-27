"""Pydantic response models — the wire contract for the HTTP API.

``from_attributes=True`` lets us hand a SQLAlchemy row straight to the schema.
Datetimes serialize to ISO-8601 automatically.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    city: str
    timestamp: datetime
    temperature_2m: float
    apparent_temperature: float
    precipitation: float
    wind_speed_10m: float
    weather_code: int
    created_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    city: str
    event_type: str
    field: str
    severity: str
    observed_value: float | None
    baseline_value: float | None
    deviation: float | None
    reason: str
    context: dict
    reading_timestamp: datetime
    detected_at: datetime


class HealthOut(BaseModel):
    status: str
    readings_stored: int
    events_stored: int


class ReadingsResponse(BaseModel):
    readings: list[ReadingOut]


class EventsResponse(BaseModel):
    events: list[EventOut]
