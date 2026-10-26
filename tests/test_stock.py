from datetime import date

import pytest

from emu_the_menu.stock import (
    InsufficientStockError,
    Location,
    StockItem,
    add_stock,
    reduce_stock,
    sort_by_expiry,
)
from emu_the_menu.units import BaseUnit

TODAY = date(2026, 10, 26)
SOON = date(2026, 10, 28)
LATER = date(2026, 11, 15)


def flour(quantity, expires_on=None, location=Location.PANTRY):
    return StockItem("flour", quantity, BaseUnit.GRAM, location, expires_on)


# --- add_stock -------------------------------------------------------------


def test_add_to_empty_stock_converts_to_base_unit():
    result = add_stock([], "Flour", 1.5, "kg", Location.PANTRY)
    assert result == [flour(1500)]


def test_add_does_not_modify_the_input_list():
    items = [flour(100)]
    add_stock(items, "flour", 50, "g", Location.PANTRY)
    assert items == [flour(100)]


def test_add_merges_same_ingredient_location_and_expiry():
    items = [flour(100, SOON)]
    result = add_stock(items, "  FLOUR ", 0.5, "kg", Location.PANTRY, SOON)
    assert result == [flour(600, SOON)]


def test_add_keeps_separate_items_for_different_expiry():
    items = [flour(100, SOON)]
    result = add_stock(items, "flour", 100, "g", Location.PANTRY, LATER)
    assert sorted(i.expires_on for i in result) == [SOON, LATER]
    assert len(result) == 2


def test_add_keeps_separate_items_for_different_location():
    items = [flour(100, location=Location.PANTRY)]
    result = add_stock(items, "flour", 100, "g", Location.FRIDGE)
    assert {i.location for i in result} == {Location.PANTRY, Location.FRIDGE}


def test_add_rejects_negative_quantity():
    with pytest.raises(ValueError):
        add_stock([], "flour", -5, "g", Location.PANTRY)


# --- reduce_stock ----------------------------------------------------------


def test_reduce_takes_from_the_earliest_expiry_first():
    items = [flour(100, LATER), flour(100, SOON)]
    result = reduce_stock(items, "flour", 150, "g")
    assert sorted(result, key=lambda i: i.expires_on) == [flour(50, LATER)]


def test_reduce_uses_items_with_no_expiry_last():
    items = [flour(100, None), flour(100, LATER)]
    result = reduce_stock(items, "flour", 150, "g")
    assert result == [flour(50, None)]


def test_reduce_removes_items_that_reach_zero():
    result = reduce_stock([flour(100)], "flour", 100, "g")
    assert result == []


def test_reduce_converts_units_and_matches_names_loosely():
    result = reduce_stock([flour(1000)], " Flour", 0.25, "kg")
    assert result == [flour(750)]


def test_reduce_leaves_other_ingredients_alone():
    sugar = StockItem("sugar", 500, BaseUnit.GRAM, Location.PANTRY)
    result = reduce_stock([flour(100), sugar], "flour", 40, "g")
    assert sugar in result
    assert flour(60) in result


def test_reduce_raises_when_there_is_not_enough():
    items = [flour(100, SOON), flour(50, LATER)]
    with pytest.raises(InsufficientStockError):
        reduce_stock(items, "flour", 200, "g")
    assert items == [flour(100, SOON), flour(50, LATER)]


def test_reduce_raises_for_an_ingredient_not_in_stock():
    with pytest.raises(InsufficientStockError):
        reduce_stock([], "flour", 1, "g")


# --- sort_by_expiry --------------------------------------------------------


def test_sort_puts_earliest_first_and_no_expiry_last():
    items = [flour(1, None), flour(2, LATER), flour(3, SOON)]
    assert [i.quantity for i in sort_by_expiry(items)] == [3, 2, 1]


def test_sort_does_not_modify_the_input_list():
    items = [flour(1, LATER), flour(2, SOON)]
    sort_by_expiry(items)
    assert [i.quantity for i in items] == [1, 2]
