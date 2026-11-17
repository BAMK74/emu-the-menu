"""Postgres storage for Ingredients and Stock Items.

The stock rules stay in `stock.py` as pure functions. The repository loads the
items, applies a rule and writes back only what changed, all in one transaction.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from emu_the_menu.allergens import Allergen, parse_allergens
from emu_the_menu.countries import COUNTRIES
from emu_the_menu.db import Database, attach
from emu_the_menu.food_groups import FoodGroup, parse_food_groups
from emu_the_menu.ingredients import check_kcal, kcal_basis
from emu_the_menu.look import AUTO, LookChoice
from emu_the_menu.names import canonical_name, clean_name, name_key, split_alternatives
from emu_the_menu.openfoodfacts import ProductInfo
from emu_the_menu.shelf_life import ShelfLife, ShelfLifeUnit
from emu_the_menu.stock import (
    Location,
    StockItem,
    TakenLot,
    add_stock,
    reduce_stock,
    sort_by_expiry,
)
from emu_the_menu.units import BaseUnit, IncompatibleUnitError, base_unit_for, to_base


class IngredientNotFoundError(LookupError):
    """No Ingredient has the given id."""


class ItemNotFoundError(LookupError):
    """No Stock Item has the given id."""


@dataclass(frozen=True)
class LastEntry:
    """The quantity and unit text last typed for an Ingredient, such as 2 "bunch"."""

    quantity: float
    unit: str


@dataclass(frozen=True)
class Ingredient:
    name: str
    base_unit: BaseUnit
    shelf_life: ShelfLife | None = None  # how long it usually keeps, as last entered
    last_entry: LastEntry | None = None
    id: int | None = None
    # Calories per 100 g, per 100 ml or per piece (see `kcal_basis`), if known.
    kcal: float | None = None
    food_groups: tuple[FoodGroup, ...] = ()
    allergens: tuple[Allergen, ...] = ()
    # True once a person has saved this Ingredient's details. Until then nothing is known, and
    # empty food groups and allergens mean "not checked", not "none".
    reviewed: bool = False


MAX_INGREDIENT_NAME = 60
_UNIT_SUFFIX = {"g": " (by weight)", "ml": " (by volume)", "count": " (counted)"}


class StockRepository:
    def __init__(self, database: str | Database | None = None, auto_split: bool = True):
        """Open the Stock in `database`: a connection URI, another repository's Database to
        share, or None for `EMU_DATABASE_URL`."""
        self._db, self._owned = attach(database)
        if auto_split:
            self._split_stored_alternatives()

    @property
    def database(self) -> Database:
        """The connection this repository works on, for repositories that share it."""
        return self._db

    def __enter__(self) -> "StockRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._owned:
            self._db.close()

    def list_items(self) -> list[StockItem]:
        return sort_by_expiry(self._load())

    def add(
        self,
        ingredient: str,
        quantity: float,
        unit: str,
        location: Location,
        expires_on: date | None = None,
        shelf_life: ShelfLife | None = None,
    ) -> None:
        """Add Stock. The first add of an Ingredient fixes its base unit from `unit`.

        The quantity and unit typed are remembered for the Ingredient, and so is a
        `shelf_life` if given, each replacing any earlier one. Nothing is remembered
        if the add fails.
        """
        if len(split_alternatives(ingredient)) > 1:
            raise ValueError(
                f"{ingredient.strip()} is more than one ingredient. Add each to stock on its"
                " own: a choice between ingredients belongs in a recipe."
            )
        ingredient = self._canonical(ingredient, unit)
        self._apply(
            lambda items: add_stock(items, ingredient, quantity, unit, location, expires_on)
        )
        with self._db.transaction():
            name = ingredient.strip().lower()
            self._db.execute(
                "UPDATE ingredients SET last_quantity = %s, last_unit = %s"
                " WHERE lower(name) = lower(%s)",
                (quantity, unit.strip().lower(), name),
            )
            if shelf_life:
                self._db.execute(
                    "UPDATE ingredients SET shelf_life_amount = %s, shelf_life_unit = %s"
                    " WHERE lower(name) = lower(%s)",
                    (shelf_life.amount, shelf_life.unit.value, name),
                )

    def use(self, ingredient: str, quantity: float, unit: str) -> None:
        """Deduct Stock, earliest expiry first. Changes nothing if there is not enough."""
        ingredient = self._canonical(ingredient, unit)
        self._apply(lambda items: reduce_stock(items, ingredient, quantity, unit))

    def use_available(
        self, ingredient: str, quantity: float, unit: str, on: date | None = None
    ) -> float:
        """Deduct as much as there is, up to `quantity`, and return how much was taken."""
        return sum(lot.quantity for lot in self.take_available(ingredient, quantity, unit, on))

    def take_available(
        self, ingredient: str, quantity: float, unit: str, on: date | None = None
    ) -> list[TakenLot]:
        """Deduct as much as there is, up to `quantity`, and say which Stock it came from.

        Unlike `use` this never fails for lack of Stock: cooking takes what is there. With `on`,
        Stock that has expired by that day is left alone and does not count. The lots returned
        are in the Ingredient's base unit and can be handed to `put_back`.
        """
        name = self._canonical(ingredient, unit)

        def usable(item: StockItem) -> bool:
            same = item.ingredient.strip().lower() == name
            return same and (on is None or item.expires_on is None or item.expires_on >= on)

        before = {i.id: i for i in self.list_items() if usable(i)}
        taken = min(sum(i.quantity for i in before.values()), to_base(quantity, unit))
        if taken <= 0:
            return []
        base = base_unit_for(unit).value

        def rule(items: list[StockItem]) -> list[StockItem]:
            fresh = [i for i in items if usable(i) or i.ingredient.strip().lower() != name]
            spoiled = [i for i in items if i not in fresh]
            return reduce_stock(fresh, name, taken, base) + spoiled

        self._apply(rule)
        after = {i.id: i for i in self.list_items()}
        lots = []
        for item_id, item in before.items():
            used = item.quantity - (after[item_id].quantity if item_id in after else 0)
            if used > 1e-9:
                lots.append(TakenLot(name, used, base, item.location, item.expires_on))
        return sorted(lots, key=lambda lot: (lot.expires_on is None, lot.expires_on or date.max))

    def put_back(self, lot: TakenLot) -> None:
        """Return a lot taken with `take_available` to the Stock it came from.

        It merges into a matching item (same place and expiry) or makes a new one, and does not
        change what adding remembers for the Ingredient.
        """
        self._apply(
            lambda items: add_stock(
                items, lot.ingredient, lot.quantity, lot.unit, lot.location, lot.expires_on
            )
        )

    def set_quantity(self, item_id: int, quantity: float, unit: str) -> None:
        amount = to_base(quantity, unit)
        with self._db.transaction():
            row = self._db.execute(
                """SELECT i.base_unit FROM stock_items s
                   JOIN ingredients i ON i.id = s.ingredient_id WHERE s.id = %s""",
                (item_id,),
            ).fetchone()
            if row is None:
                raise ItemNotFoundError(item_id)
            if base_unit_for(unit) != BaseUnit(row[0]):
                raise IncompatibleUnitError(f"{unit!r} does not match how this item is stored")
            self._db.execute(
                "UPDATE stock_items SET quantity = %s WHERE id = %s", (amount, item_id)
            )

    def update_item(
        self,
        item_id: int,
        quantity: float,
        unit: str,
        location: Location,
        expires_on: date | None = None,
    ) -> None:
        """Change what one Stock Item holds, where it is kept and when it expires.

        The unit must be in the same family as the Ingredient is stored in. Nothing is
        changed if any value is rejected.
        """
        amount = to_base(quantity, unit)
        location = Location(location)
        with self._db.transaction():
            row = self._db.execute(
                """SELECT i.base_unit FROM stock_items s
                   JOIN ingredients i ON i.id = s.ingredient_id WHERE s.id = %s""",
                (item_id,),
            ).fetchone()
            if row is None:
                raise ItemNotFoundError(item_id)
            if base_unit_for(unit) != BaseUnit(row[0]):
                raise IncompatibleUnitError(f"{unit!r} does not match how this item is stored")
            self._db.execute(
                "UPDATE stock_items SET quantity = %s, location = %s, expires_on = %s"
                " WHERE id = %s",
                (amount, location.value, expires_on, item_id),
            )

    def change_base_unit(self, ingredient: str, unit: str) -> None:
        """Correct the unit an Ingredient is stored in, such as chicken entered in ml by mistake.

        Quantities already in Stock keep their numbers and are read in the new unit,
        because grams cannot be converted from millilitres. The remembered quantity
        is forgotten, as its unit may be in the wrong family, and so are the calories, which are
        measured against the old unit. Does nothing if the
        Ingredient is unknown.
        """
        base = base_unit_for(unit)
        ingredient = self._canonical(ingredient)
        with self._db.transaction():
            self._db.execute(
                "UPDATE ingredients SET kcal = CASE WHEN base_unit = %s THEN kcal END,"
                " base_unit = %s, last_quantity = NULL, last_unit = NULL"
                " WHERE lower(name) = lower(%s)",
                (base.value, base.value, ingredient.strip().lower()),
            )

    def _canonical(self, name: str, unit: str | None = None) -> str:
        """The name an Ingredient is stored under, reusing one that is the same food."""
        kinds = dict(self._db.execute("SELECT name, base_unit FROM ingredients"))
        return canonical_name(name, kinds, base_unit_for(unit).value if unit else None)

    def twin_groups(self) -> list[list[Ingredient]]:
        """Groups of Ingredients that are one food written two ways, in the same kind of unit.

        Such as "extra virgin olive oil" and "extra-virgin olive oil", or salt and sea salt.
        """
        groups: dict[tuple[str, str], list[Ingredient]] = {}
        for ingredient in self.ingredients():
            key = (name_key(ingredient.name), ingredient.base_unit.value)
            groups.setdefault(key, []).append(ingredient)
        return [group for group in groups.values() if len(group) > 1]

    def merge_ingredients(self, keep_id: int, drop_id: int) -> None:
        """Make `drop_id` part of `keep_id` and delete the duplicate.

        Its Recipe lines and Stock move over, adding up where a Recipe had both. What was
        ticked on either is combined, so every allergen and food group of both is kept and the
        result is reviewed if either was. Raises IncompatibleUnitError if they are stored in
        different kinds of unit.
        """
        keep, drop = self.get_ingredient(keep_id), self.get_ingredient(drop_id)
        if keep is None or drop is None:
            raise IngredientNotFoundError(keep_id if keep is None else drop_id)
        if keep.base_unit is not drop.base_unit:
            raise IncompatibleUnitError(f"{keep.name} and {drop.name} use different units")
        with self._db.transaction():
            lines = self._db.execute(
                "SELECT recipe_id, quantity FROM recipe_lines WHERE ingredient_id = %s",
                (drop_id,),
            ).fetchall()
            for recipe_id, quantity in lines:
                both = self._db.execute(
                    "SELECT alt_group FROM recipe_lines"
                    " WHERE recipe_id = %s AND ingredient_id = %s",
                    (recipe_id, keep_id),
                ).fetchone()
                if both:
                    (dropped_group,) = self._db.execute(
                        "SELECT alt_group FROM recipe_lines"
                        " WHERE recipe_id = %s AND ingredient_id = %s",
                        (recipe_id, drop_id),
                    ).fetchone()
                    if both[0] is not None and both[0] == dropped_group:
                        quantity = 0  # two alternatives that are now one: counted once
                    self._db.execute(
                        "UPDATE recipe_lines SET quantity = quantity + %s,"
                        " alt_group = CASE WHEN alt_group IS NOT DISTINCT FROM %s"
                        " THEN alt_group ELSE NULL END"
                        " WHERE recipe_id = %s AND ingredient_id = %s",
                        (quantity, dropped_group, recipe_id, keep_id),
                    )
                    self._db.execute(
                        "DELETE FROM recipe_lines WHERE recipe_id = %s AND ingredient_id = %s",
                        (recipe_id, drop_id),
                    )
                else:
                    self._db.execute(
                        "UPDATE recipe_lines SET ingredient_id = %s"
                        " WHERE recipe_id = %s AND ingredient_id = %s",
                        (keep_id, recipe_id, drop_id),
                    )
            self._db.execute(
                "UPDATE stock_items SET ingredient_id = %s WHERE ingredient_id = %s",
                (keep_id, drop_id),
            )
            for table, column in (
                ("ingredient_food_groups", "food_group"),
                ("ingredient_allergens", "allergen"),
            ):
                self._db.execute(
                    f"INSERT INTO {table} (ingredient_id, {column})"
                    f" SELECT %s, {column} FROM {table} WHERE ingredient_id = %s"
                    " ON CONFLICT DO NOTHING",
                    (keep_id, drop_id),
                )
            self._db.execute(
                "UPDATE ingredients SET"
                " kcal = COALESCE(kcal, (SELECT kcal FROM ingredients WHERE id = %s)),"
                " reviewed = reviewed OR (SELECT reviewed FROM ingredients WHERE id = %s),"
                " shelf_life_amount = COALESCE(shelf_life_amount,"
                "   (SELECT shelf_life_amount FROM ingredients WHERE id = %s)),"
                " shelf_life_unit = COALESCE(shelf_life_unit,"
                "   (SELECT shelf_life_unit FROM ingredients WHERE id = %s))"
                " WHERE id = %s",
                (drop_id, drop_id, drop_id, drop_id, keep_id),
            )
            self._db.execute("DELETE FROM ingredients WHERE id = %s", (drop_id,))

    def _split_stored_alternatives(self) -> None:
        """Split Ingredients stored under a name that offers a choice, as older versions saved
        them. A choice belongs to a Recipe line only: Ingredients are always single foods."""
        offers = self._db.execute(
            "SELECT 1 FROM ingredients WHERE name LIKE '% or %' OR name LIKE '%and/or%' LIMIT 1"
        ).fetchone()
        for _ in range(3):
            if not offers or not self.split_ingredients():
                break

    def split_plan(self) -> list[tuple[Ingredient, list[str]]]:
        """Ingredients whose name offers a choice ("water or vegetable broth") and that Recipes
        use, each with the separate ingredients it would become."""
        used = self._ingredient_ids_in_recipes()
        kinds = dict(self._db.execute("SELECT name, base_unit FROM ingredients"))
        plan = []
        for ingredient in self.ingredients():
            if ingredient.id not in used or len(split_alternatives(ingredient.name)) < 2:
                continue
            names = self._part_names(ingredient, kinds)
            if len(names) >= 2:
                plan.append((ingredient, names))
        return plan

    def _ingredient_ids_in_recipes(self) -> set[int]:
        return {
            row[0] for row in self._db.execute("SELECT DISTINCT ingredient_id FROM recipe_lines")
        }

    def _part_names(self, ingredient: Ingredient, kinds: dict[str, str]) -> list[str]:
        """The names the alternatives of an Ingredient would be stored under."""
        others = {n: k for n, k in kinds.items() if n.lower() != ingredient.name.lower()}
        family = ingredient.base_unit.value
        names: list[str] = []
        for part in split_alternatives(ingredient.name):
            chosen = canonical_name(part, others, family).lower()
            if others.get(chosen, family) != family:
                # The name is taken by an Ingredient in another kind of unit, and a quantity in this
                # kind cannot be converted without a density, so this one is kept apart.
                chosen += _UNIT_SUFFIX[family]
            others.setdefault(chosen, family)
            if chosen not in names:
                names.append(chosen)
        return names

    def split_ingredients(self) -> int:
        """Replace each Ingredient that offers a choice by the separate Ingredients it names.

        Every Recipe line that used it becomes one line per alternative, all with the same
        quantity and marked as alternatives of one another. The new Ingredients are unreviewed
        unless they already existed and a person reviewed them: what was ticked on the combined
        name is not copied, because it described something that is not one food. Returns how many
        Ingredients were split.
        """
        done = 0
        for ingredient, _ in self.split_plan():
            # Named afresh: an earlier split in this pass may have made Ingredients this one meets.
            names = self._part_names(
                ingredient, dict(self._db.execute("SELECT name, base_unit FROM ingredients"))
            )
            if len(names) < 2:
                continue
            family = ingredient.base_unit.value
            taken = {
                name.lower(): kind
                for name, kind in self._db.execute("SELECT name, base_unit FROM ingredients")
            }
            if any(taken.get(n, family) != family for n in names):
                continue  # a name is used by an Ingredient in another kind of unit: leave it
            with self._db.transaction():
                ids = []
                for name in names:
                    row = self._db.execute(
                        "SELECT id FROM ingredients WHERE lower(name) = lower(%s)", (name,)
                    ).fetchone()
                    if row is None:
                        row = self._db.execute(
                            "INSERT INTO ingredients (name, base_unit) VALUES (%s, %s)"
                            " RETURNING id",
                            (name, family),
                        ).fetchone()
                    ids.append(row[0])
                lines = self._db.execute(
                    "SELECT recipe_id, quantity, position FROM recipe_lines"
                    " WHERE ingredient_id = %s",
                    (ingredient.id,),
                ).fetchall()
                for recipe_id, quantity, position in lines:
                    self._db.execute(
                        "DELETE FROM recipe_lines WHERE recipe_id = %s AND ingredient_id = %s",
                        (recipe_id, ingredient.id),
                    )
                    present = {
                        r[0]
                        for r in self._db.execute(
                            "SELECT ingredient_id FROM recipe_lines WHERE recipe_id = %s",
                            (recipe_id,),
                        )
                    }
                    new = [i for i in ids if i not in present]
                    group = None
                    if len(new) > 1:
                        (top,) = self._db.execute(
                            "SELECT COALESCE(MAX(alt_group), 0) FROM recipe_lines"
                            " WHERE recipe_id = %s",
                            (recipe_id,),
                        ).fetchone()
                        group = top + 1
                    for part_id in new:
                        self._db.execute(
                            "INSERT INTO recipe_lines"
                            " (recipe_id, ingredient_id, quantity, position, alt_group)"
                            " VALUES (%s, %s, %s, %s, %s)",
                            (recipe_id, part_id, quantity, position, group),
                        )
                held = self._db.execute(
                    "SELECT 1 FROM stock_items WHERE ingredient_id = %s", (ingredient.id,)
                ).fetchone()
                if not held:
                    self._db.execute("DELETE FROM ingredients WHERE id = %s", (ingredient.id,))
            done += 1
        return done

    def check_rename(self, ingredient_id: int, new_name: str) -> tuple[str, int | None]:
        """Check that an Ingredient may be given this name, without changing anything.

        Returns the name as it would be stored and the id of another Ingredient that already has
        it, if there is one. Raises ValueError for a blank or very long name or one that offers a
        choice, and IncompatibleUnitError if that other Ingredient is stored in a different kind
        of unit, since the two cannot be merged without a density.
        """
        ingredient = self.get_ingredient(ingredient_id)
        if ingredient is None:
            raise IngredientNotFoundError(ingredient_id)
        name = " ".join(new_name.split()).lower()
        if not name:
            raise ValueError("Enter a name for the ingredient")
        if len(name) > MAX_INGREDIENT_NAME:
            raise ValueError(f"An ingredient name can be at most {MAX_INGREDIENT_NAME} characters")
        if len(split_alternatives(name)) > 1:
            raise ValueError(
                f"{name} offers a choice, which is more than one ingredient. Name one food: a"
                " choice between ingredients belongs in a recipe."
            )
        other = self._db.execute(
            "SELECT id, base_unit FROM ingredients WHERE lower(name) = lower(%s) AND id != %s",
            (name, ingredient_id),
        ).fetchone()
        if other is None:
            return name, None
        if BaseUnit(other[1]) is not ingredient.base_unit:
            raise IncompatibleUnitError(
                f"{name} is already an ingredient stored in a different kind of unit"
            )
        return name, other[0]

    def rename_ingredient(self, ingredient_id: int, new_name: str) -> int:
        """Give an Ingredient a new name and return its id.

        If another Ingredient in the same kind of unit already has the name, the two are merged
        into that one (see `merge_ingredients`) and its id is returned.
        """
        name, other = self.check_rename(ingredient_id, new_name)
        if other is not None:
            self.merge_ingredients(other, ingredient_id)
            return other
        with self._db.transaction():
            self._db.execute(
                "UPDATE ingredients SET name = %s WHERE id = %s", (name, ingredient_id)
            )
        return ingredient_id

    def tidy_group(self, member_ids: list[int], target: str) -> None:
        """Merge Ingredients that are one food into the oldest of them and name it `target`.

        Everything is checked first, so nothing changes if the name cannot be used.
        """
        keep = min(member_ids)
        name, other = self.check_rename(keep, target)
        if other is not None and other not in member_ids:
            keep = other  # the name belongs to an Ingredient outside the group: merge into it
        for member in member_ids:
            if member != keep:
                self.merge_ingredients(keep, member)
        if keep == min(member_ids):
            self.rename_ingredient(keep, name)

    def tidy_plan(self) -> list[tuple[list[Ingredient], str]]:
        """What tidying would do: groups of Ingredients that are one food, each with the plain
        name it would end up under.

        A group of one is only there if its name would be cleaned. A name is left alone if the
        clean name is already used by an Ingredient that is stored in another kind of unit.
        """
        everything = self.ingredients()
        groups: dict[tuple[str, str], list[Ingredient]] = {}
        for ingredient in everything:
            key = (name_key(ingredient.name), ingredient.base_unit.value)
            groups.setdefault(key, []).append(ingredient)
        plan = []
        claimed: set[str] = set()  # names a group in this plan has already been given
        for members in groups.values():
            members.sort(key=lambda i: (len(i.name), i.id))
            target = clean_name(members[0].name)
            in_group = {m.id for m in members}
            taken = any(i.name.lower() == target and i.id not in in_group for i in everything)
            if taken or target in claimed:
                target = members[0].name
            claimed.add(target.lower())
            if len(members) == 1 and members[0].name == target:
                continue
            plan.append((members, target))
        return plan

    def tidy(self) -> int:
        """Split, merge and rename Ingredients until nothing is left to do (at most three passes,
        since splitting makes new names that may need tidying too). Returns how many changes."""
        total = 0
        for _ in range(3):
            changed = self._tidy_once()
            total += changed
            if not changed:
                break
        return total

    def _tidy_once(self) -> int:
        """Merge Ingredients that are one food and give each its plain name.

        Returns how many groups were changed. Allergens and food groups ticked on any of the
        merged Ingredients are kept (see `merge_ingredients`).
        """
        split = self.split_ingredients()
        plan = self.tidy_plan()
        for members, target in plan:
            keep = next((m for m in members if m.name == target), min(members, key=lambda i: i.id))
            for other in members:
                if other.id != keep.id:
                    self.merge_ingredients(keep.id, other.id)
            if keep.name != target:
                with self._db.transaction():
                    self._db.execute(
                        "UPDATE ingredients SET name = %s WHERE id = %s", (target, keep.id)
                    )
        return split + len(plan)

    def delete(self, item_id: int) -> None:
        with self._db.transaction():
            cursor = self._db.execute("DELETE FROM stock_items WHERE id = %s", (item_id,))
            if cursor.rowcount == 0:
                raise ItemNotFoundError(item_id)

    def ingredients(self) -> list[Ingredient]:
        """Every Ingredient ever added, by name, with what is known about each."""
        groups = self._tags("ingredient_food_groups", "food_group")
        allergens = self._tags("ingredient_allergens", "allergen")
        rows = self._db.execute(
            "SELECT id, name, base_unit, shelf_life_amount, shelf_life_unit, last_quantity,"
            " last_unit, kcal, reviewed FROM ingredients ORDER BY lower(name)"
        )
        return [
            Ingredient(
                name,
                BaseUnit(base_unit),
                ShelfLife(amount, ShelfLifeUnit(life_unit)) if amount else None,
                LastEntry(last_quantity, last_unit) if last_unit else None,
                id=ingredient_id,
                kcal=kcal,
                food_groups=parse_food_groups(groups.get(ingredient_id, ())),
                allergens=parse_allergens(allergens.get(ingredient_id, ())),
                reviewed=bool(reviewed),
            )
            for (
                ingredient_id,
                name,
                base_unit,
                amount,
                life_unit,
                last_quantity,
                last_unit,
                kcal,
                reviewed,
            ) in rows
        ]

    def _tags(self, table: str, column: str) -> dict[int, list[str]]:
        found: dict[int, list[str]] = {}
        for ingredient_id, value in self._db.execute(
            f"SELECT ingredient_id, {column} FROM {table}"
        ):
            found.setdefault(ingredient_id, []).append(value)
        return found

    def get_ingredient(self, ingredient_id: int) -> Ingredient | None:
        return next((i for i in self.ingredients() if i.id == ingredient_id), None)

    def set_calories(self, ingredient_id: int, kcal: float | None) -> None:
        """Set only the calories of an Ingredient (blank clears them).

        Unlike `set_ingredient_details` this does not touch the food groups or allergens and does
        not mark the Ingredient as reviewed: calories say nothing about what is safe to eat.
        """
        ingredient = self.get_ingredient(ingredient_id)
        if ingredient is None:
            raise IngredientNotFoundError(ingredient_id)
        kcal = check_kcal(kcal, ingredient.base_unit)
        with self._db.transaction():
            self._db.execute(
                "UPDATE ingredients SET kcal = %s WHERE id = %s", (kcal, ingredient_id)
            )

    def set_ingredient_details(
        self,
        ingredient_id: int,
        kcal: float | None,
        food_groups: Iterable[FoodGroup] = (),
        allergens: Iterable[Allergen] = (),
    ) -> None:
        """Replace an Ingredient's calories, food groups and allergens.

        Calories are per 100 g, per 100 ml or per piece, depending on how the Ingredient is
        stored. Nothing is changed if any value is rejected.
        """
        ingredient = self.get_ingredient(ingredient_id)
        if ingredient is None:
            raise IngredientNotFoundError(ingredient_id)
        kcal = check_kcal(kcal, ingredient.base_unit)
        groups = parse_food_groups(FoodGroup(g).value for g in food_groups)
        found_allergens = parse_allergens(Allergen(a).value for a in allergens)
        with self._db.transaction():
            self._db.execute(
                "UPDATE ingredients SET kcal = %s, reviewed = true WHERE id = %s",
                (kcal, ingredient_id),
            )
            self._db.execute(
                "DELETE FROM ingredient_food_groups WHERE ingredient_id = %s", (ingredient_id,)
            )
            self._db.execute(
                "DELETE FROM ingredient_allergens WHERE ingredient_id = %s", (ingredient_id,)
            )
            for group in groups:
                self._db.execute(
                    "INSERT INTO ingredient_food_groups (ingredient_id, food_group)"
                    " VALUES (%s, %s)",
                    (ingredient_id, group.value),
                )
            for allergen in found_allergens:
                self._db.execute(
                    "INSERT INTO ingredient_allergens (ingredient_id, allergen) VALUES (%s, %s)",
                    (ingredient_id, allergen.value),
                )

    def cached_product(self, barcode: str) -> ProductInfo | None:
        """A product found by an earlier barcode lookup."""
        row = self._db.execute(
            "SELECT name, quantity, unit, kcal_100g FROM product_cache WHERE barcode = %s",
            (barcode,),
        ).fetchone()
        return ProductInfo(barcode, *row) if row else None

    def cache_product(self, info: ProductInfo) -> None:
        with self._db.transaction():
            self._db.execute(
                "INSERT INTO product_cache (barcode, name, quantity, unit, kcal_100g)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (barcode) DO UPDATE SET name = excluded.name,"
                " quantity = excluded.quantity, unit = excluded.unit,"
                " kcal_100g = excluded.kcal_100g",
                (info.barcode, info.name, info.quantity, info.unit, info.kcal_per_100),
            )

    def export_data(self) -> dict:
        """Everything the user entered, as plain data for a readable JSON export."""
        return {
            "household": {"country": self.household_country()},
            "ingredients": [
                {
                    "name": i.name,
                    "base_unit": i.base_unit.value,
                    "shelf_life": i.shelf_life
                    and {"amount": i.shelf_life.amount, "unit": i.shelf_life.unit.value},
                    "last_entry": i.last_entry
                    and {"quantity": i.last_entry.quantity, "unit": i.last_entry.unit},
                    "kcal": i.kcal,
                    "kcal_basis": kcal_basis(i.base_unit),
                    "food_groups": [g.value for g in i.food_groups],
                    "allergens": [a.value for a in i.allergens],
                    "reviewed": i.reviewed,
                }
                for i in self.ingredients()
            ],
            "stock_items": [
                {
                    "ingredient": item.ingredient,
                    "quantity": item.quantity,
                    "base_unit": item.base_unit.value,
                    "location": item.location.value,
                    "expires_on": item.expires_on.isoformat() if item.expires_on else None,
                }
                for item in self.list_items()
            ],
        }

    def disclaimer_acceptance(self) -> tuple[str, date] | None:
        """The disclaimer version that was accepted and the date, or None if never accepted."""
        row = self._db.execute("SELECT value FROM settings WHERE key = 'disclaimer'").fetchone()
        if not row:
            return None
        version, _, accepted_on = row[0].partition("|")
        return version, date.fromisoformat(accepted_on)

    def accept_disclaimer(self, version: str, accepted_on: date) -> None:
        with self._db.transaction():
            self._db.execute(
                "INSERT INTO settings (key, value) VALUES ('disclaimer', %s)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"{version}|{accepted_on.isoformat()}",),
            )

    def look_choice(self) -> LookChoice:
        """How the pages are dressed: follow the date (the default), or a pinned season, and
        whether festive colours show at Halloween and Christmas."""
        rows = dict(
            self._db.execute(
                "SELECT key, value FROM settings WHERE key IN ('look_season', 'look_festive')"
            )
        )
        return LookChoice(rows.get("look_season", AUTO), rows.get("look_festive", "on") != "off")

    def set_look_choice(self, choice: LookChoice) -> None:
        with self._db.transaction():
            self._db.executemany(
                "INSERT INTO settings (key, value) VALUES (%s, %s)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [
                    ("look_season", choice.season),
                    ("look_festive", "on" if choice.festive else "off"),
                ],
            )

    def household_country(self) -> str | None:
        """The ISO country code the Household lives in, or None if not set yet."""
        row = self._db.execute("SELECT value FROM settings WHERE key = 'country'").fetchone()
        return row[0] if row else None

    def set_household_country(self, code: str) -> None:
        """Save the Household country by ISO code. A blank code clears it."""
        code = code.strip().upper()
        if code and code not in COUNTRIES:
            raise ValueError("Choose a country from the list")
        with self._db.transaction():
            if code:
                self._db.execute(
                    "INSERT INTO settings (key, value) VALUES ('country', %s)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (code,),
                )
            else:
                self._db.execute("DELETE FROM settings WHERE key = 'country'")

    def _load(self) -> list[StockItem]:
        rows = self._db.execute(
            """SELECT s.id, i.name, s.quantity, i.base_unit, s.location, s.expires_on
               FROM stock_items s JOIN ingredients i ON i.id = s.ingredient_id
               ORDER BY s.id"""
        )
        return [
            StockItem(
                ingredient=name,
                quantity=quantity,
                base_unit=BaseUnit(base_unit),
                location=Location(location),
                expires_on=expires_on,
                id=item_id,
            )
            for item_id, name, quantity, base_unit, location, expires_on in rows
        ]

    def _apply(self, rule: Callable[[list[StockItem]], list[StockItem]]) -> None:
        """Run a pure stock rule and persist the difference, atomically."""
        with self._db.transaction():
            old = self._load()
            new = rule(old)
            kept = {item.id for item in new if item.id is not None}
            for item in old:
                if item.id not in kept:
                    self._db.execute("DELETE FROM stock_items WHERE id = %s", (item.id,))
            before = {item.id: item for item in old}
            for item in new:
                if item.id is None:
                    self._insert(item)
                elif item != before[item.id]:
                    self._db.execute(
                        "UPDATE stock_items SET quantity = %s WHERE id = %s",
                        (item.quantity, item.id),
                    )

    def _insert(self, item: StockItem) -> None:
        self._db.execute(
            "INSERT INTO ingredients (name, base_unit) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (item.ingredient, item.base_unit.value),
        )
        (ingredient_id,) = self._db.execute(
            "SELECT id FROM ingredients WHERE lower(name) = lower(%s)", (item.ingredient,)
        ).fetchone()
        self._db.execute(
            "INSERT INTO stock_items (ingredient_id, quantity, location, expires_on)"
            " VALUES (%s, %s, %s, %s)",
            (
                ingredient_id,
                item.quantity,
                item.location.value,
                item.expires_on,
            ),
        )
