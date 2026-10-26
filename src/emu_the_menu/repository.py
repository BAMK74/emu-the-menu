"""SQLite storage for Ingredients and Stock Items.

The stock rules stay in `stock.py` as pure functions. The repository loads the
items, applies a rule and writes back only what changed, all in one transaction.
"""

import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path

from emu_the_menu.stock import Location, StockItem, add_stock, reduce_stock, sort_by_expiry
from emu_the_menu.units import BaseUnit, IncompatibleUnitError, base_unit_for, to_base

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredients (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    base_unit TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stock_items (
    id            INTEGER PRIMARY KEY,
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
    quantity      REAL NOT NULL CHECK (quantity >= 0),
    location      TEXT NOT NULL,
    expires_on    TEXT
);
"""


class ItemNotFoundError(LookupError):
    """No Stock Item has the given id."""


class StockRepository:
    def __init__(self, path: str | Path):
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)

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
    ) -> None:
        """Add Stock. The first add of an Ingredient fixes its base unit from `unit`."""
        self._apply(
            lambda items: add_stock(items, ingredient, quantity, unit, location, expires_on)
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

    def delete(self, item_id: int) -> None:
        with self._db:
            cursor = self._db.execute("DELETE FROM stock_items WHERE id = ?", (item_id,))
            if cursor.rowcount == 0:
                raise ItemNotFoundError(item_id)

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
