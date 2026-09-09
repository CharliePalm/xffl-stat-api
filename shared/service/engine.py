"""
db/engine.py — engine and session setup. The only file that knows the path.

Everything else (models, services, routes) imports `get_session` from
here and stays ignorant of which database is behind it, so switching to
Postgres later touches this file alone.

    # main.py
    from db.engine import build_engine, get_session

Requires: sqlalchemy>=2.0.49
"""

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.config import settings
from .service import configure_sqlite

# ---------------------------------------------------------------------------
# Where the database lives
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = "../../srv/infra/docker/db/xffl.db"

#: Override without code changes: DB_PATH=/srv/data/app.db
DB_PATH = Path(settings.DB_PATH or DEFAULT_DB_PATH)


def sqlite_url(path: Path) -> str:
    """Build a SQLite URL from a filesystem path.

    The slash count is load-bearing and easy to get wrong by hand:
    `sqlite:///` plus the path yields three slashes for a relative path
    and four for an absolute one, which is exactly what SQLAlchemy wants.
    Interpolating a `Path` gets this right in both cases.

        Path("app.db")          -> sqlite:///app.db
        Path("/srv/data/app.db") -> sqlite:////srv/data/app.db

    On Windows a drive letter (`C:\\...`) does not follow this rule; use
    `sqlalchemy.engine.URL.create("sqlite", database=str(path))` there.
    """
    return f"sqlite:///{path}"


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def build_engine(path: Path | None = None, *, echo: bool = False) -> Engine:
    """Create the engine for a local SQLite file.

    Creates the parent directory first. SQLite will not do this, and the
    error when it is missing is unhelpfully vague — "unable to open
    database file", which reads like a permissions problem.
    """
    path = (path or DB_PATH).resolve()
    # path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        sqlite_url(path),
        echo=echo,
        # A connection can be handed to a worker thread: FastAPI runs
        # `def` endpoints in a threadpool. SQLite's driver objects to that
        # by default.
        connect_args={"check_same_thread": False},
    )
    configure_sqlite(engine)  # real transactions, savepoints, FK pragma
    return engine


def build_test_engine() -> Engine:
    """In-memory engine for tests.

    `StaticPool` is required, not a tuning choice. Each connection to
    ":memory:" gets its own private database, so without a pool that
    hands out one shared connection, a test's setup and its assertions
    look at different empty databases.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    configure_sqlite(engine)
    return engine


engine = build_engine()

SessionLocal = sessionmaker(
    engine,
    # Without this, touching an attribute on a returned object after the
    # request's commit triggers a refresh against a closed session.
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# Session dependency
# ---------------------------------------------------------------------------


def get_session() -> Iterator[Session]:
    """One transaction per request: commits on success, rolls back on error.

    `session.begin()` is what makes `Service.put`'s savepoint recovery
    meaningful — there has to be an outer transaction for a savepoint to
    nest inside.

        @app.get("/users/{id}")
        def read(id: int, session: Session = Depends(get_session)):
            return UserService(session).get(id)
    """
    with SessionLocal() as session, session.begin():
        yield session


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
#
# Your tables already exist and are managed outside SQLAlchemy, so there
# is deliberately no `create_all()` call here. If you want it for a fresh
# local file, make it explicit and opt-in:
#
#     def init_db(engine: Engine) -> None:
#         from .models import Base
#         Base.metadata.create_all(engine)
#
# Run it from a script or a CLI command, never at import time — importing
# this module should not mutate a database.
#
# Note that `create_all` only creates tables that are absent; it will not
# alter one whose columns have drifted from your mapped class. Once the
# schema starts changing, that gap is what Alembic is for.
