"""The connection to emu's Postgres database, and the schema migrations run on first use.

Emu reads one connection URI from `EMU_DATABASE_URL`, such as
`postgresql://emu:emu@localhost:5432/emu`. Each URI gets a small pool of connections for the life
of the process. A repository borrows a connection when it opens and gives it back when it closes,
and repositories opened inside another one share their parent's connection.

Connections run in autocommit mode, as the standard sqlite3 module did for reads: a statement on
its own commits at once, and `Database.transaction()` groups statements into one atomic change.

The schema lives in numbered files in `migrations/` (001_initial.sql, 002_...). The first
connection to a database applies the ones it has not had yet, in order, each in its own
transaction, and records them in `schema_migrations`. An advisory lock keeps two processes
starting at once from both applying them.
"""

import atexit
import os
import re
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from importlib import resources
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

ENV_VAR = "EMU_DATABASE_URL"
POOL_MAX = 10  # connections per process; a household is a handful of people
POOL_TIMEOUT = 30.0  # seconds a request waits for a free connection before it fails

_MIGRATION_LOCK = 0x656D755F6D6967  # "emu_mig", an arbitrary key for pg_advisory_lock
_WRITE_LOCK = 0x656D755F777269  # "emu_wri": held by every write transaction (see transaction())
_MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

_pools: dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()


class DatabaseNotConfiguredError(RuntimeError):
    """No database connection URI was given or set in the environment."""


def database_url(url: str | None = None) -> str:
    """The connection URI to use: `url` if given, else `EMU_DATABASE_URL`."""
    found = url or os.environ.get(ENV_VAR)
    if not found:
        raise DatabaseNotConfiguredError(
            f"Set {ENV_VAR} to a Postgres connection URI, such as"
            " postgresql://emu:emu@localhost:5432/emu (see the README)"
        )
    if not isinstance(found, str) or not found.startswith(("postgresql://", "postgres://")):
        raise DatabaseNotConfiguredError(
            f"{found!r} is not a Postgres connection URI (postgresql://user:password@host/db)"
        )
    return found


class Database:
    """One borrowed connection, with the few calls the repositories need."""

    def __init__(self, connection: psycopg.Connection, release=None):
        self._conn = connection
        self._release = release

    def execute(self, query: str, params: Sequence[Any] | None = None) -> psycopg.Cursor:
        return self._conn.execute(query, params)

    def executemany(self, query: str, rows: Iterable[Sequence[Any]]) -> None:
        rows = list(rows)
        if rows:
            with self._conn.cursor() as cursor:
                cursor.executemany(query, rows)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run the statements inside as one change: all of them happen, or none on an error.

        Write transactions run one at a time, across every process, as they did in SQLite: the
        outermost block takes one advisory lock until it ends. Without it, two requests that read
        and then write (refreshing the forecast, adding a new ingredient, taking stock) could
        interleave under Postgres's READ COMMITTED and collide or lose an update. Reads outside a
        transaction are not held up. Nested blocks become savepoints of the outer one.
        """
        outermost = self._conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        with self._conn.transaction():
            if outermost:
                self._conn.execute("SELECT pg_advisory_xact_lock(%s)", (_WRITE_LOCK,))
            yield

    def close(self) -> None:
        if self._release is not None:
            self._release(self._conn)
            self._release = None


def open_database(url: str | None = None) -> Database:
    """Borrow a connection to the database, creating or upgrading its schema on first use."""
    pool = _pool(database_url(url))
    return Database(pool.getconn(), pool.putconn)


# The connections borrowed by the request being handled, by URI (see `request_scope`).
_request_connections: ContextVar[dict[str, Database] | None] = ContextVar(
    "emu_request_connections", default=None
)


@contextmanager
def request_scope() -> Iterator[None]:
    """Make every repository opened inside share one connection per URI, given back at the end.

    The web app wraps each request in this. A page opens several repositories, some inside
    others; sharing one connection means a request never holds more than one, so requests waiting
    for the pool can never all be waiting on each other. Nothing is borrowed until a repository
    opens, so a request that does not touch the database (such as /healthz) borrows nothing.
    """
    connections: dict[str, Database] = {}
    token = _request_connections.set(connections)
    try:
        yield
    finally:
        _request_connections.reset(token)
        for db in connections.values():
            db.close()


def attach(source: str | Database | None) -> tuple[Database, bool]:
    """The Database a repository works on, and whether it is the repository's to give back.

    A repository opened with another one's Database shares that connection, so a Recipe
    repository and the Stock repository inside it see each other's writes in one transaction.
    Inside `request_scope` every repository on the same URI shares the request's connection.
    """
    if isinstance(source, Database):
        return source, False
    shared = _request_connections.get()
    if shared is not None:
        url = database_url(source)
        if url not in shared:
            shared[url] = open_database(url)
        return shared[url], False
    return open_database(source), True


@contextmanager
def borrowed(source: str | Database | None) -> Iterator[Database]:
    """A Database for a one-off job: `source` itself if it is one, else a borrowed connection."""
    db, owned = attach(source)
    try:
        yield db
    finally:
        if owned:
            db.close()


def _pool(url: str) -> ConnectionPool:
    with _pools_lock:
        pool = _pools.get(url)
        if pool is None:
            # A plain connection first, so a wrong address or password fails at once with the
            # server's own message, instead of the pool retrying in the background.
            with psycopg.connect(url, autocommit=True) as conn:
                migrate(conn)
            pool = ConnectionPool(
                url,
                min_size=1,
                max_size=POOL_MAX,
                kwargs={"autocommit": True},
                # Check each connection before lending it, so one Postgres restart (a failover,
                # an upgrade) costs a reconnect rather than an error page per dead connection.
                check=ConnectionPool.check_connection,
                timeout=POOL_TIMEOUT,
                open=True,
                name="emu",
            )
            if not _pools:
                atexit.register(close_pools)  # stop the pool threads before the process ends
            _pools[url] = pool
        return pool


def close_pools() -> None:
    """Close every pool, such as when the process stops or between test runs."""
    with _pools_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


def migrations() -> list[tuple[int, str, str]]:
    """Every migration shipped with emu as (version, file name, SQL), in order."""
    found = []
    for entry in resources.files("emu_the_menu.migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [version for version, _, _ in found]
    if len(set(versions)) != len(versions):
        raise RuntimeError("Two migrations share a number")
    return found


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply the migrations this database has not had yet. Returns the names applied."""
    applied: list[str] = []
    conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version integer PRIMARY KEY,"
            " name text NOT NULL,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {v for (v,) in conn.execute("SELECT version FROM schema_migrations")}
        for version, name, sql in migrations():
            if version in done:
                continue
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                    (version, name),
                )
            applied.append(name)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
    return applied


# Tables holding a household's data, parents before children (the order the importer fills
# them in). `schema_migrations` is not data and is not listed.
DATA_TABLES = (
    "settings",
    "ingredients",
    "ingredient_food_groups",
    "ingredient_allergens",
    "stock_items",
    "product_cache",
    "recipes",
    "recipe_meal_types",
    "recipe_tags",
    "recipe_lines",
    "eaters",
    "eater_terms",
    "eater_allergens",
    "category_goals",
    "occasions",
    "occasion_tags",
    "weather_days",
    "menus",
    "menu_meals",
    "menu_meal_taken",
    "menu_portions",
)
