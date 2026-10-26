"""Stock rules: adding, using and ordering Stock Items. Pure functions, no database."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from emu_the_menu.units import BaseUnit


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
    raise NotImplementedError


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
    raise NotImplementedError


def sort_by_expiry(items: list[StockItem]) -> list[StockItem]:
    """Return the items ordered by expiry, earliest first, with no expiry last."""
    raise NotImplementedError
