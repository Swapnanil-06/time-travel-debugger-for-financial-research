"""Reuse the research store against a Durable Object's synchronous SQLite API.

No per-request database snapshots, local filesystem writes, D1 provisioning, or
second forecasting implementation. The Worker wraps a request in transactionSync.
Only the storage transport is adapted; validation, hashing and replay are shared.
"""
import base64
from contextlib import contextmanager

from .engine import IntegrityError, ResearchError
from .store import SCHEMA, Store

MAX_CLOUD_RUNS = 40
MAX_CLOUD_OBSERVATIONS = 1000


class Row(dict):
    """Support the named and positional access used by sqlite3.Row callers."""

    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class Connection:
    def __init__(self, sql):
        self.sql = sql

    def execute(self, statement, parameters=()):
        # Store byte artifacts losslessly as base64 text. This avoids an FFI
        # dependency in the shared store and makes the transport unit-testable.
        bindings = ["b64:" + base64.b64encode(v).decode("ascii") if isinstance(v, bytes) else v for v in parameters]
        result = self.sql.exec(statement, *bindings).toArray()
        rows = result.to_py() if hasattr(result, "to_py") else result
        converted = []
        for row in rows:
            values = {}
            for key, value in row.items():
                if key in {"content", "manifest"} and isinstance(value, str) and value.startswith("b64:"):
                    try:
                        value = base64.b64decode(value[4:], validate=True)
                    except ValueError as error:
                        raise IntegrityError("Corrupted byte artifact encoding in cloud SQLite.") from error
                values[key] = value
            converted.append(Row(values))
        return Cursor(converted)


class CloudStore(Store):
    def __init__(self, sql):
        self.sql = sql
        self.sql.exec(SCHEMA)

    @contextmanager
    def connect(self):
        # The request's outer transactionSync owns commit/rollback. Opening this
        # context never executes transaction statements forbidden by workerd.
        yield Connection(self.sql)

    def create(self, **arguments):
        rows = arguments.get("rows")
        if isinstance(rows, list) and len(rows) > MAX_CLOUD_OBSERVATIONS:
            raise ResearchError(f"The public demo supports at most {MAX_CLOUD_OBSERVATIONS} observations per run. Use local mode for larger datasets.")
        with self.connect() as db:
            count = db.execute("SELECT COUNT(*) AS count FROM runs").fetchone()[0]
        if count >= MAX_CLOUD_RUNS:
            raise ResearchError(f"This browser workspace has reached its {MAX_CLOUD_RUNS}-run demo limit. Existing runs remain available for comparison and replay.")
        return super().create(**arguments)
