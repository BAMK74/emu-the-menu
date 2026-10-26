"""Unit conversion to an Ingredient's base unit (g, ml or count)."""

from enum import StrEnum


class BaseUnit(StrEnum):
    GRAM = "g"
    MILLILITRE = "ml"
    COUNT = "count"


class UnknownUnitError(ValueError):
    """The unit text is not one Emu understands."""


class IncompatibleUnitError(ValueError):
    """Two units measure different things, such as grams and millilitres."""


def base_unit_for(unit: str) -> BaseUnit:
    """Return the base unit that `unit` converts to.

    Unit text is case-insensitive and ignores surrounding whitespace.
    Raises UnknownUnitError for text Emu does not understand.
    """
    raise NotImplementedError


def to_base(quantity: float, unit: str) -> float:
    """Convert `quantity` of `unit` into the base unit for that unit's family.

    Raises ValueError for a negative quantity and UnknownUnitError for an unknown unit.
    """
    raise NotImplementedError


def convert(quantity: float, from_unit: str, to_unit: str) -> float:
    """Convert between two units of the same family.

    Raises IncompatibleUnitError when the units measure different things.
    """
    raise NotImplementedError
