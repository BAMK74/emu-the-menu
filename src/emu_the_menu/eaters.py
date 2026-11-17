"""Eaters: the people a Household cooks for, with a Category, calorie goal and food restrictions.

Each Category has a default calorie goal that can be changed, and each Eater has an adjustment
to their Category's default, picked from a short list of sensible steps such as +200 kcal. Their
goal is the default plus the adjustment, so changing a Category's default moves everyone in it.
Each Eater also has a Diet. A strict diet (pescatarian, vegetarian, vegan) excludes whole food
groups as a hard rule, and low meat is a soft limit on meat. Allergies are hard exclusions and
dislikes are soft penalties when Menus are built later. Both
are stored as plain lowercase terms such as "peanuts". How those terms match Recipe ingredients
is decided when Recipes exist.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

import psycopg

from emu_the_menu.allergens import Allergen, parse_allergens
from emu_the_menu.db import Database, attach
from emu_the_menu.food_groups import FoodGroup


class Category(StrEnum):
    MAN = "man"
    WOMAN = "woman"
    TEEN = "teen"
    CHILD = "child"


# Rough daily energy needs, in kcal. Each can be changed on the Eaters page.
DEFAULT_CALORIE_GOALS: dict[Category, int] = {
    Category.MAN: 2500,
    Category.WOMAN: 2000,
    Category.TEEN: 2200,
    Category.CHILD: 1600,
}
# Steps of 100 kcal up to 500 either way, then larger ones for athletes and small eaters.
CALORIE_ADJUSTMENTS: tuple[int, ...] = (
    -1000, -750, -500, -400, -300, -200, -100, 0, 100, 200, 300, 400, 500, 750, 1000,
)  # fmt: skip
MIN_CALORIE_GOAL = 500
MAX_CALORIE_GOAL = 6000
MAX_NAME_LENGTH = 40


class EaterNotFoundError(LookupError):
    """No Eater has the given id."""


class Diet(StrEnum):
    OMNIVORE = "omnivore"
    LOW_MEAT = "low-meat"
    PESCATARIAN = "pescatarian"
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"

    @property
    def label(self) -> str:
        return _DIETS[self][0]

    @property
    def description(self) -> str:
        return _DIETS[self][1]

    @property
    def excluded_groups(self) -> frozenset[FoodGroup]:
        """Food groups a Menu must never serve this diet. Low meat excludes none, it only limits."""
        return _DIETS[self][2]

    @property
    def limits_meat(self) -> bool:
        """True for a soft preference to eat meat only now and then, which is not a ban."""
        return self is Diet.LOW_MEAT


_NO_MEAT = frozenset({FoodGroup.MEAT, FoodGroup.POULTRY})
_NO_FISH_EITHER = _NO_MEAT | {FoodGroup.FISH}
_DIETS: dict[Diet, tuple[str, str, frozenset[FoodGroup]]] = {
    Diet.OMNIVORE: ("Eats everything", "no restrictions", frozenset()),
    Diet.LOW_MEAT: ("Low meat", "meat only now and then", frozenset()),
    Diet.PESCATARIAN: ("Pescatarian", "fish, but no meat", _NO_MEAT),
    Diet.VEGETARIAN: ("Vegetarian", "no meat or fish", _NO_FISH_EITHER),
    Diet.VEGAN: (
        "Vegan",
        "no animal products",
        _NO_FISH_EITHER | {FoodGroup.DAIRY, FoodGroup.EGGS, FoodGroup.HONEY},
    ),
}


@dataclass(frozen=True)
class Eater:
    id: int | None
    name: str
    category: Category
    calorie_adjustment: int = 0
    allergies: tuple[str, ...] = ()
    dislikes: tuple[str, ...] = ()
    diet: Diet = Diet.OMNIVORE
    # Ticked from the 14 EU allergens. `allergies` holds anything else, as free text.
    allergens: tuple[Allergen, ...] = ()
    # The Category's current default, filled in by the repository. None means the built-in one.
    default_goal: int | None = None

    @property
    def default_calorie_goal(self) -> int:
        return self.default_goal or DEFAULT_CALORIE_GOALS[self.category]

    @property
    def calorie_goal(self) -> int:
        return self.default_calorie_goal + self.calorie_adjustment

    @property
    def has_custom_goal(self) -> bool:
        return self.calorie_adjustment != 0


def clean_name(name: str) -> str:
    """Trim and collapse spaces. Raises ValueError for a blank or very long name."""
    name = " ".join(name.split())
    if not name:
        raise ValueError("Enter a name")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"A name can be at most {MAX_NAME_LENGTH} characters")
    return name


def _clean_terms(items: Iterable[str]) -> tuple[str, ...]:
    """Lowercase, collapse spaces, drop blanks and repeats, keeping the order typed."""
    seen: dict[str, None] = {}
    for item in items:
        term = " ".join(item.split()).lower()
        if term:
            seen.setdefault(term)
    return tuple(seen)


def parse_terms(text: str) -> tuple[str, ...]:
    """Read a typed list like "Peanuts, shellfish; milk", split on commas, semicolons and lines."""
    return _clean_terms(re.split(r"[,;\n]", text))


def _check_goal(goal: int | None) -> int | None:
    if goal is not None and not MIN_CALORIE_GOAL <= goal <= MAX_CALORIE_GOAL:
        raise ValueError(
            f"The calorie goal should be between {MIN_CALORIE_GOAL} and {MAX_CALORIE_GOAL}"
        )
    return goal


def parse_calorie_goal(text: str) -> int | None:
    """Read a typed calorie goal. Blank means nothing, so the built-in default is used."""
    text = text.strip()
    if not text:
        return None
    if not re.fullmatch(r"\d+", text):
        raise ValueError("Enter the calorie goal as a whole number, or leave it blank")
    return _check_goal(int(text))


def _check_adjustment(adjustment: int) -> int:
    if adjustment not in CALORIE_ADJUSTMENTS:
        raise ValueError("Choose a calorie adjustment from the list")
    return adjustment


def parse_calorie_adjustment(text: str) -> int:
    """Read a form value such as "-200". Blank means no adjustment. Must be one of the choices."""
    text = text.strip()
    if not text:
        return 0
    if not re.fullmatch(r"[+-]?\d+", text):
        raise ValueError("Choose a calorie adjustment from the list")
    return _check_adjustment(int(text))


def parse_diet(text: str) -> Diet:
    """Read a form value such as "pescatarian". Blank means no diet: eats everything."""
    text = text.strip().lower()
    if not text:
        return Diet.OMNIVORE
    try:
        return Diet(text)
    except ValueError:
        raise ValueError("Choose a diet from the list") from None


def nearest_adjustment(difference: int) -> int:
    """The listed adjustment closest to `difference`, preferring the smaller one on a tie."""
    return min(CALORIE_ADJUSTMENTS, key=lambda step: (abs(difference - step), abs(step)))


class EaterRepository:
    """Eaters, in the same database as the Stock."""

    def __init__(self, database: str | Database | None = None):
        self._db, self._owned = attach(database)

    def __enter__(self) -> "EaterRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._owned:
            self._db.close()

    def default_goals(self) -> dict[Category, int]:
        """The default calorie goal of each Category: the built-in one unless it was changed."""
        goals = dict(DEFAULT_CALORIE_GOALS)
        for category, kcal in self._db.execute("SELECT category, kcal FROM category_goals"):
            goals[Category(category)] = kcal
        return goals

    def set_default_goals(self, goals: Mapping[Category, int | None]) -> None:
        """Change the default of some Categories. None goes back to the built-in goal.

        Every value is checked first, so a bad one changes nothing.
        """
        checked = {Category(category): _check_goal(kcal) for category, kcal in goals.items()}
        with self._db.transaction():
            for category, kcal in checked.items():
                if kcal is None:
                    self._db.execute(
                        "DELETE FROM category_goals WHERE category = %s", (category.value,)
                    )
                else:
                    self._db.execute(
                        "INSERT INTO category_goals (category, kcal) VALUES (%s, %s)"
                        " ON CONFLICT(category) DO UPDATE SET kcal = excluded.kcal",
                        (category.value, kcal),
                    )

    def list(self) -> list[Eater]:
        """Everyone, in the order they were added."""
        defaults = self.default_goals()
        rows = self._db.execute(
            "SELECT id, name, category, calorie_adjustment, diet FROM eaters ORDER BY id"
        ).fetchall()
        return [self._eater(*row, defaults) for row in rows]

    def get(self, eater_id: int) -> Eater | None:
        row = self._db.execute(
            "SELECT id, name, category, calorie_adjustment, diet FROM eaters WHERE id = %s",
            (eater_id,),
        ).fetchone()
        return self._eater(*row, self.default_goals()) if row else None

    def add(
        self,
        name: str,
        category: Category,
        calorie_adjustment: int = 0,
        allergies: Iterable[str] = (),
        dislikes: Iterable[str] = (),
        diet: Diet = Diet.OMNIVORE,
        allergens: Iterable[Allergen] = (),
    ) -> Eater:
        name = clean_name(name)
        category = Category(category)
        diet = Diet(diet)
        allergens = parse_allergens(Allergen(a).value for a in allergens)
        self._check_goal_for(category, calorie_adjustment)
        try:
            with self._db.transaction():
                (eater_id,) = self._db.execute(
                    "INSERT INTO eaters (name, category, calorie_adjustment, diet)"
                    " VALUES (%s, %s, %s, %s) RETURNING id",
                    (name, category.value, calorie_adjustment, diet.value),
                ).fetchone()
                self._save_terms(eater_id, allergies, dislikes)
                self._save_allergens(eater_id, allergens)
        except psycopg.errors.UniqueViolation:
            raise ValueError(f"Someone called {name} is already in the household") from None
        return self.get(eater_id)

    def update(
        self,
        eater_id: int,
        name: str,
        category: Category,
        calorie_adjustment: int = 0,
        allergies: Iterable[str] = (),
        dislikes: Iterable[str] = (),
        diet: Diet = Diet.OMNIVORE,
        allergens: Iterable[Allergen] = (),
    ) -> Eater:
        name = clean_name(name)
        category = Category(category)
        diet = Diet(diet)
        allergens = parse_allergens(Allergen(a).value for a in allergens)
        self._check_goal_for(category, calorie_adjustment)
        if self.get(eater_id) is None:
            raise EaterNotFoundError(eater_id)
        try:
            with self._db.transaction():
                self._db.execute(
                    "UPDATE eaters SET name = %s, category = %s, calorie_adjustment = %s, diet = %s"
                    " WHERE id = %s",
                    (name, category.value, calorie_adjustment, diet.value, eater_id),
                )
                self._db.execute("DELETE FROM eater_terms WHERE eater_id = %s", (eater_id,))
                self._db.execute("DELETE FROM eater_allergens WHERE eater_id = %s", (eater_id,))
                self._save_terms(eater_id, allergies, dislikes)
                self._save_allergens(eater_id, allergens)
        except psycopg.errors.UniqueViolation:
            raise ValueError(f"Someone called {name} is already in the household") from None
        return self.get(eater_id)

    def delete(self, eater_id: int) -> None:
        with self._db.transaction():
            cursor = self._db.execute("DELETE FROM eaters WHERE id = %s", (eater_id,))
            if cursor.rowcount == 0:
                raise EaterNotFoundError(eater_id)

    def _check_goal_for(self, category: Category, adjustment: int) -> None:
        """The adjustment must be a listed one and the resulting goal must stay in range."""
        _check_adjustment(adjustment)
        _check_goal(self.default_goals()[category] + adjustment)

    def _save_allergens(self, eater_id: int, allergens: Iterable[Allergen]) -> None:
        for allergen in allergens:
            self._db.execute(
                "INSERT INTO eater_allergens (eater_id, allergen) VALUES (%s, %s)",
                (eater_id, Allergen(allergen).value),
            )

    def _save_terms(self, eater_id: int, allergies: Iterable[str], dislikes: Iterable[str]) -> None:
        allergy_terms = _clean_terms(allergies)
        # An allergy is a hard exclusion, so the same term is not also kept as a dislike.
        dislike_terms = tuple(t for t in _clean_terms(dislikes) if t not in allergy_terms)
        for kind, terms in (("allergy", allergy_terms), ("dislike", dislike_terms)):
            for position, term in enumerate(terms):
                self._db.execute(
                    "INSERT INTO eater_terms (eater_id, kind, term, position)"
                    " VALUES (%s, %s, %s, %s)",
                    (eater_id, kind, term, position),
                )

    def _eater(
        self,
        eater_id: int,
        name: str,
        category: str,
        adjustment: int,
        diet: str,
        defaults: Mapping[Category, int],
    ) -> Eater:
        terms = self._db.execute(
            "SELECT kind, term FROM eater_terms WHERE eater_id = %s ORDER BY kind, position",
            (eater_id,),
        ).fetchall()
        allergens = self._db.execute(
            "SELECT allergen FROM eater_allergens WHERE eater_id = %s", (eater_id,)
        ).fetchall()
        return Eater(
            eater_id,
            name,
            Category(category),
            adjustment,
            allergies=tuple(term for kind, term in terms if kind == "allergy"),
            dislikes=tuple(term for kind, term in terms if kind == "dislike"),
            diet=Diet(diet),
            allergens=parse_allergens(value for (value,) in allergens),
            default_goal=defaults[Category(category)],
        )
