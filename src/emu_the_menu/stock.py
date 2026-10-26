"""Stock rules: adding, using and ordering Stock Items. Pure functions, no database."""

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from emu_the_menu.units import BaseUnit, IncompatibleUnitError, base_unit_for, to_base

_EPSILON = 1e-9


class Location(StrEnum):
    FRIDGE = "fridge"
    FREEZER = "freezer"
    PANTRY = "pantry"


class InsufficientStockError(ValueError):
    """There is not enough of an Ingredient in Stock to use the requested quantity."""


@dataclass(frozen=True)
class StockItem:
    ingredient: str
    quantity: float  # always in `base_unit`
    base_unit: BaseUnit
    location: Location
    expires_on: date | None = None
    id: int | None = None  # set once the item is stored in the database


def _name(ingredient: str) -> str:
    return ingredient.strip().lower()


def _expiry_key(item: StockItem) -> tuple[bool, date]:
    return (item.expires_on is None, item.expires_on or date.max)


def add_stock(
    items: list[StockItem],
    ingredient: str,
    quantity: float,
    unit: str,
    location: Location,
    expires_on: date | None = None,
) -> list[StockItem]:
    """Return a new list with the quantity added.

    The quantity is converted to the base unit. Ingredient names are matched
    case-insensitively with surrounding whitespace ignored. Adding to an existing
    item with the same Ingredient, Location and expiry merges into it; otherwise a
    new Stock Item is created. The input list is not modified.
    """
    base = base_unit_for(unit)
    amount = to_base(quantity, unit)
    name = _name(ingredient)
    if any(_name(i.ingredient) == name and i.base_unit != base for i in items):
        raise IncompatibleUnitError(f"{unit!r} does not match how {name} is stored")
    result = list(items)
    for i, item in enumerate(result):
        if (
            _name(item.ingredient) == name
            and item.location == location
            and item.expires_on == expires_on
        ):
            result[i] = replace(item, quantity=item.quantity + amount)
            return result
    result.append(StockItem(name, amount, base, location, expires_on))
    return result


def reduce_stock(
    items: list[StockItem],
    ingredient: str,
    quantity: float,
    unit: str,
) -> list[StockItem]:
    """Return a new list with the quantity used up, earliest expiry first.

    Items with no expiry are used last. Items that reach zero are removed.
    Raises InsufficientStockError, leaving the input unchanged, when the total
    in Stock is less than the quantity requested.
    """
    base = base_unit_for(unit)
    needed = to_base(quantity, unit)
    name = _name(ingredient)
    matching = [item for item in items if _name(item.ingredient) == name]
    if any(item.base_unit != base for item in matching):
        raise IncompatibleUnitError(f"{unit!r} does not match how {name} is stored")
    if sum(item.quantity for item in matching) < needed - _EPSILON:
        raise InsufficientStockError(f"not enough {name} in Stock")

    remaining = needed
    updated: dict[int, StockItem | None] = {}
    for item in sorted(matching, key=_expiry_key):
        if remaining <= _EPSILON:
            break
        used = min(item.quantity, remaining)
        remaining -= used
        left = item.quantity - used
        updated[id(item)] = replace(item, quantity=left) if left > _EPSILON else None

    result = []
    for item in items:
        if id(item) in updated:
            if updated[id(item)] is not None:
                result.append(updated[id(item)])
        else:
            result.append(item)
    return result


def sort_by_expiry(items: list[StockItem]) -> list[StockItem]:
    """Return the items ordered by expiry, earliest first, with no expiry last."""
    return sorted(items, key=_expiry_key)
