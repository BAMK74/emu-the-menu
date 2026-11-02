"""Recipes in the same SQLite file as the Stock, on a connection of its own."""

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from emu_the_menu.names import canonical_name, split_alternatives
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
    weather  TEXT NOT NULL DEFAULT 'any',
    source_url TEXT
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
    alt_group     INTEGER,
    PRIMARY KEY (recipe_id, ingredient_id)
);
"""


class RecipeNotFoundError(LookupError):
    """No Recipe has this id."""


@dataclass(frozen=True)
class RecipeLine:
    ingredient: Ingredient
    quantity: float  # in the Ingredient's base unit
    # Lines of one Recipe with the same number are alternatives: any one of them may be used.
    alt_group: int | None = None


@dataclass(frozen=True)
class Recipe:
    id: int
    name: str
    servings: int
    meal_types: tuple[MealType, ...]
    weather: Weather
    tags: tuple[str, ...]
    lines: tuple[RecipeLine, ...]
    source_url: str | None = None  # the web page it was imported from or copied from

    @property
    def for_analysis(self) -> list[tuple[Ingredient, float, int | None]]:
        """The lines as `recipes.analyse` wants them, with their groups of alternatives."""
        return [(line.ingredient, line.quantity, line.alt_group) for line in self.lines]

    @property
    def slots(self) -> list[list[Ingredient]]:
        """What the Recipe needs, one entry per line, a group of alternatives being one entry."""
        found: list[list[Ingredient]] = []
        placed: dict[int, list[Ingredient]] = {}
        for line in self.lines:
            if line.alt_group is None:
                found.append([line.ingredient])
            elif line.alt_group in placed:
                placed[line.alt_group].append(line.ingredient)
            else:
                placed[line.alt_group] = [line.ingredient]
                found.append(placed[line.alt_group])
        return found

    @property
    def source_host(self) -> str | None:
        return urlsplit(self.source_url).hostname if self.source_url else None


MAX_SOURCE_LENGTH = 500


def clean_source(url: str | None) -> str | None:
    """A web address to remember, or None if blank. Raises ValueError unless it is http or https."""
    url = (url or "").strip()
    if not url:
        return None
    if len(url) > MAX_SOURCE_LENGTH or urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("The source must be a web address starting with http:// or https://")
    if not urlsplit(url).hostname:
        raise ValueError("The source must be a web address starting with http:// or https://")
    return url


LineInput = tuple[str, float, str]  # ingredient name, quantity, unit as typed
_Entry = tuple[str, BaseUnit, float, int | None]  # name, base unit, amount, group


class RecipeRepository:
    def __init__(self, path: str | Path):
        # The Stock repository creates the ingredient tables and reads Ingredients for us.
        self._stock = StockRepository(path)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(recipes)")}
        if "source_url" not in columns:
            with self._db:
                self._db.execute("ALTER TABLE recipes ADD COLUMN source_url TEXT")
        line_columns = {row[1] for row in self._db.execute("PRAGMA table_info(recipe_lines)")}
        if "alt_group" not in line_columns:
            with self._db:
                self._db.execute("ALTER TABLE recipe_lines ADD COLUMN alt_group INTEGER")

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
        source_url: str | None = None,
    ) -> int:
        """Save a new Recipe and return its id.

        Nothing is saved, not even new Ingredients, if anything is wrong.
        """
        return self._save(None, name, servings, meal_types, weather, tags, lines, source_url)

    def update(
        self,
        recipe_id: int,
        name: str,
        servings: int,
        meal_types: Iterable[MealType],
        weather: Weather,
        tags: Iterable[str],
        lines: Sequence[LineInput],
        source_url: str | None = None,
    ) -> None:
        """Replace everything about a Recipe. Nothing changes if anything is wrong."""
        self._save(recipe_id, name, servings, meal_types, weather, tags, lines, source_url)

    def delete(self, recipe_id: int) -> None:
        """Delete a Recipe. Its Ingredients stay."""
        with self._db:
            if not self._db.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,)).rowcount:
                raise RecipeNotFoundError(recipe_id)

    def _save(self, recipe_id, name, servings, meal_types, weather, tags, lines, source_url) -> int:
        name = clean_recipe_name(name)
        servings = parse_servings(str(servings))
        meals = parse_meal_types(m.value for m in meal_types)
        tags = parse_tags(",".join(tags))
        amounts = _merged(self._entries(lines))
        source = clean_source(source_url)
        try:
            with self._db:
                if recipe_id is None:
                    recipe_id = self._db.execute(
                        "INSERT INTO recipes (name, servings, weather, source_url)"
                        " VALUES (?, ?, ?, ?)",
                        (name, servings, weather.value, source),
                    ).lastrowid
                else:
                    changed = self._db.execute(
                        "UPDATE recipes SET name = ?, servings = ?, weather = ?, source_url = ?"
                        " WHERE id = ?",
                        (name, servings, weather.value, source, recipe_id),
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
                for position, (ingredient, (base, amount, group)) in enumerate(amounts.items()):
                    self._db.execute(
                        "INSERT INTO recipe_lines"
                        " (recipe_id, ingredient_id, quantity, position, alt_group)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (
                            recipe_id,
                            self._ingredient_id(ingredient, base),
                            amount,
                            position,
                            group,
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            if "recipes.name" in str(exc):
                raise ValueError(f"You already have a recipe called {name}") from None
            raise
        return recipe_id

    def _entries(self, lines: Sequence[LineInput]) -> list[_Entry]:
        """The lines as one entry per Ingredient, each alternative being an Ingredient of its own.

        "water or vegetable broth" is two Ingredients that share a group. Each is named as one
        already stored where it is the same food ("extra-virgin olive oil" becomes "olive oil"),
        so one food is never stored twice. Raises ValueError for a blank name or a quantity of 0
        or less.
        """
        kinds = dict(self._db.execute("SELECT name, base_unit FROM ingredients"))
        entries: list[_Entry] = []
        for number, (ingredient, quantity, unit) in enumerate(lines):
            base, amount = base_unit_for(unit), to_base(quantity, unit)
            name = " ".join(ingredient.split())
            if not name:
                raise ValueError("Every ingredient line needs an ingredient")
            if not quantity > 0:
                raise ValueError(f"The quantity of {name.lower()} must be more than 0")
            alternatives = split_alternatives(name)
            group = number if len(alternatives) > 1 else None
            for alternative in alternatives:
                chosen = canonical_name(alternative, kinds, base.value)
                kinds.setdefault(chosen, base.value)
                entries.append((chosen.lower(), base, amount, group))
        return entries

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
            (rid, RecipeLine(ingredients[iid], quantity, group))
            for rid, iid, quantity, group in self._db.execute(
                "SELECT recipe_id, ingredient_id, quantity, alt_group FROM recipe_lines"
                " ORDER BY position"
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
                source_url,
            )
            for rid, name, servings, weather, source_url in self._db.execute(
                "SELECT id, name, servings, weather, source_url FROM recipes ORDER BY name"
            )
        ]

    def export_data(self) -> list[dict]:
        return [
            {
                "name": r.name,
                "servings": r.servings,
                "meal_types": [m.value for m in r.meal_types],
                "weather": r.weather.value,
                "source_url": r.source_url,
                "tags": list(r.tags),
                "lines": [
                    {
                        "ingredient": line.ingredient.name,
                        "quantity": line.quantity,
                        "unit": line.ingredient.base_unit.value,
                        "alternative_group": line.alt_group,
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


def _merged(entries: Sequence[_Entry]) -> dict[str, tuple[BaseUnit, float, int | None]]:
    """Ingredient entries in the units they will be stored in, the same Ingredient added together.

    Alternatives that end up as one Ingredient are kept once. An Ingredient that is both an
    alternative and a line of its own is a line of its own. Groups that are left with a single
    Ingredient are no longer alternatives, and the rest are numbered from 1.
    Raises ValueError for no entries at all.
    """
    merged: dict[str, tuple[BaseUnit, float, int | None]] = {}
    for name, base, amount, group in entries:
        if name in merged:
            old_base, old_amount, old_group = merged[name]
            if old_base is not base:
                raise IncompatibleUnitError(f"{name} is listed in two kinds of unit")
            if group is not None and group == old_group:
                continue  # the same alternative named twice
            merged[name] = (base, old_amount + amount, None)
        else:
            merged[name] = (base, amount, group)
    if not merged:
        raise ValueError("Add at least one ingredient")
    members: dict[int, int] = {}
    for _, _, group in merged.values():
        if group is not None:
            members[group] = members.get(group, 0) + 1
    numbers = {g: n for n, g in enumerate((g for g in members if members[g] > 1), start=1)}
    return {
        name: (base, amount, numbers.get(group) if group is not None else None)
        for name, (base, amount, group) in merged.items()
    }
