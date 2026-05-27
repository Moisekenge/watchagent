"""HTTP API (``uvicorn app.main:app``).

Three read-only endpoints over the data the poller collects. FastAPI is the
chosen framework — see the README for the justification (async server, built-in
Pydantic validation of query params and responses, and auto-generated OpenAPI
docs at ``/docs``).

The API is intentionally read-only: collection is the poller's job. This clean
split is why the two run as separate services.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Query
from sqlalchemy.orm import Session

from app import __version__
from app import repository as repo
from app.config import settings
from app.db import get_session, init_db
from app.logging_config import configure_logging
from app.schemas import (
    EventOut,
    EventsResponse,
    HealthOut,
    ReadingOut,
    ReadingsResponse,
)

# Reusable dependency / query annotations (avoids function calls in defaults).
SessionDep = Annotated[Session, Depends(get_session)]
CityQuery = Annotated[str | None, Query(description="Optional exact city filter.")]
LimitQuery = Annotated[int, Query(ge=1, le=1000, description="Max rows, most recent first.")]

configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Idempotent — create tables if the poller hasn't already.
    init_db()
    yield


app = FastAPI(
    title="WatchAgent API",
    version=__version__,
    summary="Weather monitor and notable-event stream for three Canadian cities.",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "service": "watchagent",
        "version": __version__,
        "endpoints": ["/health", "/readings", "/events", "/docs"],
    }


@app.get("/health", response_model=HealthOut)
def health(session: SessionDep) -> HealthOut:
    return HealthOut(
        status="ok",
        readings_stored=repo.count_readings(session),
        events_stored=repo.count_events(session),
    )


@app.get("/readings", response_model=ReadingsResponse)
def readings(
    session: SessionDep,
    city: CityQuery = None,
    limit: LimitQuery = 50,
) -> ReadingsResponse:
    rows = repo.get_recent_readings(session, city=city, limit=limit)
    return ReadingsResponse(readings=[ReadingOut.model_validate(r) for r in rows])


@app.get("/events", response_model=EventsResponse)
def events(
    session: SessionDep,
    city: CityQuery = None,
    limit: LimitQuery = 50,
) -> EventsResponse:
    rows = repo.get_recent_events(session, city=city, limit=limit)
    return EventsResponse(events=[EventOut.model_validate(r) for r in rows])
