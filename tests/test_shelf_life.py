from datetime import date

import pytest

from emu_the_menu.shelf_life import ShelfLife, ShelfLifeUnit

TODAY = date(2026, 10, 26)


@pytest.mark.parametrize(
    ("amount", "unit", "expected"),
    [
        (3, ShelfLifeUnit.DAY, date(2026, 10, 29)),
        (1, ShelfLifeUnit.WEEK, date(2026, 11, 2)),
        (2, ShelfLifeUnit.WEEK, date(2026, 11, 9)),
        (1, ShelfLifeUnit.MONTH, date(2026, 11, 26)),
        (3, ShelfLifeUnit.MONTH, date(2027, 1, 26)),
        (12, ShelfLifeUnit.MONTH, date(2027, 10, 26)),
    ],
)
def test_expires_from(amount, unit, expected):
    assert ShelfLife(amount, unit).expires_from(TODAY) == expected


def test_a_month_from_the_end_of_a_longer_month_clamps_to_the_month_end():
    assert ShelfLife(1, ShelfLifeUnit.MONTH).expires_from(date(2026, 1, 31)) == date(2026, 2, 28)
    assert ShelfLife(1, ShelfLifeUnit.MONTH).expires_from(date(2028, 1, 31)) == date(2028, 2, 29)


@pytest.mark.parametrize("amount", [0, -1])
def test_amount_must_be_positive(amount):
    with pytest.raises(ValueError, match="at least 1"):
        ShelfLife(amount, ShelfLifeUnit.DAY)


def test_parse_reads_form_text():
    assert ShelfLife.parse("2", "weeks") == ShelfLife(2, ShelfLifeUnit.WEEK)
    assert ShelfLife.parse(" 1 ", "month") == ShelfLife(1, ShelfLifeUnit.MONTH)


@pytest.mark.parametrize(
    ("amount", "unit"), [("", "days"), ("x", "days"), ("1.5", "days"), ("2", "years")]
)
def test_parse_rejects_nonsense(amount, unit):
    with pytest.raises(ValueError):
        ShelfLife.parse(amount, unit)
