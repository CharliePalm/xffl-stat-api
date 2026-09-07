"""
db.py — a thin wrapper around a single sqlite3 connection that
transparently re-establishes itself if the connection has been closed
or otherwise becomes unusable.

NOT designed for concurrent use of the SAME instance across multiple
threads — sqlite3 connections aren't safe to share across threads by
default. If you're calling this from asyncio coroutines on a single
event loop thread, that's fine. If you spin up real OS threads, give
each thread its own Database() instance instead.
"""

import sqlite3
import threading
from typing import Any, Iterable, Optional, Type, TypeVar

from pydantic import BaseModel
import re
import json
import atexit
import signal
import weakref
from shared.utils import to_snake

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

T = TypeVar("T", bound=BaseModel)
IS_TEST = True

DB_PATH = "/db/xffl.db" if not IS_TEST else "../../srv/infra/docker/db/xffl.db"

# Track live Database instances so we can close connections at process
# shutdown (atexit and on termination signals).
_DB_INSTANCES: "weakref.WeakSet[Database]" = weakref.WeakSet()
_EXIT_HANDLERS_REGISTERED = False


def _close_all_databases(signum=None, frame=None):
    for db in list(_DB_INSTANCES):
        try:
            db.close()
        except Exception:
            pass


def _register_exit_handlers_once():
    global _EXIT_HANDLERS_REGISTERED
    if _EXIT_HANDLERS_REGISTERED:
        return
    _EXIT_HANDLERS_REGISTERED = True
    atexit.register(_close_all_databases)

    try:
        # Try to register for common termination signals as well.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                orig = signal.getsignal(sig)

                def _handler(signum, frame, orig=orig):
                    _close_all_databases(signum, frame)
                    # If there was an original handler, call it (unless it
                    # was the default or ignored marker).
                    if callable(orig) and orig not in (signal.SIG_DFL, signal.SIG_IGN):
                        try:
                            orig(signum, frame)
                        except Exception:
                            pass

                signal.signal(sig, _handler)
            except Exception:
                # Best-effort; ignore if signals aren't available.
                pass
    except Exception:
        pass


