"""Saved Menus: a planned set of Meals kept in the database, so it can be opened again and changed.

A saved Meal holds the id of its Recipe and, for each Eater, how many servings they get. Calories,
names and allergen checks are not stored: they are worked out from the live Recipes and Eaters
each time, so a change to an Ingredient or a goal shows up in the saved Menu too.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from emu_the_menu.db import Database
from emu_the_menu.eaters import Eater, EaterRepository
from emu_the_menu.planner import Menu, Slot
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.stock import Location, TakenLot

_MENU_COLUMNS = "SELECT id, name, start_day, days, accepted_on, accepted_order FROM menus"
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
    short: str = ""  # what Stock could not cover when it was cooked
    guests: int = 0  # people beyond the Eaters, from an Occasion
    guest_servings: float = 0.0  # servings cooked for them, all together


@dataclass(frozen=True)
class SavedMenu:
    id: int
    name: str
    start: date
    days: int
    meals: tuple[SavedMeal, ...]
    accepted_on: date | None = None  # set while the Menu is accepted and holds Stock
    accepted_order: int | None = None  # 1 for the Menu accepted first


class MenuRepository:
    def __init__(self, database: str | Database | None = None):
        self._recipes = RecipeRepository(database)
        self._db = self._recipes.database
        self._eaters = EaterRepository(self._db)

    def __enter__(self) -> "MenuRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._eaters.close()
        self._recipes.close()

    def save(
        self, name: str, start: date, days: int, menu: Menu, table: Sequence[Eater] = ()
    ) -> int:
        """Keep a planned Menu and return its id.

        `table` is who the Menu is for. A Meal with no Recipe keeps them with no servings, so it
        is still known who a Recipe put there later has to suit.
        """
        name = " ".join(name.split()) or f"Menu from {start.day} {start:%b %Y}"
        with self._db.transaction():
            (menu_id,) = self._db.execute(
                "INSERT INTO menus (name, start_day, days) VALUES (%s, %s, %s) RETURNING id",
                (name, start, days),
            ).fetchone()
            for meal in menu.meals:
                (meal_id,) = self._db.execute(
                    "INSERT INTO menu_meals"
                    " (menu_id, day, slot, recipe_id, reason, guests, guest_servings)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        menu_id,
                        meal.day,
                        meal.slot.value,
                        meal.recipe.id if meal.recipe else None,
                        meal.reason,
                        meal.guests,
                        meal.guest_servings,
                    ),
                ).fetchone()
                self._db.executemany(
                    "INSERT INTO menu_portions (meal_id, eater_id, servings) VALUES (%s, %s, %s)",
                    [(meal_id, p.eater.id, p.servings) for p in meal.portions]
                    or [(meal_id, e.id, 0.0) for e in table],
                )
        return menu_id

    def get(self, menu_id: int) -> SavedMenu | None:
        row = self._db.execute(
            _MENU_COLUMNS + " WHERE id = %s",
            (menu_id,),
        ).fetchone()
        return self._menu(row) if row else None

    def menus(self) -> list[SavedMenu]:
        """Every saved Menu, newest first."""
        rows = self._db.execute(_MENU_COLUMNS + " ORDER BY id DESC")
        return [self._menu(row) for row in rows.fetchall()]

    def accept(self, menu_id: int, day: date) -> None:
        """Accept a Menu: its Meals set aside the Stock they need (see `reserve`).

        Menus are numbered in the order they were accepted, which decides who gets Stock that
        two of them need. Accepting a Menu that is already accepted changes nothing.
        """
        with self._db.transaction():
            row = self._db.execute(
                "SELECT accepted_order FROM menus WHERE id = %s", (menu_id,)
            ).fetchone()
            if row is None:
                raise MenuNotFoundError(menu_id)
            if row[0] is not None:
                return
            (top,) = self._db.execute(
                "SELECT COALESCE(MAX(accepted_order), 0) FROM menus"
            ).fetchone()
            self._db.execute(
                "UPDATE menus SET accepted_on = %s, accepted_order = %s WHERE id = %s",
                (day, top + 1, menu_id),
            )

    def unaccept(self, menu_id: int) -> None:
        """Give back the Stock a Menu was holding. Its place in line is forgotten."""
        with self._db.transaction():
            cursor = self._db.execute(
                "UPDATE menus SET accepted_on = NULL, accepted_order = NULL WHERE id = %s",
                (menu_id,),
            )
        if cursor.rowcount == 0:
            raise MenuNotFoundError(menu_id)

    def accepted_menus(self) -> list[SavedMenu]:
        """The accepted Menus, the one accepted first first."""
        rows = self._db.execute(
            _MENU_COLUMNS + " WHERE accepted_order IS NOT NULL ORDER BY accepted_order"
        )
        return [self._menu(row) for row in rows.fetchall()]

    def delete(self, menu_id: int) -> None:
        with self._db.transaction():
            cursor = self._db.execute("DELETE FROM menus WHERE id = %s", (menu_id,))
        if cursor.rowcount == 0:
            raise MenuNotFoundError(menu_id)

    def set_meal(
        self,
        menu_id: int,
        meal_id: int,
        recipe_id: int,
        portions: Sequence[tuple[int, float]],
        guests: int = 0,
        guest_servings: float = 0.0,
    ) -> None:
        """Put another Recipe in a Meal of a Menu, with each Eater's servings of it and the
        servings for any guests."""
        with self._db.transaction():
            _, cooked = self._row(menu_id, meal_id)
            if cooked:
                raise ValueError("A cooked meal cannot be changed. Mark it not cooked first")
            self._db.execute(
                "UPDATE menu_meals SET recipe_id = %s, reason = '',"
                " guests = %s, guest_servings = %s"
                " WHERE id = %s",
                (recipe_id, guests, guest_servings, meal_id),
            )
            self._db.execute("DELETE FROM menu_portions WHERE meal_id = %s", (meal_id,))
            self._db.executemany(
                "INSERT INTO menu_portions (meal_id, eater_id, servings) VALUES (%s, %s, %s)",
                [(meal_id, eater_id, servings) for eater_id, servings in portions],
            )

    def set_guests(self, menu_id: int, meal_id: int, guests: int, guest_servings: float) -> None:
        """Cook a Meal for `guests` more people, with the servings for them all together."""
        with self._db.transaction():
            recipe_id, cooked = self._row(menu_id, meal_id)
            if cooked:
                raise ValueError("A cooked meal cannot be changed. Mark it not cooked first")
            if recipe_id is None:
                raise ValueError("Choose a recipe for the meal before adding guests")
            self._db.execute(
                "UPDATE menu_meals SET guests = %s, guest_servings = %s WHERE id = %s",
                (guests, guest_servings, meal_id),
            )

    def set_cooked(
        self,
        menu_id: int,
        meal_id: int,
        day: date | None,
        short: str = "",
        took: Sequence[TakenLot] = (),
    ) -> None:
        """Record that a Meal was cooked on `day`, or with None that it was not.

        `short` says what Stock could not cover, to show beside the Meal, and `took` is the Stock
        it used up, kept so that it can be put back (see `taken`). Marking a Meal not cooked
        forgets both.
        """
        with self._db.transaction():
            recipe_id, _ = self._row(menu_id, meal_id)
            if day is not None and recipe_id is None:
                raise ValueError("A meal with no recipe cannot be cooked")
            self._db.execute(
                "UPDATE menu_meals SET cooked_on = %s, short = %s WHERE id = %s",
                (day, short if day else "", meal_id),
            )
            self._db.execute("DELETE FROM menu_meal_taken WHERE meal_id = %s", (meal_id,))
            if day is not None:
                self._db.executemany(
                    "INSERT INTO menu_meal_taken"
                    " (meal_id, ingredient, quantity, unit, location, expires_on)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    [
                        (
                            meal_id,
                            lot.ingredient,
                            lot.quantity,
                            lot.unit,
                            lot.location.value,
                            lot.expires_on,
                        )
                        for lot in took
                    ],
                )

    def taken(self, menu_id: int, meal_id: int) -> list[TakenLot]:
        """The Stock a cooked Meal used up, in the order it was taken."""
        self._row(menu_id, meal_id)
        rows = self._db.execute(
            "SELECT ingredient, quantity, unit, location, expires_on FROM menu_meal_taken"
            " WHERE meal_id = %s ORDER BY id",
            (meal_id,),
        )
        return [
            TakenLot(
                ingredient,
                quantity,
                unit,
                Location(location),
                expires_on,
            )
            for ingredient, quantity, unit, location, expires_on in rows
        ]

    def last_served(self) -> dict[int, date]:
        """When each Recipe was last cooked, from every saved Menu. This drives the Cooldown."""
        rows = self._db.execute(
            "SELECT recipe_id, MAX(cooked_on) FROM menu_meals"
            " WHERE cooked_on IS NOT NULL AND recipe_id IS NOT NULL GROUP BY recipe_id"
        )
        return dict(rows)

    def _row(self, menu_id: int, meal_id: int) -> tuple[int | None, date | None]:
        """A Meal's Recipe id and cooked date, if it belongs to the Menu."""
        row = self._db.execute(
            "SELECT recipe_id, cooked_on FROM menu_meals WHERE id = %s AND menu_id = %s",
            (meal_id, menu_id),
        ).fetchone()
        if row is None:
            raise MenuNotFoundError(meal_id)
        return row

    # --- reading ------------------------------------------------------------------------------

    def _menu(self, row) -> SavedMenu:
        menu_id, name, start, days, accepted_on, accepted_order = row
        order = {slot.value: n for n, slot in enumerate(Slot)}
        meals = [
            self._meal(m)
            for m in self._db.execute(
                "SELECT id, day, slot, recipe_id, reason, cooked_on, short, guests, guest_servings"
                " FROM menu_meals"
                " WHERE menu_id = %s",
                (menu_id,),
            ).fetchall()
        ]
        meals.sort(key=lambda m: (m.day, order[m.slot.value], m.id))
        return SavedMenu(
            menu_id,
            name,
            start,
            days,
            tuple(meals),
            accepted_on,
            accepted_order,
        )

    def _meal(self, row) -> SavedMeal:
        meal_id, day, slot, recipe_id, reason, cooked_on, short, guests, guest_servings = row
        portions = tuple(
            SavedPortion(eater_id, servings)
            for eater_id, servings in self._db.execute(
                "SELECT eater_id, servings FROM menu_portions WHERE meal_id = %s ORDER BY eater_id",
                (meal_id,),
            )
        )
        if recipe_id is None and not reason:
            reason = _DELETED_RECIPE
        return SavedMeal(
            meal_id,
            day,
            Slot(slot),
            recipe_id,
            reason,
            portions,
            cooked_on,
            short,
            guests,
            guest_servings,
        )
