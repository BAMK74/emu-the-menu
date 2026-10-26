import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from emu_the_menu.repository import ItemNotFoundError, LastEntry, StockRepository
from emu_the_menu.shelf_life import ShelfLife, ShelfLifeUnit
from emu_the_menu.stock import InsufficientStockError, Location, StockItem
from emu_the_menu.units import BaseUnit, IncompatibleUnitError, UnknownUnitError

SOON = date(2026, 10, 28)
LATER = date(2026, 11, 15)


@pytest.fixture
def repo(tmp_path):
    with StockRepository(tmp_path / "emu.db") as repo:
        yield repo


def test_new_repository_is_empty(repo):
    assert repo.list_items() == []


def test_add_creates_the_ingredient_and_stores_in_base_unit(repo):
    repo.add("Flour", 1.5, "kg", Location.PANTRY, SOON)
    [item] = repo.list_items()
    assert item.id is not None
    assert replace(item, id=None) == StockItem("flour", 1500, BaseUnit.GRAM, Location.PANTRY, SOON)


def test_add_merges_into_the_same_row(repo):
    repo.add("flour", 100, "g", Location.PANTRY)
    repo.add(" FLOUR", 0.1, "kg", Location.PANTRY)
    [item] = repo.list_items()
    assert item.quantity == pytest.approx(200)


def test_add_rejects_a_unit_family_that_clashes_with_the_ingredient(repo):
    repo.add("milk", 1, "l", Location.FRIDGE)
    with pytest.raises(IncompatibleUnitError):
        repo.add("milk", 100, "g", Location.FRIDGE)
    assert len(repo.list_items()) == 1


def test_list_is_sorted_by_expiry_with_no_expiry_last(repo):
    repo.add("a", 1, "count", Location.PANTRY)
    repo.add("b", 1, "count", Location.PANTRY, LATER)
    repo.add("c", 1, "count", Location.PANTRY, SOON)
    assert [i.ingredient for i in repo.list_items()] == ["c", "b", "a"]


def test_use_deducts_earliest_expiry_first_and_removes_empty_rows(repo):
    repo.add("flour", 100, "g", Location.PANTRY, LATER)
    repo.add("flour", 100, "g", Location.PANTRY, SOON)
    repo.use("flour", 150, "g")
    [item] = repo.list_items()
    assert (item.quantity, item.expires_on) == (pytest.approx(50), LATER)


def test_use_with_too_little_stock_changes_nothing(repo):
    repo.add("flour", 100, "g", Location.PANTRY)
    with pytest.raises(InsufficientStockError):
        repo.use("flour", 500, "g")
    assert repo.list_items()[0].quantity == 100


def test_delete_removes_one_item(repo):
    repo.add("flour", 100, "g", Location.PANTRY)
    repo.add("sugar", 100, "g", Location.PANTRY)
    flour = next(i for i in repo.list_items() if i.ingredient == "flour")
    repo.delete(flour.id)
    assert [i.ingredient for i in repo.list_items()] == ["sugar"]


def test_set_quantity_adjusts_one_item(repo):
    repo.add("flour", 100, "g", Location.PANTRY)
    [item] = repo.list_items()
    repo.set_quantity(item.id, 2, "kg")
    assert repo.list_items()[0].quantity == pytest.approx(2000)


def test_data_survives_reopening_the_database(tmp_path):
    path = tmp_path / "emu.db"
    with StockRepository(path) as repo:
        repo.add("flour", 100, "g", Location.PANTRY, SOON)
    with StockRepository(path) as repo:
        assert repo.list_items()[0].expires_on == SOON


def test_set_quantity_rejects_a_different_unit_family(repo):
    repo.add("milk", 1, "l", Location.FRIDGE)
    [item] = repo.list_items()
    with pytest.raises(IncompatibleUnitError):
        repo.set_quantity(item.id, 2, "kg")
    assert repo.list_items()[0].quantity == 1000


def test_missing_item_raises(repo):
    with pytest.raises(ItemNotFoundError):
        repo.delete(999)
    with pytest.raises(ItemNotFoundError):
        repo.set_quantity(999, 1, "g")


def test_ingredients_remember_everything_ever_added(repo):
    repo.add("flour", 100, "g", Location.PANTRY)
    repo.add("milk", 1, "l", Location.FRIDGE)
    [flour] = [i for i in repo.list_items() if i.ingredient == "flour"]
    repo.delete(flour.id)
    assert [(i.name, i.base_unit) for i in repo.ingredients()] == [
        ("flour", BaseUnit.GRAM),
        ("milk", BaseUnit.MILLILITRE),
    ]


def test_shelf_life_is_remembered_per_ingredient(repo):
    week = ShelfLife(1, ShelfLifeUnit.WEEK)
    repo.add("Spinach", 1, "count", Location.FRIDGE, SOON, shelf_life=week)
    repo.add("flour", 1, "kg", Location.PANTRY)
    by_name = {i.name: i for i in repo.ingredients()}
    assert by_name["spinach"].shelf_life == week
    assert by_name["flour"].shelf_life is None


