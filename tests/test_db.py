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
