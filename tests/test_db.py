"""The connection to Postgres: configuration, the pool and the migrations."""

import psycopg
import pytest

from emu_the_menu import db
from emu_the_menu.repository import StockRepository
from emu_the_menu.stock import Location


def test_without_a_database_url_the_app_says_how_to_set_one(monkeypatch):
    monkeypatch.delenv(db.ENV_VAR, raising=False)
    with pytest.raises(db.DatabaseNotConfiguredError, match="Set EMU_DATABASE_URL"):
        db.database_url()


def test_the_environment_names_the_database(monkeypatch):
    monkeypatch.setenv(db.ENV_VAR, "postgresql://u:p@db.example/emu")
    assert db.database_url() == "postgresql://u:p@db.example/emu"
    assert db.database_url("postgresql://other/emu") == "postgresql://other/emu"


@pytest.mark.parametrize("given", ["emu.db", "/mnt/emu/db/emu.db", "sqlite:///emu.db"])
def test_a_sqlite_file_is_not_mistaken_for_a_database(given):
    with pytest.raises(db.DatabaseNotConfiguredError, match="not a Postgres connection URI"):
        db.database_url(given)


def test_every_migration_is_applied_once_and_recorded(db_url, run_sql):
    shipped = [(version, name) for version, name, _ in db.migrations()]
    assert shipped[0] == (1, "001_initial.sql")
    assert run_sql("SELECT version, name FROM schema_migrations ORDER BY version") == shipped
    with psycopg.connect(db_url, autocommit=True) as conn:
        assert db.migrate(conn) == []  # nothing left to apply


def test_migrations_build_every_data_table(run_sql):
    tables = {
        name
        for (name,) in run_sql(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        )
    }
    assert tables == {*db.DATA_TABLES, "schema_migrations"}


def test_the_app_carries_on_after_postgres_drops_its_connections(db_url):
    with StockRepository(db_url) as stock:
        stock.add("flour", 100, "g", Location.PANTRY)
    with psycopg.connect(db_url, autocommit=True) as admin:  # as a restart or failover would
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )
    with StockRepository(db_url) as stock:
        assert [i.ingredient for i in stock.list_items()] == ["flour"]


def test_a_repository_inside_another_shares_its_connection_and_transaction(db_url):
    from emu_the_menu.recipe_repository import RecipeRepository

    with RecipeRepository(db_url) as recipes:
        database = recipes.database
        with StockRepository(database) as stock:
            assert stock.database is database
            with pytest.raises(RuntimeError):
                with database.transaction():
                    stock.add("rice", 100, "g", Location.PANTRY)
                    raise RuntimeError("something went wrong after the add")
            assert stock.list_items() == []  # rolled back with the outer transaction
        database.execute("SELECT 1")  # still usable: the inner repository did not give it back


def test_a_request_borrows_one_connection_however_many_repositories_it_opens(db_url, monkeypatch):
    from emu_the_menu.eaters import EaterRepository
    from emu_the_menu.menu_repository import MenuRepository
    from emu_the_menu.recipe_repository import RecipeRepository

    borrowed = []
    real_open = db.open_database
    monkeypatch.setattr(
        db, "open_database", lambda url=None: borrowed.append(url) or real_open(url)
    )
    with db.request_scope():
        with RecipeRepository(db_url), MenuRepository(db_url), EaterRepository(db_url):
            with StockRepository(db_url) as stock:
                stock.add("rice", 100, "g", Location.PANTRY)
        with StockRepository(db_url) as stock:  # later in the same request: still the same one
            assert [i.ingredient for i in stock.list_items()] == ["rice"]
    assert borrowed == [db_url]


def test_many_menu_pages_at_once_never_wait_on_each_other(db_url, monkeypatch):
    """Ten people generating a week together, on a pool of two: each request holds at most one
    connection, so they queue for the pool instead of each holding some and waiting for more."""
    import concurrent.futures as cf
    from datetime import date

    from fastapi.testclient import TestClient

    from emu_the_menu.import_sqlite import import_sqlite
    from emu_the_menu.web import create_app

    import_sqlite("tests/data/emu-sqlite.db", db_url)
    small = db_url + "?application_name=emu-small-pool"  # the same database, a pool of its own
    monkeypatch.setattr(db, "POOL_MAX", 2)
    monkeypatch.setattr(db, "POOL_TIMEOUT", 10.0)
    app = create_app(small, today=lambda: date(2026, 11, 10), get_json=lambda url: {})
    query = "/menu?days=7&meal_types=breakfast&meal_types=lunch&meal_types=dinner&seed={}"

    def generate(seed):
        with TestClient(app) as client:
            return client.get(query.format(seed)).status_code

    with cf.ThreadPoolExecutor(10) as pool:
        assert list(pool.map(generate, range(10))) == [200] * 10


def test_write_transactions_take_turns_across_connections(db_url):
    """As in SQLite, one write at a time: a second transaction waits for the first to end."""
    import threading
    import time

    order = []
    first_in = threading.Event()

    def first():
        with db.borrowed(db_url) as conn, conn.transaction():
            first_in.set()
            time.sleep(0.5)
            order.append("first done")

    def second():
        first_in.wait()
        with db.borrowed(db_url) as conn, conn.transaction():
            order.append("second in")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert order == ["first done", "second in"]


def test_pages_refreshing_the_forecast_together_do_not_collide(db_url):
    """Several pages at once each finding the forecast old used to insert the same days twice."""
    import concurrent.futures as cf
    from datetime import UTC, datetime

    from emu_the_menu.weather import Place
    from emu_the_menu.weather_store import WeatherRepository

    with WeatherRepository(db_url) as weather:
        weather.set_place(Place("Wellington", "", "NZ", -41.29, 174.78))
    days = ["2026-11-10", "2026-11-11", "2026-11-12"]

    def forecast(url):
        return {
            "daily": {
                "time": days,
                "temperature_2m_max": [18.0, 20.0, 15.0],
                "temperature_2m_min": [9.0, 11.0, 7.0],
            }
        }

    def refresh(_):
        with WeatherRepository(db_url) as weather:
            return weather.refresh(forecast, now=datetime(2026, 11, 10, tzinfo=UTC), force=True)

    with cf.ThreadPoolExecutor(8) as pool:
        assert list(pool.map(refresh, range(16))) == [True] * 16