class Database:
    def __init__(self, timeout: float = 5.0):
        self.db_path = str(DB_PATH)
        self.timeout = timeout
        self._connection: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()  # protects (re)connect, not query execution
        # Register instance for process-exit cleanup.
        _DB_INSTANCES.add(self)
        _register_exit_handlers_once()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)};")
        return conn

    @property
    def connection(self) -> sqlite3.Connection:
        """
        Returns a live connection, transparently reconnecting if the
        current one is missing or has been closed underneath us.
        """
        with self._lock:
            if self._connection is None:
                self._connection = self._connect()
                return self._connection

            # sqlite3 has no real "ping" — running a trivial statement
            # and catching the specific error it raises once a
            # connection is closed is the standard way to check.
            try:
                self._connection.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                self._connection = self._connect()

            return self._connection

    def _execute_with_retry(
        self,
        query: str,
        params: Iterable[Any],
        many: bool = False,
        _retried: bool = False,
    ) -> sqlite3.Cursor:
        conn = self.connection
        try:
            if many:
                print("executing many")
                return conn.executemany(query, params)
            return conn.execute(query, params)  # type: ignore
        except sqlite3.ProgrammingError:
            # Rare race: connection died between our liveness check
            # above and this call. Reconnect once and retry.
            if _retried:
                raise
            with self._lock:
                self._connection = self._connect()
            return self._execute_with_retry(query, params, many=many, _retried=True)

    def executemany(
        self, query: str, seq_of_params: Iterable[Iterable[Any]]
    ) -> sqlite3.Cursor:
        return self._execute_with_retry(query, seq_of_params, many=True)

    def fetchall(self, query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self._execute_with_retry(query, params).fetchall()

    def fetchone(self, query: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        return self._execute_with_retry(query, params).fetchone()

    def fetch_models(
        self, query: str, model: Type[T], params: Iterable[Any] = ()
    ) -> list[T]:
        """
        Runs a query and validates each resulting row into an instance
        of the given Pydantic model.

        The SELECT's column names (or aliases) must match the model's
        field names exactly — e.g.:

            db.fetch_models(
                "SELECT gameWeek AS game_week, cbsLink AS cbs_link, ... FROM game",
                model=Game,
            )

        This keeps the mapping explicit in the query itself rather than
        guessing at camelCase-to-snake_case conversions, which could
        silently map the wrong column to the wrong field.
        """
        rows = self.fetchall(query, params)
        return [model.model_validate(dict(row)) for row in rows]

    def fetch_model(
        self, query: str, model: Type[T], params: Iterable[Any] = ()
    ) -> Optional[T]:
        """Same as fetch_models, but returns a single instance (or None)."""
        row = self.fetchone(query, params)
        return model.model_validate(dict(row)) if row is not None else None

    def execute_and_commit(
        self, sql: str, params: Iterable[Any] = ()
    ) -> sqlite3.Cursor:
        """Convenience for a single write statement that should commit immediately."""
        cursor = self._execute_with_retry(sql, params)
        self.connection.commit()
        return cursor

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _quote_identifier(self, name: str) -> str:
        """Validate and quote a table/column name for safe SQL interpolation."""
        if not _IDENTIFIER_RE.match(name):
            raise ValueError(f"Invalid SQL identifier: {name!r}")
        return f'"{name}"'

    def _sqlite_type_for_value(self, value: Any) -> str:
        """Return a reasonable sqlite type for a Python value."""
        if isinstance(value, bool):
            return "INTEGER"
        if isinstance(value, int):
            return "INTEGER"
        if isinstance(value, float):
            return "REAL"
        if isinstance(value, (dict, list)):
            return "TEXT"
        if value is None:
            return "TEXT"  # unknown; None carries no type info
        return "TEXT"

    def write_model(
        self, model: BaseModel, table_name: Optional[str] = None, commit=True
    ) -> sqlite3.Cursor:
        """Writes a Pydantic `model` instance to the database.

        The table name is derived from the model class name converted to
        snake_case unless `table_name` is provided. If the table does not
        exist it will be created with columns inferred from the model's
        current fields. Complex values (lists/dicts) are serialized as
        JSON into TEXT columns.

        Note: if fields are added to the model after the table was first
        created, this will NOT migrate the existing table's schema — the
        insert will fail with "no column named X". Call a migration step
        explicitly if you need to add columns to an existing table.
        """
        if not isinstance(model, BaseModel):
            raise TypeError("model must be a Pydantic BaseModel instance")

        raw_table = table_name or to_snake(model.__class__.__name__)
        table = self._quote_identifier(raw_table)
        data = model.model_dump()  # pydantic v2 style

        columns = []  # (quoted_name, sql_type, raw_value)
        for key, val in data.items():
            columns.append(
                (self._quote_identifier(key), self._sqlite_type_for_value(val), val)
            )

        col_defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"] + [
            f"{name} {sql_type}" for name, sql_type, _ in columns
        ]
        create_sql = f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(col_defs)});"
        self.execute_and_commit(create_sql)

        quoted_keys = [name for name, _, _ in columns]
        placeholders = ", ".join(["?" for _ in columns])
        insert_sql = (
            f"INSERT INTO {table} ({', '.join(quoted_keys)}) VALUES ({placeholders})"
        )

        params = []
        for _, _, v in columns:
            if isinstance(v, (dict, list)):
                params.append(json.dumps(v))
            elif isinstance(v, bool):
                params.append(1 if v else 0)
            else:
                params.append(v)

        return (
            self.execute_and_commit(insert_sql, params)
            if commit
            else self._execute_with_retry(insert_sql, params)
        )

    def commit(self):
        self._connection.commit()
