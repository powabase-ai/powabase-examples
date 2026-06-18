"""Direct Postgres access to the Powabase per-project database.

This is the DEFAULT path for all RankForge `public.*` app data (and read-only
`ai.*` queries). Use the Powabase HTTP client only for platform-managed work
(agents, workflows, knowledge bases) — see `powabase/client.py`.

A single pooled `Database` is created at app startup and closed at shutdown.
Always pass parameters separately — never f-string user input into SQL.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class Database:
    """Thin wrapper over a psycopg3 ConnectionPool with dict rows."""

    def __init__(self, conninfo: str, *, min_size: int = 1, max_size: int = 10):
        self._pool = ConnectionPool(
            conninfo,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            # Validate (and transparently replace) a pooled connection before
            # handing it out. Long-running ops (research/generation runs) can
            # outlast the pooler's idle timeout, leaving stale connections —
            # without this you get "server closed the connection unexpectedly".
            check=ConnectionPool.check_connection,
            open=False,
        )

    def open(self) -> None:
        self._pool.open()

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            yield conn

    def fetch_all(self, query: str, params: Any = None) -> list[dict[str, Any]]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()

    def fetch_one(self, query: str, params: Any = None) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def execute(self, query: str, params: Any = None) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(query, params)
