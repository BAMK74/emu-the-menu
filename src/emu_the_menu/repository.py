"""SQLite storage for Ingredients and Stock Items.

The stock rules stay in `stock.py` as pure functions. The repository loads the
items, applies a rule and writes back only what changed, all in one transaction.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from emu_the_menu.countries import COUNTRIES
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
    last_unit         TEXT
);
CREATE TABLE IF NOT EXISTS stock_items (
    id            INTEGER PRIMARY KEY,
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
    quantity      REAL NOT NULL CHECK (quantity >= 0),
    location      TEXT NOT NULL,
    expires_on    TEXT
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
}


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

    def change_base_unit(self, ingredient: str, unit: str) -> None:
        """Correct the unit an Ingredient is stored in, such as chicken entered in ml by mistake.

        Quantities already in Stock keep their numbers and are read in the new unit,
        because grams cannot be converted from millilitres. The remembered quantity
        is forgotten, as its unit may be in the wrong family. Does nothing if the
        Ingredient is unknown.
        """
        base = base_unit_for(unit)
        with self._db:
            self._db.execute(
                "UPDATE ingredients SET base_unit = ?, last_quantity = NULL, last_unit = NULL"
                " WHERE name = ?",
                (base.value, ingredient.strip().lower()),
            )

    def delete(self, item_id: int) -> None:
        with self._db:
            cursor = self._db.execute("DELETE FROM stock_items WHERE id = ?", (item_id,))
            if cursor.rowcount == 0:
                raise ItemNotFoundError(item_id)

    def ingredients(self) -> list[Ingredient]:
        """Every Ingredient ever added, by name, for suggestions."""
        rows = self._db.execute(
            "SELECT name, base_unit, shelf_life_amount, shelf_life_unit, last_quantity, last_unit"
            " FROM ingredients ORDER BY name"
        )
        return [
            Ingredient(
                name,
                BaseUnit(base_unit),
                ShelfLife(amount, ShelfLifeUnit(life_unit)) if amount else None,
                LastEntry(last_quantity, last_unit) if last_unit else None,
            )
            for name, base_unit, amount, life_unit, last_quantity, last_unit in rows
        ]

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
