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


# unit text -> (base unit, factor into that base unit)
_UNITS: dict[str, tuple[BaseUnit, float]] = {
    "g": (BaseUnit.GRAM, 1),
    "kg": (BaseUnit.GRAM, 1000),
    "ml": (BaseUnit.MILLILITRE, 1),
    "l": (BaseUnit.MILLILITRE, 1000),
    "tsp": (BaseUnit.MILLILITRE, 5),
    "tbsp": (BaseUnit.MILLILITRE, 15),
    "cup": (BaseUnit.MILLILITRE, 240),
    "count": (BaseUnit.COUNT, 1),
}


def known_units() -> tuple[str, ...]:
    """The unit names Emu understands, for building forms."""
    return tuple(_UNITS)


def _lookup(unit: str) -> tuple[BaseUnit, float]:
    try:
        return _UNITS[unit.strip().lower()]
    except KeyError:
        raise UnknownUnitError(f"unknown unit: {unit!r}") from None


def base_unit_for(unit: str) -> BaseUnit:
    """Return the base unit that `unit` converts to.

    Unit text is case-insensitive and ignores surrounding whitespace.
    Raises UnknownUnitError for text Emu does not understand.
    """
    return _lookup(unit)[0]


def to_base(quantity: float, unit: str) -> float:
    """Convert `quantity` of `unit` into the base unit for that unit's family.

    Raises ValueError for a negative quantity and UnknownUnitError for an unknown unit.
    """
    base, factor = _lookup(unit)
    if quantity < 0:
        raise ValueError(f"quantity cannot be negative: {quantity}")
    return quantity * factor


def convert(quantity: float, from_unit: str, to_unit: str) -> float:
    """Convert between two units of the same family.

    Raises IncompatibleUnitError when the units measure different things.
    """
    from_base, from_factor = _lookup(from_unit)
    to_base_unit, to_factor = _lookup(to_unit)
    if from_base != to_base_unit:
        raise IncompatibleUnitError(f"cannot convert {from_unit!r} to {to_unit!r}")
    return quantity * from_factor / to_factor
