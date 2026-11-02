"""Recipes: the small fields, and what a Recipe's Ingredients add up to. No database here.

A Recipe's calories, food groups, allergens and diets all come from its Ingredients. The safety
rule is the one from Ingredients: an Ingredient nobody has reviewed is unknown, so a Recipe
containing one is "not checked", never "safe", and earns no diet badge.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from emu_the_menu.allergens import Allergen
from emu_the_menu.eaters import Diet
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.ingredients import calories_in
from emu_the_menu.repository import Ingredient

MAX_NAME_LENGTH = 80
MIN_SERVINGS, MAX_SERVINGS = 1, 50
MAX_TAGS = 10
MAX_TAG_LENGTH = 30


class MealType(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"

    @property
    def label(self) -> str:
        return self.value.capitalize()


class Weather(StrEnum):
    ANY = "any"
    WARM = "warm"
    COLD = "cold"

    @property
    def label(self) -> str:
        return {"any": "Any weather", "warm": "Warm weather", "cold": "Cold weather"}[self.value]


def clean_recipe_name(name: str) -> str:
    """Trim and collapse spaces. Raises ValueError for a blank or very long name."""
    name = " ".join(name.split())
    if not name:
        raise ValueError("Enter a name for the recipe")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"A recipe name can be at most {MAX_NAME_LENGTH} characters")
    return name


def parse_servings(text: str) -> int:
    text = text.strip()
    if not re.fullmatch(r"\d+", text) or not MIN_SERVINGS <= int(text) <= MAX_SERVINGS:
        raise ValueError(f"Servings must be a whole number from {MIN_SERVINGS} to {MAX_SERVINGS}")
    return int(text)


def parse_meal_types(values: Iterable[str]) -> tuple[MealType, ...]:
    found = set(values)
    unknown = found - {m.value for m in MealType}
    if unknown or not found:
        raise ValueError("Choose at least one meal type from the list")
    return tuple(m for m in MealType if m.value in found)


def parse_weather(text: str) -> Weather:
    try:
        return Weather(text.strip().lower() or "any")
    except ValueError:
        raise ValueError("Choose weather from the list") from None


def parse_tags(text: str) -> tuple[str, ...]:
    """Read typed tags such as "Festive, quick", split on commas, semicolons and lines."""
    seen: dict[str, None] = {}
    for item in re.split(r"[,;\n]", text):
        tag = " ".join(item.split()).lower()
        if tag:
            seen.setdefault(tag)
    if len(seen) > MAX_TAGS or any(len(tag) > MAX_TAG_LENGTH for tag in seen):
        raise ValueError(f"Use at most {MAX_TAGS} tags, each up to {MAX_TAG_LENGTH} characters")
    return tuple(seen)


# Diets that ban food groups, mildest first. Low meat and omnivore ban nothing, so need no badge.
_BADGE_DIETS = (Diet.PESCATARIAN, Diet.VEGETARIAN, Diet.VEGAN)


@dataclass(frozen=True)
class RecipeFacts:
    """What a Recipe's Ingredients add up to."""

    total_calories: float | None  # None unless every Ingredient has calories
    calories_per_serving: float | None
    without_calories: tuple[str, ...]
    food_groups: tuple[FoodGroup, ...]
    allergens: tuple[Allergen, ...]
    diets: tuple[Diet, ...]  # diets this Recipe fits, empty if anything is unchecked
    unchecked: tuple[str, ...]  # Ingredients nobody has reviewed

    @property
    def is_checked(self) -> bool:
        return not self.unchecked


def analyse(
    lines: Sequence[tuple[Ingredient, float] | tuple[Ingredient, float, int | None]],
    servings: int,
) -> RecipeFacts:
    """Add up Ingredient lines, each with its quantity in the Ingredient's base unit.

    A line may carry a third item, the number of a group of alternatives ("water or vegetable
    broth"). Any one of a group may be used, so the allergens, food groups and unreviewed
    Ingredients of all of them count, but only one of them is counted in the calories: the highest.
    """
    rows = [(row[0], row[1], row[2] if len(row) > 2 else None) for row in lines]
    calories = [calories_in(quantity, i.base_unit, i.kcal) for i, quantity, _ in rows]
    # What each group of alternatives or single line adds: None if any part has no calories.
    parts: dict[object, list[float | None]] = {}
    for n, ((_, _, group), kcal) in enumerate(zip(rows, calories, strict=True)):
        parts.setdefault(("group", group) if group is not None else ("line", n), []).append(kcal)
    counted = [
        None if any(k is None for k in kcals) else max(kcals)  # type: ignore[type-var]
        for kcals in parts.values()
    ]
    total = None if any(c is None for c in counted) else sum(counted)  # type: ignore[arg-type]
    groups = {g for i, _, _ in rows for g in i.food_groups}
    allergens = {a for i, _, _ in rows for a in i.allergens}
    unchecked = tuple(dict.fromkeys(i.name for i, _, _ in rows if not i.reviewed))
    diets = () if unchecked else tuple(d for d in _BADGE_DIETS if not groups & d.excluded_groups)
    return RecipeFacts(
        total_calories=total,
        calories_per_serving=None if total is None else total / servings,
        without_calories=tuple(
            dict.fromkeys(i.name for (i, _, _), c in zip(rows, calories, strict=True) if c is None)
        ),
        food_groups=tuple(g for g in FoodGroup if g in groups),
        allergens=tuple(a for a in Allergen if a in allergens),
        diets=diets,
        unchecked=unchecked,
    )
