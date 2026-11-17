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
def repo(db_url):
    with StockRepository(db_url) as repo:
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


def test_data_survives_reopening_the_database(db_url):
    with StockRepository(db_url) as repo:
        repo.add("flour", 100, "g", Location.PANTRY, SOON)
    with StockRepository(db_url) as repo:
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


def test_household_country_starts_unset_and_can_be_saved(repo):
    assert repo.household_country() is None
    repo.set_household_country("au")
    assert repo.household_country() == "AU"


def test_household_country_survives_reopening(db_url):
    with StockRepository(db_url) as repo:
        repo.set_household_country("NZ")
    with StockRepository(db_url) as repo:
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


def test_export_data_lists_everything_in_a_readable_form(repo):
    repo.set_household_country("AU")
    repo.add(
        "Spinach", 2, "bunch", Location.FRIDGE, SOON, shelf_life=ShelfLife(1, ShelfLifeUnit.WEEK)
    )
    repo.add("flour", 1.5, "kg", Location.PANTRY)
    data = repo.export_data()
    assert data["household"] == {"country": "AU"}
    assert data["stock_items"] == [
        {
            "ingredient": "spinach",
            "quantity": 2,
            "base_unit": "count",
            "location": "fridge",
            "expires_on": "2026-10-28",
        },
        {
            "ingredient": "flour",
            "quantity": 1500,
            "base_unit": "g",
            "location": "pantry",
            "expires_on": None,
        },
    ]
    spinach = next(i for i in data["ingredients"] if i["name"] == "spinach")
    assert spinach["shelf_life"] == {"amount": 1, "unit": "weeks"}
    assert spinach["last_entry"] == {"quantity": 2, "unit": "bunch"}


def test_export_data_of_an_empty_database(repo):
    data = repo.export_data()
    assert (data["ingredients"], data["stock_items"], data["household"]) == (
        [],
        [],
        {"country": None},
    )


# --- using what there is ----------------------------------------------------------------------


def test_use_available_takes_what_is_there_and_says_how_much(repo):
    repo.add("Flour", 300, "g", Location.PANTRY, SOON)
    assert repo.use_available("flour", 500, "g") == 300
    assert repo.list_items() == []


def test_use_available_takes_all_of_a_request_that_stock_covers(repo):
    repo.add("Flour", 300, "g", Location.PANTRY, SOON)
    repo.add("Flour", 300, "g", Location.PANTRY, LATER)
    assert repo.use_available("flour", 400, "g") == 400
    assert [(i.quantity, i.expires_on) for i in repo.list_items()] == [(200, LATER)]


def test_use_available_of_something_not_in_stock_takes_nothing(repo):
    assert repo.use_available("flour", 100, "g") == 0
    assert repo.list_items() == []


def test_use_available_still_refuses_a_unit_of_another_kind(repo):
    repo.add("Flour", 300, "g", Location.PANTRY, SOON)
    with pytest.raises(IncompatibleUnitError):
        repo.use_available("flour", 100, "ml")


def test_use_available_on_a_day_ignores_stock_that_has_expired_by_then(repo):
    repo.add("Milk", 500, "ml", Location.FRIDGE, SOON)
    repo.add("Milk", 500, "ml", Location.FRIDGE, LATER)
    day = date(2026, 11, 1)  # after SOON, before LATER
    assert repo.use_available("milk", 800, "ml", on=day) == 500
    assert [(i.quantity, i.expires_on) for i in repo.list_items()] == [(500, SOON)]


def test_use_available_on_a_day_keeps_stock_with_no_expiry_usable(repo):
    repo.add("Rice", 500, "g", Location.PANTRY)
    assert repo.use_available("rice", 200, "g", on=date(2030, 1, 1)) == 200


# --- knowing what was taken, and putting it back ----------------------------------------------


def test_take_available_reports_each_lot_it_took_from(repo):
    from emu_the_menu.stock import TakenLot

    repo.add("Milk", 300, "ml", Location.FRIDGE, SOON)
    repo.add("Milk", 300, "ml", Location.FREEZER, LATER)
    taken = repo.take_available("milk", 400, "ml")
    assert taken == [
        TakenLot("milk", 300, "ml", Location.FRIDGE, SOON),
        TakenLot("milk", 100, "ml", Location.FREEZER, LATER),
    ]


def test_take_available_reports_nothing_when_nothing_was_there(repo):
    assert repo.take_available("milk", 400, "ml") == []


def test_put_back_restores_a_lot_where_it_came_from(repo):
    from emu_the_menu.stock import TakenLot

    repo.add("Milk", 300, "ml", Location.FRIDGE, SOON)
    taken = repo.take_available("milk", 300, "ml")
    assert repo.list_items() == []
    repo.put_back(taken[0])
    [item] = repo.list_items()
    assert (item.ingredient, item.quantity, item.location, item.expires_on) == (
        "milk",
        300,
        Location.FRIDGE,
        SOON,
    )
    repo.put_back(TakenLot("milk", 50, "ml", Location.FRIDGE, SOON))
    assert repo.list_items()[0].quantity == 350  # merged into the matching lot


def test_put_back_does_not_change_what_adding_remembers(repo):
    from emu_the_menu.stock import TakenLot

    repo.add("Milk", 1, "l", Location.FRIDGE, SOON)
    before = repo.ingredients()[0].last_entry
    repo.put_back(TakenLot("milk", 100, "ml", Location.FRIDGE, SOON))
    assert repo.ingredients()[0].last_entry == before
