"""The one-off import of an emu.db from the SQLite days into Postgres.

`data/emu-sqlite.db` was written by the SQLite-era app (see `data/make_emu_sqlite.py`), with a row
in every table. `data/emu-sqlite.expected.json` is what that app read back from it: the JSON
export, the saved Menus, the Stock, the weather and the settings. After the import, this app must
read back the same.
"""

import dataclasses
import json
import shutil
import sqlite3
from datetime import date
from enum import Enum
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from emu_the_menu import import_sqlite as importer
from emu_the_menu.db import DATA_TABLES
from emu_the_menu.eaters import Category, EaterRepository
from emu_the_menu.menu_repository import MenuRepository
from emu_the_menu.occasion_repository import OccasionRepository
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import StockRepository
from emu_the_menu.stock import Location
from emu_the_menu.weather_store import WeatherRepository
from emu_the_menu.web import create_app

DATA = Path(__file__).parent / "data"
SOURCE = DATA / "emu-sqlite.db"
TODAY = date(2026, 11, 10)


def source_counts(path: Path = SOURCE) -> dict[str, int]:
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)  # never create it
    try:
        return {
            name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for (name,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        db.close()


def plain(value):
    if dataclasses.is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    return value


def read_back(db_url: str) -> dict:
    """What the app shows, gathered the same way as `make_emu_sqlite.read_back`."""
    client = TestClient(create_app(db_url, today=lambda: TODAY))
    export = client.get("/export.json").json()
    with MenuRepository(db_url) as menus, StockRepository(db_url) as stock:
        with WeatherRepository(db_url) as weather, OccasionRepository(db_url) as occasions:
            return {
                "export": export,
                "menus": plain(menus.menus()),
                "last_served": plain(menus.last_served()),
                "taken": plain(
                    [menus.taken(m.id, meal.id) for m in menus.menus() for meal in m.meals]
                ),
                "stock": plain(stock.list_items()),
                "ingredients": plain(stock.ingredients()),
                "look": plain(stock.look_choice()),
                "disclaimer": plain(stock.disclaimer_acceptance()),
                "products": plain(
                    [stock.cached_product(b) for b in ("3274080005003", "5000000000001")]
                ),
                "place": plain(weather.place()),
                "unit": weather.unit(),
                "limits": plain(weather.limits()),
                "weather_days": plain(weather.days(date(2026, 11, 1))),
                "occasions": plain(occasions.all()),
            }


def test_the_fixture_has_a_row_in_every_table():
    counts = source_counts()
    assert set(counts) == set(DATA_TABLES)
    assert all(counts.values())


def test_every_table_arrives_with_the_same_number_of_rows(db_url):
    counts = importer.import_sqlite(SOURCE, db_url)
    assert {c.table: (c.source, c.copied) for c in counts} == {
        table: (n, n) for table, n in source_counts().items()
    }


def test_the_app_shows_what_the_sqlite_app_showed(db_url):
    importer.import_sqlite(SOURCE, db_url)
    expected = json.loads((DATA / "emu-sqlite.expected.json").read_text())
    assert read_back(db_url) == expected


def test_the_sqlite_file_is_left_as_it_was(db_url, tmp_path):
    copy = tmp_path / "emu.db"
    shutil.copy(SOURCE, copy)
    importer.import_sqlite(copy, db_url)
    assert copy.read_bytes() == SOURCE.read_bytes()


def test_a_database_that_holds_anything_is_refused_and_left_alone(db_url):
    with StockRepository(db_url) as stock:
        stock.add("flour", 100, "g", Location.PANTRY)
    with pytest.raises(importer.ImportRefusedError, match="already holds data"):
        importer.import_sqlite(SOURCE, db_url)
    with StockRepository(db_url) as stock:
        assert [i.ingredient for i in stock.list_items()] == ["flour"]
    with RecipeRepository(db_url) as recipes:
        assert recipes.recipes() == []


def test_a_setting_alone_is_enough_to_refuse(db_url):
    with StockRepository(db_url) as stock:
        stock.set_household_country("NZ")
    with pytest.raises(importer.ImportRefusedError, match="settings"):
        importer.import_sqlite(SOURCE, db_url)


def test_a_file_that_is_not_an_emu_database_is_refused(db_url, tmp_path):
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE notes (text TEXT)").connection.commit()
    with pytest.raises(importer.ImportRefusedError, match="not an emu database"):
        importer.import_sqlite(other, db_url)
    with pytest.raises(importer.ImportRefusedError, match="no file"):
        importer.import_sqlite(tmp_path / "missing.db", db_url)


def test_new_rows_get_ids_after_the_imported_ones(db_url):
    importer.import_sqlite(SOURCE, db_url)
    with RecipeRepository(db_url) as recipes:
        highest = max(r.id for r in recipes.recipes())
        new = recipes.add(
            "Toast", 1, [MealType.BREAKFAST], Weather.ANY, [], [("bread", 2, "count")]
        )
    assert new > highest
    with EaterRepository(db_url) as eaters:
        assert eaters.add("Robin", Category.TEEN).id == 4


def test_a_bad_row_rolls_the_whole_import_back(db_url, tmp_path):
    broken = tmp_path / "broken.db"
    shutil.copy(SOURCE, broken)
    db = sqlite3.connect(broken)  # foreign keys are not checked on this connection
    db.execute("UPDATE menu_portions SET eater_id = 999 WHERE rowid = 1")  # the last table copied
    db.commit()
    db.close()
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        importer.import_sqlite(broken, db_url)
    with StockRepository(db_url) as stock:
        assert stock.ingredients() == []  # the tables copied before it were rolled back too
    importer.import_sqlite(SOURCE, db_url)  # and it can be run again once fixed


def test_an_older_sqlite_layout_still_imports(db_url, tmp_path):
    """A file from before calorie adjustments, diets, reviews and saved takings."""
    old = tmp_path / "old.db"
    db = sqlite3.connect(old)
    db.executescript(
        """
        CREATE TABLE ingredients (id INTEGER PRIMARY KEY, name TEXT, base_unit TEXT);
        CREATE TABLE stock_items (id INTEGER PRIMARY KEY, ingredient_id INTEGER,
            quantity REAL, location TEXT, expires_on TEXT);
        CREATE TABLE eaters (id INTEGER PRIMARY KEY, name TEXT, category TEXT,
            calorie_goal INTEGER);
        CREATE TABLE menus (id INTEGER PRIMARY KEY, name TEXT, start_day TEXT, days INTEGER);
        CREATE TABLE menu_meals (id INTEGER PRIMARY KEY, menu_id INTEGER, day TEXT, slot TEXT,
            recipe_id INTEGER, reason TEXT, cooked_on TEXT);
        CREATE TABLE menu_meal_taken (meal_id INTEGER, ingredient TEXT, quantity REAL,
            unit TEXT, location TEXT, expires_on TEXT);
        INSERT INTO ingredients VALUES (3, 'flour', 'g');
        INSERT INTO stock_items VALUES (1, 3, 500, 'pantry', '2027-01-31');
        INSERT INTO eaters VALUES (1, 'Alex', 'man', 2700), (2, 'Mia', 'woman', NULL);
        INSERT INTO menus VALUES (1, 'Old menu', '2026-10-01', 1);
        INSERT INTO menu_meals VALUES (1, 1, '2026-10-01', 'dinner', NULL, '', '2026-10-01');
        INSERT INTO menu_meal_taken VALUES (1, 'flour', 20, 'g', 'pantry', NULL),
                                           (1, 'flour', 30, 'g', 'pantry', '2027-01-31');
        """
    )
    db.close()
    importer.import_sqlite(old, db_url)
    with StockRepository(db_url) as stock:
        (flour,) = stock.ingredients()
        assert (flour.id, flour.name, flour.reviewed, flour.kcal) == (3, "flour", False, None)
        assert stock.list_items()[0].expires_on == date(2027, 1, 31)
    with EaterRepository(db_url) as eaters:
        alex, mia = eaters.list()
        assert (alex.calorie_adjustment, alex.diet.value) == (200, "omnivore")
        assert mia.calorie_adjustment == 0
    with MenuRepository(db_url) as menus:
        (menu,) = menus.menus()
        assert menu.meals[0].cooked_on == date(2026, 10, 1)
        assert [lot.quantity for lot in menus.taken(1, 1)] == [20, 30]  # in the order taken


def test_the_command_prints_the_counts(db_url, capsys):
    assert importer.main([str(SOURCE), "--database-url", db_url]) == 0
    out = capsys.readouterr().out
    assert "menu_portions" in out and "Imported" in out


def test_the_command_says_why_it_refused(db_url, capsys):
    importer.main([str(SOURCE), "--database-url", db_url])
    assert importer.main([str(SOURCE), "--database-url", db_url]) == 1
    assert "Not imported: The database already holds data" in capsys.readouterr().err


def test_only_the_importer_imports_sqlite3():
    package = Path(importer.__file__).parent
    users = sorted(
        p.name for p in package.rglob("*.py") if "import sqlite3" in p.read_text(encoding="utf-8")
    )
    assert users == ["import_sqlite.py"]
