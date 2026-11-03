"""Occasions: dated events that shape the Meals around them (see GLOSSARY.md).

An Occasion has a name, a date, an optional headcount (everyone at the table, the household
included; blank means just the household), tags that favour Recipes carrying the same tag, and an
optional extra calories percentage that raises every Eater's calorie goal for that day. Pure rules
only, no database.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from emu_the_menu.recipes import parse_tags

MAX_NAME = 80
MAX_HEADCOUNT = 200
MAX_EXTRA_PERCENT = 50


class Source(StrEnum):
    MANUAL = "manual"
    HOLIDAY = "holiday"  # imported public holiday


@dataclass(frozen=True)
class Occasion:
    name: str
    day: date
    headcount: int | None = None  # None: just the Household
    extra_percent: int = 0  # raises every Eater's calorie goal that day by this much
    tags: tuple[str, ...] = ()
    source: Source = Source.MANUAL
    id: int | None = None

    def guests(self, eaters: int) -> int:
        """People at the table beyond the Eaters of the Household."""
        return max(0, (self.headcount or 0) - eaters)


def parse_name(text: str) -> str:
    name = " ".join(text.split())
    if not name:
        raise ValueError("Enter a name for the occasion")
    if len(name) > MAX_NAME:
        raise ValueError(f"The name can be at most {MAX_NAME} characters")
    return name


def parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise ValueError("Enter a valid date") from None


def parse_headcount(text: str) -> int | None:
    """Blank means just the Household."""
    text = text.strip()
    if not text:
        return None
    if not text.isascii() or not text.isdigit() or not 1 <= int(text) <= MAX_HEADCOUNT:
        raise ValueError(f"The headcount must be a whole number from 1 to {MAX_HEADCOUNT}")
    return int(text)


def parse_extra_percent(text: str) -> int:
    """Blank means no change."""
    text = text.strip().removesuffix("%").strip()
    if not text:
        return 0
    if not text.isascii() or not text.isdigit() or int(text) > MAX_EXTRA_PERCENT:
        raise ValueError(
            f"The extra calories must be a whole number from 0 to {MAX_EXTRA_PERCENT}%"
        )
    return int(text)


def read_form(name: str, day: str, headcount: str, extra: str, tags: str) -> tuple:
    """The fields of a typed Occasion, checked. Raises ValueError with a message to show."""
    return (
        parse_name(name),
        parse_day(day),
        parse_headcount(headcount),
        parse_extra_percent(extra),
        parse_tags(tags),
    )


def combine(occasions: Iterable[Occasion]) -> Occasion | None:
    """One Occasion standing for all those on the same day: the biggest table, the biggest calorie
    allowance and every tag. None if there are none."""
    chosen = list(occasions)
    if not chosen:
        return None
    first = chosen[0]
    if len(chosen) == 1:
        return first
    heads = [o.headcount for o in chosen if o.headcount]
    tags = dict.fromkeys(tag for o in chosen for tag in o.tags)
    return Occasion(
        " and ".join(o.name for o in chosen),
        first.day,
        max(heads) if heads else None,
        max(o.extra_percent for o in chosen),
        tuple(tags),
        first.source,
    )
