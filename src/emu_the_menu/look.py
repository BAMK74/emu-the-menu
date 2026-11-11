"""The look of the pages: a season, and at Halloween and Christmas a festive overlay, that colour
the app and choose the words and photos of the banner.

The look only changes the accent colours, the banner and the drawings. Safety colours (the amber
and red of allergens, expiry and warnings) never change with it, so a warning looks the same in
every season.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Season(StrEnum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"


HALLOWEEN = "halloween"  # 24 to 31 October
CHRISTMAS = "christmas"  # 15 to 26 December
FESTIVE = (HALLOWEEN, CHRISTMAS)
AUTO = "auto"  # the Settings choice that follows the date

# Countries wholly or mostly south of the equator, for when no town (and so no latitude) is set.
SOUTHERN_COUNTRIES = frozenset({"AR", "AU", "BR", "CL", "NZ", "ZA"})

_NORTH = {
    1: Season.WINTER, 2: Season.WINTER, 3: Season.SPRING, 4: Season.SPRING, 5: Season.SPRING,
    6: Season.SUMMER, 7: Season.SUMMER, 8: Season.SUMMER, 9: Season.AUTUMN, 10: Season.AUTUMN,
    11: Season.AUTUMN, 12: Season.WINTER,
}  # fmt: skip
_OPPOSITE = {
    Season.SPRING: Season.AUTUMN,
    Season.SUMMER: Season.WINTER,
    Season.AUTUMN: Season.SPRING,
    Season.WINTER: Season.SUMMER,
}

# The banner: a line, a sentence under it and six photos (see static/photos/credits.html).
COPY: dict[str, tuple[str, str]] = {
    "spring": (
        "Fresh, crisp and green",
        "Asparagus, peas and radishes are at their best. What will you cook this week?",
    ),
    "summer": (
        "Light, bright and quick",
        "Ripe tomatoes, sweet corn and dinners that taste like sunshine.",
    ),
    "autumn": (
        "Warm bowls and slow simmers",
        "Pumpkin, apples and mushrooms are in season. Time for something comforting.",
    ),
    "winter": (
        "Cosy suppers and big pots",
        "Soups, roasts and fresh bread to keep the house warm.",
    ),
    HALLOWEEN: ("Spooky snacks and pumpkin everything", "Carve one, cook one. What will you make?"),
    CHRISTMAS: ("Bake, roast and share", "Gingerbread, spice and a table full of people."),
}
PHOTOS: dict[str, tuple[str, ...]] = {
    "spring": ("asparagus", "radish", "peas", "strawberry", "egg", "basil"),
    "summer": ("corn", "tomato", "lemon", "watermelon", "peach", "basil"),
    "autumn": ("pumpkin", "mushroom", "apple", "pear", "carrot", "rosemary"),
    "winter": ("soup", "bread", "orange", "garlic", "carrot", "rosemary"),
    HALLOWEEN: ("pumpkin", "apple", "mushroom", "soup", "carrot", "pear"),
    CHRISTMAS: ("gingerbread", "cinnamon", "orange", "anise", "rosemary", "bread"),
}


def season_of(day: date, southern: bool = False) -> Season:
    """The season a date falls in: March to May is spring in the north, autumn in the south."""
    season = _NORTH[day.month]
    return _OPPOSITE[season] if southern else season


def festive_of(day: date) -> str | None:
    """Halloween in its last week of October, Christmas from mid December to Boxing Day."""
    if day.month == 10 and day.day >= 24:
        return HALLOWEEN
    if day.month == 12 and 15 <= day.day <= 26:
        return CHRISTMAS
    return None


def is_southern(country: str | None, latitude: float | None) -> bool:
    """Whether the Household is in the southern hemisphere: the town's latitude when a town is
    set, else the country, else no."""
    if latitude is not None:
        return latitude < 0
    return (country or "").upper() in SOUTHERN_COUNTRIES


@dataclass(frozen=True)
class LookChoice:
    """What Settings stores: follow the date, or pin one season; festive colours on or off."""

    season: str = AUTO  # AUTO or a Season value
    festive: bool = True


def parse_look_choice(season: str, festive: str) -> LookChoice:
    """Read the Settings form. `festive` is "on" or "off"."""
    season = season.strip().lower() or AUTO
    if season != AUTO and season not in {s.value for s in Season}:
        raise ValueError("Choose a season from the list, or follow the date")
    festive = festive.strip().lower() or "on"
    if festive not in ("on", "off"):
        raise ValueError("Festive colours are on or off")
    return LookChoice(season, festive == "on")


@dataclass(frozen=True)
class Look:
    season: Season
    festive: str | None  # HALLOWEEN, CHRISTMAS or None

    @property
    def scene(self) -> str:
        """Which banner and photos: the festive overlay when there is one, else the season."""
        return self.festive or self.season.value

    @property
    def line(self) -> str:
        return COPY[self.scene][0]

    @property
    def sub(self) -> str:
        return COPY[self.scene][1]

    @property
    def photos(self) -> tuple[str, ...]:
        return PHOTOS[self.scene]


def look_for(day: date, choice: LookChoice | None = None, southern: bool = False) -> Look:
    """The look on `day` for a Household with this choice (by default, follow the date), in this
    hemisphere."""
    choice = choice or LookChoice()
    season = season_of(day, southern) if choice.season == AUTO else Season(choice.season)
    return Look(season, festive_of(day) if choice.festive else None)
