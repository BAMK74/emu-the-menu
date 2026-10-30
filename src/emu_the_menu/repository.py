"""SQLite storage for Ingredients and Stock Items.

The stock rules stay in `stock.py` as pure functions. The repository loads the
items, applies a rule and writes back only what changed, all in one transaction.
"""

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from emu_the_menu.allergens import Allergen, parse_allergens
from emu_the_menu.countries import COUNTRIES
from emu_the_menu.food_groups import FoodGroup, parse_food_groups
from emu_the_menu.ingredients import check_kcal, kcal_basis
from emu_the_menu.names import canonical_name, clean_name, name_key
from emu_the_menu.openfoodfacts import ProductInfo
from emu_the_menu.shelf_life import ShelfLife, ShelfLifeUnit
from emu_the_menu.stock import Location, StockItem, add_stock, reduce_stock, sort_by_expiry
from emu_the_menu.units import BaseUnit, IncompatibleUnitError, base_unit_for, to_base

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredients (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    base_unit TEXT NOT NULL,
    shelf_life_amount INTEGER,
    shelf_life_unit   TEXT,
    last_quantity     REAL,
    last_unit         TEXT,
    kcal              REAL,
    reviewed          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ingredient_food_groups (
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
    food_group    TEXT NOT NULL,
    PRIMARY KEY (ingredient_id, food_group)
);
CREATE TABLE IF NOT EXISTS ingredient_allergens (
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
    allergen      TEXT NOT NULL,
    PRIMARY KEY (ingredient_id, allergen)
);
CREATE TABLE IF NOT EXISTS stock_items (
    id            INTEGER PRIMARY KEY,
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
    quantity      REAL NOT NULL CHECK (quantity >= 0),
    location      TEXT NOT NULL,
    expires_on    TEXT
);
CREATE TABLE IF NOT EXISTS product_cache (
    barcode  TEXT PRIMARY KEY,
    name     TEXT,
    quantity REAL,
    unit     TEXT,
    kcal_100g REAL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added to `ingredients` after the first release, for upgrading older databases.
_ADDED_COLUMNS = {
    "shelf_life_amount": "INTEGER",
    "shelf_life_unit": "TEXT",
    "last_quantity": "REAL",
    "last_unit": "TEXT",
    "kcal": "REAL",
    "reviewed": "INTEGER NOT NULL DEFAULT 0",
}


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


class StockRepository:
    def __init__(self, path: str | Path):
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        self._upgrade()

    def _upgrade(self) -> None:
        """Add columns that databases created by older versions lack."""
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(ingredients)")}
        with self._db:
            for name, sql_type in _ADDED_COLUMNS.items():
                if name not in columns:
                    self._db.execute(f"ALTER TABLE ingredients ADD COLUMN {name} {sql_type}")
            cached = {row[1] for row in self._db.execute("PRAGMA table_info(product_cache)")}
            if "kcal_100g" not in cached:
                self._db.execute("ALTER TABLE product_cache ADD COLUMN kcal_100g REAL")

    def __enter__(self) -> "StockRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
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
        ingredient = self._canonical(ingredient, unit)
        self._apply(
            lambda items: add_stock(items, ingredient, quantity, unit, location, expires_on)
        )
        with self._db:
            name = ingredient.strip().lower()
            self._db.execute(
                "UPDATE ingredients SET last_quantity = ?, last_unit = ? WHERE name = ?",
                (quantity, unit.strip().lower(), name),
            )
            if shelf_life:
                self._db.execute(
                    "UPDATE ingredients SET shelf_life_amount = ?, shelf_life_unit = ?"
                    " WHERE name = ?",
                    (shelf_life.amount, shelf_life.unit.value, name),
                )

    def use(self, ingredient: str, quantity: float, unit: str) -> None:
        """Deduct Stock, earliest expiry first. Changes nothing if there is not enough."""
        ingredient = self._canonical(ingredient, unit)
        self._apply(lambda items: reduce_stock(items, ingredient, quantity, unit))

    def set_quantity(self, item_id: int, quantity: float, unit: str) -> None:
        amount = to_base(quantity, unit)
        with self._db:
            row = self._db.execute(
                """SELECT i.base_unit FROM stock_items s
                   JOIN ingredients i ON i.id = s.ingredient_id WHERE s.id = ?""",
                (item_id,),
            ).fetchone()
            if row is None:
                raise ItemNotFoundError(item_id)
            if base_unit_for(unit) != BaseUnit(row[0]):
                raise IncompatibleUnitError(f"{unit!r} does not match how this item is stored")
            self._db.execute("UPDATE stock_items SET quantity = ? WHERE id = ?", (amount, item_id))

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
        with self._db:
            row = self._db.execute(
                """SELECT i.base_unit FROM stock_items s
                   JOIN ingredients i ON i.id = s.ingredient_id WHERE s.id = ?""",
                (item_id,),
            ).fetchone()
            if row is None:
                raise ItemNotFoundError(item_id)
            if base_unit_for(unit) != BaseUnit(row[0]):
                raise IncompatibleUnitError(f"{unit!r} does not match how this item is stored")
            self._db.execute(
                "UPDATE stock_items SET quantity = ?, location = ?, expires_on = ? WHERE id = ?",
                (amount, location.value, expires_on.isoformat() if expires_on else None, item_id),
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
        with self._db:
            self._db.execute(
                "UPDATE ingredients SET kcal = CASE WHEN base_unit = ? THEN kcal END,"
                " base_unit = ?, last_quantity = NULL, last_unit = NULL WHERE name = ?",
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
        with self._db:
            has_recipes = self._db.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'recipe_lines'"
            ).fetchone()
            if has_recipes:
                lines = self._db.execute(
                    "SELECT recipe_id, quantity FROM recipe_lines WHERE ingredient_id = ?",
                    (drop_id,),
                ).fetchall()
                for recipe_id, quantity in lines:
                    both = self._db.execute(
                        "SELECT 1 FROM recipe_lines WHERE recipe_id = ? AND ingredient_id = ?",
                        (recipe_id, keep_id),
                    ).fetchone()
                    if both:
                        self._db.execute(
                            "UPDATE recipe_lines SET quantity = quantity + ?"
                            " WHERE recipe_id = ? AND ingredient_id = ?",
                            (quantity, recipe_id, keep_id),
                        )
                        self._db.execute(
                            "DELETE FROM recipe_lines WHERE recipe_id = ? AND ingredient_id = ?",
                            (recipe_id, drop_id),
                        )
                    else:
                        self._db.execute(
                            "UPDATE recipe_lines SET ingredient_id = ?"
                            " WHERE recipe_id = ? AND ingredient_id = ?",
                            (keep_id, recipe_id, drop_id),
                        )
            self._db.execute(
                "UPDATE stock_items SET ingredient_id = ? WHERE ingredient_id = ?",
                (keep_id, drop_id),
            )
            for table, column in (
                ("ingredient_food_groups", "food_group"),
                ("ingredient_allergens", "allergen"),
            ):
                self._db.execute(
                    f"INSERT OR IGNORE INTO {table} (ingredient_id, {column})"
                    f" SELECT ?, {column} FROM {table} WHERE ingredient_id = ?",
                    (keep_id, drop_id),
                )
            self._db.execute(
                "UPDATE ingredients SET"
                " kcal = COALESCE(kcal, (SELECT kcal FROM ingredients WHERE id = ?)),"
                " reviewed = MAX(reviewed, (SELECT reviewed FROM ingredients WHERE id = ?)),"
                " shelf_life_amount = COALESCE(shelf_life_amount,"
                "   (SELECT shelf_life_amount FROM ingredients WHERE id = ?)),"
                " shelf_life_unit = COALESCE(shelf_life_unit,"
                "   (SELECT shelf_life_unit FROM ingredients WHERE id = ?))"
                " WHERE id = ?",
                (drop_id, drop_id, drop_id, drop_id, keep_id),
            )
            self._db.execute("DELETE FROM ingredients WHERE id = ?", (drop_id,))

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
        """Merge Ingredients that are one food and give each its plain name.

        Returns how many groups were changed. Allergens and food groups ticked on any of the
        merged Ingredients are kept (see `merge_ingredients`).
        """
        plan = self.tidy_plan()
        for members, target in plan:
            keep = next((m for m in members if m.name == target), min(members, key=lambda i: i.id))
            for other in members:
                if other.id != keep.id:
                    self.merge_ingredients(keep.id, other.id)
            if keep.name != target:
                with self._db:
                    self._db.execute(
                        "UPDATE ingredients SET name = ? WHERE id = ?", (target, keep.id)
                    )
        return len(plan)

    def delete(self, item_id: int) -> None:
        with self._db:
            cursor = self._db.execute("DELETE FROM stock_items WHERE id = ?", (item_id,))
            if cursor.rowcount == 0:
                raise ItemNotFoundError(item_id)

    def ingredients(self) -> list[Ingredient]:
        """Every Ingredient ever added, by name, with what is known about each."""
        groups = self._tags("ingredient_food_groups", "food_group")
        allergens = self._tags("ingredient_allergens", "allergen")
        rows = self._db.execute(
            "SELECT id, name, base_unit, shelf_life_amount, shelf_life_unit, last_quantity,"
            " last_unit, kcal, reviewed FROM ingredients ORDER BY name"
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
        with self._db:
            self._db.execute(
                "UPDATE ingredients SET kcal = ?, reviewed = 1 WHERE id = ?", (kcal, ingredient_id)
            )
            self._db.execute(
                "DELETE FROM ingredient_food_groups WHERE ingredient_id = ?", (ingredient_id,)
            )
            self._db.execute(
                "DELETE FROM ingredient_allergens WHERE ingredient_id = ?", (ingredient_id,)
            )
            for group in groups:
                self._db.execute(
                    "INSERT INTO ingredient_food_groups (ingredient_id, food_group) VALUES (?, ?)",
                    (ingredient_id, group.value),
                )
            for allergen in found_allergens:
                self._db.execute(
                    "INSERT INTO ingredient_allergens (ingredient_id, allergen) VALUES (?, ?)",
                    (ingredient_id, allergen.value),
                )

    def cached_product(self, barcode: str) -> ProductInfo | None:
        """A product found by an earlier barcode lookup."""
        row = self._db.execute(
            "SELECT name, quantity, unit, kcal_100g FROM product_cache WHERE barcode = ?",
            (barcode,),
        ).fetchone()
        return ProductInfo(barcode, *row) if row else None

    def cache_product(self, info: ProductInfo) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO product_cache (barcode, name, quantity, unit, kcal_100g)"
                " VALUES (?, ?, ?, ?, ?)",
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
        with self._db:
            self._db.execute(
                "INSERT INTO settings (key, value) VALUES ('disclaimer', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"{version}|{accepted_on.isoformat()}",),
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
        with self._db:
            if code:
                self._db.execute(
                    "INSERT INTO settings (key, value) VALUES ('country', ?)"
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
                expires_on=date.fromisoformat(expires_on) if expires_on else None,
                id=item_id,
            )
            for item_id, name, quantity, base_unit, location, expires_on in rows
        ]

    def _apply(self, rule: Callable[[list[StockItem]], list[StockItem]]) -> None:
        """Run a pure stock rule and persist the difference, atomically."""
        with self._db:
            old = self._load()
            new = rule(old)
            kept = {item.id for item in new if item.id is not None}
            for item in old:
                if item.id not in kept:
                    self._db.execute("DELETE FROM stock_items WHERE id = ?", (item.id,))
            before = {item.id: item for item in old}
            for item in new:
                if item.id is None:
                    self._insert(item)
                elif item != before[item.id]:
                    self._db.execute(
                        "UPDATE stock_items SET quantity = ? WHERE id = ?", (item.quantity, item.id)
                    )

    def _insert(self, item: StockItem) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO ingredients (name, base_unit) VALUES (?, ?)",
            (item.ingredient, item.base_unit.value),
        )
        (ingredient_id,) = self._db.execute(
            "SELECT id FROM ingredients WHERE name = ?", (item.ingredient,)
        ).fetchone()
        self._db.execute(
            "INSERT INTO stock_items (ingredient_id, quantity, location, expires_on)"
            " VALUES (?, ?, ?, ?)",
            (
                ingredient_id,
                item.quantity,
                item.location.value,
                item.expires_on.isoformat() if item.expires_on else None,
            ),
        )
