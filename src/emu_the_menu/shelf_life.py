"""How long an Ingredient keeps, as a count of days, weeks or months."""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum


class ShelfLifeUnit(StrEnum):
    DAY = "days"
    WEEK = "weeks"
    MONTH = "months"


@dataclass(frozen=True)
class ShelfLife:
    amount: int
    unit: ShelfLifeUnit

    def __post_init__(self) -> None:
        if self.amount < 1:
            raise ValueError("Shelf life must be at least 1")

    @classmethod
    def parse(cls, amount: str, unit: str) -> "ShelfLife":
        """Read form text such as ("2", "weeks"). Singular units ("week") also work."""
        try:
            count = int(amount.strip())
        except ValueError:
            raise ValueError("Enter a whole number for how long it keeps") from None
        name = unit.strip().lower()
        try:
            parsed_unit = ShelfLifeUnit(name if name.endswith("s") else name + "s")
        except ValueError:
            raise ValueError("Choose days, weeks or months") from None
        return cls(count, parsed_unit)

    def expires_from(self, start: date) -> date:
        """The date this shelf life ends when it starts on `start`."""
        if self.unit is ShelfLifeUnit.DAY:
            return start + timedelta(days=self.amount)
        if self.unit is ShelfLifeUnit.WEEK:
            return start + timedelta(weeks=self.amount)
        months = start.month - 1 + self.amount
        year, month = start.year + months // 12, months % 12 + 1
        day = min(start.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)
