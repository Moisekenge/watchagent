"""Database engine, session factory, and schema creation.

Sessions are dependency-injected (passed into repository functions and the API
routes) rather than reached for as a global. That keeps the data layer testable:
the test suite points a fresh engine at SQLite and the exact same repository
code runs unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.models import Base


def make_engine(url: str) -> Engine:
    """Create an engine, applying SQLite-specific options when needed.

    SQLite (used only in tests) needs a shared single connection so an
    in-memory database is visible across threads — e.g. FastAPI's TestClient.
    Postgres uses ``pool_pre_ping`` to survive idle-connection drops.
    """
    if url.startswith("sqlite"):
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
    return create_engine(url, pool_pre_ping=True, future=True)


engine: Engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(target: Engine | None = None) -> None:
    """Create tables if absent. Idempotent — safe for every service to call."""
    Base.metadata.create_all(target or engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commit on success, roll back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: a read-scoped session per request."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
