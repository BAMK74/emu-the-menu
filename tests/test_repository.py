from dataclasses import replace
from datetime import date

import pytest

from emu_the_menu.repository import StockRepository
from emu_the_menu.stock import InsufficientStockError, Location, StockItem
from emu_the_menu.units import BaseUnit, IncompatibleUnitError

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
