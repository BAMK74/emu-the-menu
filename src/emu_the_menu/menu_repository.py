"""Saved Menus: a planned set of Meals kept in SQLite, so it can be opened again and changed.

A saved Meal holds the id of its Recipe and, for each Eater, how many servings they get. Calories,
names and allergen checks are not stored: they are worked out from the live Recipes and Eaters
each time, so a change to an Ingredient or a goal shows up in the saved Menu too.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from emu_the_menu.eaters import EaterRepository
from emu_the_menu.planner import Menu, Slot
from emu_the_menu.recipe_repository import RecipeRepository

_SCHEMA = """
CREATE TABLE IF NOT EXISTS menus (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    start_day  TEXT NOT NULL,
    days       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS menu_meals (
    id         INTEGER PRIMARY KEY,
    menu_id    INTEGER NOT NULL REFERENCES menus(id) ON DELETE CASCADE,
    day        TEXT NOT NULL,
    slot       TEXT NOT NULL,
    recipe_id  INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
    reason     TEXT NOT NULL DEFAULT '',
    cooked_on  TEXT
);
CREATE TABLE IF NOT EXISTS menu_portions (
    meal_id    INTEGER NOT NULL REFERENCES menu_meals(id) ON DELETE CASCADE,
    eater_id   INTEGER NOT NULL REFERENCES eaters(id) ON DELETE CASCADE,
    servings   REAL NOT NULL,
    PRIMARY KEY (meal_id, eater_id)
);
"""

_DELETED_RECIPE = "The recipe was deleted"


class MenuNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class SavedPortion:
    eater_id: int
    servings: float


@dataclass(frozen=True)
class SavedMeal:
    id: int
    day: date
    slot: Slot
    recipe_id: int | None
    reason: str
    portions: tuple[SavedPortion, ...]
    cooked_on: date | None


@dataclass(frozen=True)
class SavedMenu:
    id: int
    name: str
    start: date
    days: int
    meals: tuple[SavedMeal, ...]


class MenuRepository:
    def __init__(self, path: str | Path):
        # These create the tables the Menu tables point at.
        self._recipes = RecipeRepository(path)
        self._eaters = EaterRepository(path)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)

    def __enter__(self) -> "MenuRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()
        self._recipes.close()
        self._eaters.close()

    def save(self, name: str, start: date, days: int, menu: Menu) -> int:
        """Keep a planned Menu and return its id."""
        name = " ".join(name.split()) or f"Menu from {start.day} {start:%b %Y}"
        with self._db:
            menu_id = self._db.execute(
                "INSERT INTO menus (name, start_day, days) VALUES (?, ?, ?)",
                (name, start.isoformat(), days),
            ).lastrowid
            for meal in menu.meals:
                meal_id = self._db.execute(
                    "INSERT INTO menu_meals (menu_id, day, slot, recipe_id, reason)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        menu_id,
                        meal.day.isoformat(),
                        meal.slot.value,
                        meal.recipe.id if meal.recipe else None,
                        meal.reason,
                    ),
                ).lastrowid
                self._db.executemany(
                    "INSERT INTO menu_portions (meal_id, eater_id, servings) VALUES (?, ?, ?)",
                    [(meal_id, p.eater.id, p.servings) for p in meal.portions],
                )
        assert menu_id is not None
        return menu_id

    def get(self, menu_id: int) -> SavedMenu | None:
        row = self._db.execute(
            "SELECT id, name, start_day, days FROM menus WHERE id = ?", (menu_id,)
        ).fetchone()
        return self._menu(row) if row else None

    def menus(self) -> list[SavedMenu]:
        """Every saved Menu, newest first."""
        rows = self._db.execute("SELECT id, name, start_day, days FROM menus ORDER BY id DESC")
        return [self._menu(row) for row in rows.fetchall()]

    def delete(self, menu_id: int) -> None:
        with self._db:
            cursor = self._db.execute("DELETE FROM menus WHERE id = ?", (menu_id,))
        if cursor.rowcount == 0:
            raise MenuNotFoundError(menu_id)

    def set_meal(
        self,
        menu_id: int,
        meal_id: int,
        recipe_id: int,
        portions: Sequence[tuple[int, float]],
    ) -> None:
        """Put another Recipe in a Meal of a Menu, with each Eater's servings of it."""
        with self._db:
            _, cooked = self._row(menu_id, meal_id)
            if cooked:
                raise ValueError("A cooked meal cannot be changed. Mark it not cooked first")
            self._db.execute(
                "UPDATE menu_meals SET recipe_id = ?, reason = '' WHERE id = ?",
                (recipe_id, meal_id),
            )
            self._db.execute("DELETE FROM menu_portions WHERE meal_id = ?", (meal_id,))
            self._db.executemany(
                "INSERT INTO menu_portions (meal_id, eater_id, servings) VALUES (?, ?, ?)",
                [(meal_id, eater_id, servings) for eater_id, servings in portions],
            )

    def set_cooked(self, menu_id: int, meal_id: int, day: date | None) -> None:
        """Record that a Meal was cooked on `day`, or with None that it was not."""
        with self._db:
            recipe_id, _ = self._row(menu_id, meal_id)
            if day is not None and recipe_id is None:
                raise ValueError("A meal with no recipe cannot be cooked")
            self._db.execute(
                "UPDATE menu_meals SET cooked_on = ? WHERE id = ?",
                (day.isoformat() if day else None, meal_id),
            )

    def last_served(self) -> dict[int, date]:
        """When each Recipe was last cooked, from every saved Menu. This drives the Cooldown."""
        rows = self._db.execute(
            "SELECT recipe_id, MAX(cooked_on) FROM menu_meals"
            " WHERE cooked_on IS NOT NULL AND recipe_id IS NOT NULL GROUP BY recipe_id"
        )
        return {recipe_id: date.fromisoformat(day) for recipe_id, day in rows}

    def _row(self, menu_id: int, meal_id: int) -> tuple[int | None, str | None]:
        """A Meal's Recipe id and cooked date, if it belongs to the Menu."""
        row = self._db.execute(
            "SELECT recipe_id, cooked_on FROM menu_meals WHERE id = ? AND menu_id = ?",
            (meal_id, menu_id),
        ).fetchone()
        if row is None:
            raise MenuNotFoundError(meal_id)
        return row

    # --- reading ------------------------------------------------------------------------------

    def _menu(self, row) -> SavedMenu:
        menu_id, name, start, days = row
        order = {slot.value: n for n, slot in enumerate(Slot)}
        meals = [
            self._meal(m)
            for m in self._db.execute(
                "SELECT id, day, slot, recipe_id, reason, cooked_on FROM menu_meals"
                " WHERE menu_id = ?",
                (menu_id,),
            ).fetchall()
        ]
        meals.sort(key=lambda m: (m.day, order[m.slot.value], m.id))
        return SavedMenu(menu_id, name, date.fromisoformat(start), days, tuple(meals))

    def _meal(self, row) -> SavedMeal:
        meal_id, day, slot, recipe_id, reason, cooked_on = row
        portions = tuple(
            SavedPortion(eater_id, servings)
            for eater_id, servings in self._db.execute(
                "SELECT eater_id, servings FROM menu_portions WHERE meal_id = ? ORDER BY eater_id",
                (meal_id,),
            )
        )
        if recipe_id is None and not reason:
            reason = _DELETED_RECIPE
        return SavedMeal(
            meal_id,
            date.fromisoformat(day),
            Slot(slot),
            recipe_id,
            reason,
            portions,
            date.fromisoformat(cooked_on) if cooked_on else None,
        )