def test_a_newer_shelf_life_replaces_the_old_one(repo):
    repo.add("spinach", 1, "count", Location.FRIDGE, shelf_life=ShelfLife(1, ShelfLifeUnit.WEEK))
    repo.add("spinach", 1, "count", Location.FRIDGE, shelf_life=ShelfLife(3, ShelfLifeUnit.DAY))
    [spinach] = repo.ingredients()
    assert spinach.shelf_life == ShelfLife(3, ShelfLifeUnit.DAY)


def test_a_failed_add_remembers_nothing(repo):
    repo.add("milk", 1, "l", Location.FRIDGE)
    with pytest.raises(IncompatibleUnitError):
        repo.add("milk", 1, "g", Location.FRIDGE, shelf_life=ShelfLife(1, ShelfLifeUnit.DAY))
    assert repo.ingredients()[0].shelf_life is None


def test_an_old_database_without_shelf_life_columns_is_upgraded(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """CREATE TABLE ingredients (id INTEGER PRIMARY KEY,
               name TEXT NOT NULL UNIQUE COLLATE NOCASE, base_unit TEXT NOT NULL);
           CREATE TABLE stock_items (id INTEGER PRIMARY KEY,
               ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
               quantity REAL NOT NULL, location TEXT NOT NULL, expires_on TEXT);
           INSERT INTO ingredients (name, base_unit) VALUES ('flour', 'g');"""
    )
    db.commit()
    db.close()
    with StockRepository(path) as repo:
        assert repo.ingredients()[0].shelf_life is None
        repo.add("flour", 1, "kg", Location.PANTRY, shelf_life=ShelfLife(2, ShelfLifeUnit.MONTH))
        assert repo.ingredients()[0].shelf_life == ShelfLife(2, ShelfLifeUnit.MONTH)


def test_changing_the_base_unit_keeps_the_numbers_and_fixes_the_unit(repo):
    repo.add("chicken", 500, "ml", Location.FRIDGE)
    repo.change_base_unit("Chicken", "kg")
    [item] = repo.list_items()
    assert (item.quantity, item.base_unit) == (500, BaseUnit.GRAM)
    repo.add("chicken", 1, "kg", Location.FRIDGE)
    assert repo.list_items()[0].quantity == pytest.approx(1500)


def test_changing_the_base_unit_of_an_unknown_ingredient_does_nothing(repo):
    repo.change_base_unit("chicken", "g")
    assert repo.ingredients() == []


def test_changing_the_base_unit_rejects_an_unknown_unit(repo):
    repo.add("chicken", 500, "ml", Location.FRIDGE)
    with pytest.raises(UnknownUnitError):
        repo.change_base_unit("chicken", "furlong")
    assert repo.list_items()[0].base_unit == BaseUnit.MILLILITRE


def test_the_last_quantity_and_unit_entered_are_remembered(repo):
    repo.add("Kale", 2, " Bunch ", Location.FRIDGE)
    assert repo.ingredients()[0].last_entry == LastEntry(2, "bunch")
    repo.add("kale", 1.5, "head", Location.FRIDGE)
    assert repo.ingredients()[0].last_entry == LastEntry(1.5, "head")


def test_a_failed_add_does_not_change_the_remembered_quantity(repo):
    repo.add("milk", 1, "l", Location.FRIDGE)
    with pytest.raises(IncompatibleUnitError):
        repo.add("milk", 9, "g", Location.FRIDGE)
    assert repo.ingredients()[0].last_entry == LastEntry(1, "l")


def test_changing_the_base_unit_forgets_the_remembered_quantity(repo):
    repo.add("chicken", 500, "ml", Location.FRIDGE)
    repo.change_base_unit("chicken", "g")
    assert repo.ingredients()[0].last_entry is None


def test_an_old_database_without_last_entry_columns_is_upgraded(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """CREATE TABLE ingredients (id INTEGER PRIMARY KEY,
               name TEXT NOT NULL UNIQUE COLLATE NOCASE, base_unit TEXT NOT NULL,
               shelf_life_amount INTEGER, shelf_life_unit TEXT);
           CREATE TABLE stock_items (id INTEGER PRIMARY KEY,
               ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
               quantity REAL NOT NULL, location TEXT NOT NULL, expires_on TEXT);
           INSERT INTO ingredients (name, base_unit) VALUES ('flour', 'g');"""
    )
    db.commit()
    db.close()
    with StockRepository(path) as repo:
        assert repo.ingredients()[0].last_entry is None
        repo.add("flour", 1, "kg", Location.PANTRY)
        assert repo.ingredients()[0].last_entry == LastEntry(1, "kg")


def test_household_country_starts_unset_and_can_be_saved(repo):
    assert repo.household_country() is None
    repo.set_household_country("au")
    assert repo.household_country() == "AU"


def test_household_country_survives_reopening(tmp_path):
    path = tmp_path / "emu.db"
    with StockRepository(path) as repo:
        repo.set_household_country("NZ")
    with StockRepository(path) as repo:
        assert repo.household_country() == "NZ"


def test_household_country_can_be_changed_and_cleared(repo):
    repo.set_household_country("AU")
    repo.set_household_country("GB")
    assert repo.household_country() == "GB"
    repo.set_household_country("")
    assert repo.household_country() is None


def test_an_unknown_country_is_rejected(repo):
    with pytest.raises(ValueError, match="country"):
        repo.set_household_country("ZZ")
    assert repo.household_country() is None
