import pytest

from emu_the_menu.units import (
    BaseUnit,
    IncompatibleUnitError,
    UnknownUnitError,
    base_unit_for,
    convert,
    display_quantity,
    to_base,
)


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("g", BaseUnit.GRAM),
        ("kg", BaseUnit.GRAM),
        ("ml", BaseUnit.MILLILITRE),
        ("l", BaseUnit.MILLILITRE),
        ("tsp", BaseUnit.MILLILITRE),
        ("tbsp", BaseUnit.MILLILITRE),
        ("cup", BaseUnit.MILLILITRE),
        ("count", BaseUnit.COUNT),
        ("bunch", BaseUnit.COUNT),
        ("head", BaseUnit.COUNT),
    ],
)
def test_base_unit_for_each_family(unit, expected):
    assert base_unit_for(unit) == expected


@pytest.mark.parametrize(
    ("quantity", "unit", "expected"),
    [
        (500, "g", 500),
        (1, "kg", 1000),
        (0.25, "kg", 250),
        (250, "ml", 250),
        (2, "l", 2000),
        (1, "tsp", 5),
        (1, "tbsp", 15),
        (1, "cup", 240),
        (3, "count", 3),
        (2, "bunch", 2),
        (1, "head", 1),
    ],
)
def test_to_base(quantity, unit, expected):
    assert to_base(quantity, unit) == pytest.approx(expected)


def test_unit_text_ignores_case_and_whitespace():
    assert to_base(1, "  KG ") == pytest.approx(1000)
    assert base_unit_for(" Tbsp") == BaseUnit.MILLILITRE


def test_zero_quantity_is_allowed():
    assert to_base(0, "g") == 0


def test_negative_quantity_is_rejected():
    with pytest.raises(ValueError, match="negative"):
        to_base(-1, "g")


def test_unknown_unit_is_rejected():
    with pytest.raises(UnknownUnitError):
        to_base(1, "furlong")
    with pytest.raises(UnknownUnitError):
        base_unit_for("")


def test_convert_within_a_family():
    assert convert(1500, "g", "kg") == pytest.approx(1.5)
    assert convert(2, "cup", "ml") == pytest.approx(480)
    assert convert(1, "l", "tbsp") == pytest.approx(1000 / 15)


def test_bunch_and_head_count_as_whole_items():
    assert convert(2, "bunch", "count") == pytest.approx(2)
    assert convert(1, "head", "bunch") == pytest.approx(1)


def test_convert_across_families_is_rejected():
    with pytest.raises(IncompatibleUnitError):
        convert(100, "g", "ml")
    with pytest.raises(IncompatibleUnitError):
        convert(1, "count", "g")


def test_incompatible_unit_error_is_a_value_error():
    assert issubclass(IncompatibleUnitError, ValueError)
    assert issubclass(UnknownUnitError, ValueError)


@pytest.mark.parametrize(
    ("quantity", "base", "expected"),
    [
        (1000, BaseUnit.GRAM, (1, "kg")),
        (1500, BaseUnit.GRAM, (1.5, "kg")),
        (1234, BaseUnit.GRAM, (1.234, "kg")),
        (2500, BaseUnit.MILLILITRE, (2.5, "l")),
        (1000, BaseUnit.MILLILITRE, (1, "l")),
        (999, BaseUnit.GRAM, (999, "g")),
        (250, BaseUnit.MILLILITRE, (250, "ml")),
        (0, BaseUnit.GRAM, (0, "g")),
        (1500, BaseUnit.COUNT, (1500, "count")),
        (999.9996, BaseUnit.GRAM, (1, "kg")),
    ],
)
def test_display_quantity_uses_the_larger_unit_from_1000_up(quantity, base, expected):
    assert display_quantity(quantity, base) == expected


def test_display_quantity_gives_a_unit_the_forms_understand():
    for base in BaseUnit:
        unit = display_quantity(5000, base)[1]
        assert base_unit_for(unit) == base
