"""A Postgres database for the tests.

Tests run against the database named by `EMU_TEST_DATABASE_URL` (by default
postgresql://emu:emu@localhost:5432/emu_test). It is dropped and created afresh once per run, so
point it at a database you do not mind losing; the role needs CREATEDB. The README says how to get
a Postgres to run against.

Each test that asks for `db_url` gets that database with every table emptied and ids starting
from 1 again, as if it were new.
"""

import os
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql as pgsql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from emu_the_menu import db

DEFAULT_TEST_URL = "postgresql://emu:emu@localhost:5432/emu_test"


def _test_url() -> str:
    return os.environ.get("EMU_TEST_DATABASE_URL", DEFAULT_TEST_URL)


def _recreate(url: str) -> None:
    """Drop and create the test database, connected to the server's `postgres` database."""
    name = conninfo_to_dict(url)["dbname"]
    if not name or name in ("postgres", "template0", "template1"):
        raise RuntimeError(f"Refusing to use {name!r} as the test database")
    admin = make_conninfo(url, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(name))
        )
        conn.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(name)))


@pytest.fixture(scope="session")
def _test_database() -> Iterator[str]:
    url = _test_url()
    try:
        _recreate(url)
    except psycopg.OperationalError as exc:
        pytest.exit(
            f"Cannot reach the test Postgres at {url}: {exc}\n"
            "Start one (see the README) or set EMU_TEST_DATABASE_URL.",
            returncode=2,
        )
    db.open_database(url).close()  # creates the schema
    yield url
    db.close_pools()


_TRUNCATE = "TRUNCATE {} RESTART IDENTITY CASCADE".format(", ".join(db.DATA_TABLES))


@pytest.fixture
def db_url(_test_database: str) -> str:
    """The connection URI of an empty test database."""
    with db.borrowed(_test_database) as conn:
        conn.execute(_TRUNCATE)
    return _test_database


@pytest.fixture
def run_sql(db_url: str):
    """Run one statement on the test database, for tests that set up or inspect raw rows.

    Returns the rows, or an empty list for a statement that returns none.
    """

    def run(query: str, params=None) -> list[tuple]:
        with db.borrowed(db_url) as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchall() if cursor.description else []

    return run
