"""Recipes in the same SQLite file as the Stock, on a connection of its own."""

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from emu_the_menu.recipes import (
    MealType,
    Weather,
    clean_recipe_name,
    parse_meal_types,
    parse_servings,
    parse_tags,
    parse_weather,
)
from emu_the_menu.repository import Ingredient, StockRepository
from emu_the_menu.units import BaseUnit, IncompatibleUnitError, base_unit_for, to_base

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    servings INTEGER NOT NULL CHECK (servings >= 1),
    weather  TEXT NOT NULL DEFAULT 'any'
);
CREATE TABLE IF NOT EXISTS recipe_meal_types (
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    meal_type TEXT NOT NULL,
    PRIMARY KEY (recipe_id, meal_type)
);
CREATE TABLE IF NOT EXISTS recipe_tags (
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    tag       TEXT NOT NULL,
    position  INTEGER NOT NULL,
    PRIMARY KEY (recipe_id, tag)
);
CREATE TABLE IF NOT EXISTS recipe_lines (
    recipe_id     INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
    quantity      REAL NOT NULL CHECK (quantity > 0),
    position      INTEGER NOT NULL,
    PRIMARY KEY (recipe_id, ingredient_id)
);
"""


class RecipeNotFoundError(LookupError):
    """No Recipe has this id."""


@dataclass(frozen=True)
class RecipeLine:
    ingredient: Ingredient
    quantity: float  # in the Ingredient's base unit


@dataclass(frozen=True)
class Recipe:
    id: int
    name: str
    servings: int
    meal_types: tuple[MealType, ...]
    weather: Weather
    tags: tuple[str, ...]
    lines: tuple[RecipeLine, ...]


LineInput = tuple[str, float, str]  # ingredient name, quantity, unit as typed


class RecipeRepository:
    def __init__(self, path: str | Path):
        # The Stock repository creates the ingredient tables and reads Ingredients for us.
        self._stock = StockRepository(path)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)

    def __enter__(self) -> "RecipeRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()
        self._stock.close()

    # --- writing ------------------------------------------------------------------------------

    def add(
        self,
        name: str,
        servings: int,
        meal_types: Iterable[MealType],
        weather: Weather,
        tags: Iterable[str],
        lines: Sequence[LineInput],
    ) -> int:
        """Save a new Recipe and return its id.

        Nothing is saved, not even new Ingredients, if anything is wrong.
        """
        return self._save(None, name, servings, meal_types, weather, tags, lines)

    def update(
        self,
        recipe_id: int,
        name: str,
        servings: int,
        meal_types: Iterable[MealType],
        weather: Weather,
        tags: Iterable[str],
        lines: Sequence[LineInput],
    ) -> None:
        """Replace everything about a Recipe. Nothing changes if anything is wrong."""
        self._save(recipe_id, name, servings, meal_types, weather, tags, lines)

    def delete(self, recipe_id: int) -> None:
        """Delete a Recipe. Its Ingredients stay."""
        with self._db:
            if not self._db.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,)).rowcount:
                raise RecipeNotFoundError(recipe_id)

    def _save(self, recipe_id, name, servings, meal_types, weather, tags, lines) -> int:
        name = clean_recipe_name(name)
        servings = parse_servings(str(servings))
        meals = parse_meal_types(m.value for m in meal_types)
        tags = parse_tags(",".join(tags))
        amounts = _merged(lines)
        try:
            with self._db:
                if recipe_id is None:
                    recipe_id = self._db.execute(
                        "INSERT INTO recipes (name, servings, weather) VALUES (?, ?, ?)",
                        (name, servings, weather.value),
                    ).lastrowid
                else:
                    changed = self._db.execute(
                        "UPDATE recipes SET name = ?, servings = ?, weather = ? WHERE id = ?",
                        (name, servings, weather.value, recipe_id),
                    ).rowcount
                    if not changed:
                        raise RecipeNotFoundError(recipe_id)
                    for table in ("recipe_meal_types", "recipe_tags", "recipe_lines"):
                        self._db.execute(f"DELETE FROM {table} WHERE recipe_id = ?", (recipe_id,))
                self._db.executemany(
                    "INSERT INTO recipe_meal_types (recipe_id, meal_type) VALUES (?, ?)",
                    [(recipe_id, m.value) for m in meals],
                )
                self._db.executemany(
                    "INSERT INTO recipe_tags (recipe_id, tag, position) VALUES (?, ?, ?)",
                    [(recipe_id, tag, n) for n, tag in enumerate(tags)],
                )
                for position, (ingredient, (base, amount)) in enumerate(amounts.items()):
                    self._db.execute(
                        "INSERT INTO recipe_lines (recipe_id, ingredient_id, quantity, position)"
                        " VALUES (?, ?, ?, ?)",
                        (recipe_id, self._ingredient_id(ingredient, base), amount, position),
                    )
        except sqlite3.IntegrityError as exc:
            if "recipes.name" in str(exc):
                raise ValueError(f"You already have a recipe called {name}") from None
            raise
        return recipe_id

    def _ingredient_id(self, name: str, base: BaseUnit) -> int:
        """The id of an Ingredient, creating it (unreviewed) if it is new.

        Raises IncompatibleUnitError if it is stored in another kind of unit.
        """
        row = self._db.execute(
            "SELECT id, base_unit FROM ingredients WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return self._db.execute(
                "INSERT INTO ingredients (name, base_unit) VALUES (?, ?)", (name, base.value)
            ).lastrowid
        if BaseUnit(row[1]) is not base:
            raise IncompatibleUnitError(f"That unit does not match how {name} is stored")
        return row[0]

    # --- reading ------------------------------------------------------------------------------

    def get(self, recipe_id: int) -> Recipe | None:
        return next((r for r in self.recipes() if r.id == recipe_id), None)

    def recipes(self) -> list[Recipe]:
        """Every Recipe, by name, with its Ingredients as they are now."""
        ingredients = {i.id: i for i in self._stock.ingredients()}
        meals = _grouped(self._db.execute("SELECT recipe_id, meal_type FROM recipe_meal_types"))
        tags = _grouped(
            self._db.execute("SELECT recipe_id, tag FROM recipe_tags ORDER BY position")
        )
        lines = _grouped(
            (rid, RecipeLine(ingredients[iid], quantity))
            for rid, iid, quantity in self._db.execute(
                "SELECT recipe_id, ingredient_id, quantity FROM recipe_lines ORDER BY position"
            )
        )
        return [
            Recipe(
                rid,
                name,
                servings,
                parse_meal_types(meals.get(rid, ())),
                parse_weather(weather),
                tuple(tags.get(rid, ())),
                tuple(lines.get(rid, ())),
            )
            for rid, name, servings, weather in self._db.execute(
                "SELECT id, name, servings, weather FROM recipes ORDER BY name"
            )
        ]

    def export_data(self) -> list[dict]:
        return [
            {
                "name": r.name,
                "servings": r.servings,
                "meal_types": [m.value for m in r.meal_types],
                "weather": r.weather.value,
                "tags": list(r.tags),
                "lines": [
                    {
                        "ingredient": line.ingredient.name,
                        "quantity": line.quantity,
                        "unit": line.ingredient.base_unit.value,
                    }
                    for line in r.lines
                ],
            }
            for r in self.recipes()
        ]


def _grouped(pairs) -> dict[int, list]:
    found: dict[int, list] = {}
    for key, value in pairs:
        found.setdefault(key, []).append(value)
    return found


def _merged(lines: Sequence[LineInput]) -> dict[str, tuple[BaseUnit, float]]:
    """Ingredient lines in the units they will be stored in, the same Ingredient added together.

    Raises ValueError for a blank name, a quantity of 0 or less, or no lines at all.
    """
    merged: dict[str, tuple[BaseUnit, float]] = {}
    for ingredient, quantity, unit in lines:
        name = " ".join(ingredient.split()).lower()
        if not name:
            raise ValueError("Every ingredient line needs an ingredient")
        if not quantity > 0:
            raise ValueError(f"The quantity of {name} must be more than 0")
        base, amount = base_unit_for(unit), to_base(quantity, unit)
        if name in merged:
            if merged[name][0] is not base:
                raise IncompatibleUnitError(f"{name} is listed in two kinds of unit")
            amount += merged[name][1]
        merged[name] = (base, amount)
    if not merged:
        raise ValueError("Add at least one ingredient")
    return merged
