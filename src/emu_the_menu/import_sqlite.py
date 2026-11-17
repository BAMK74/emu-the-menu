"""Copy an emu.db from the SQLite days into an empty Postgres database, once.

    python -m emu_the_menu.import_sqlite /tmp/emu.db

The database is the one `EMU_DATABASE_URL` names (or `--database-url`). Its schema is created
first if needed. Everything is copied with its ids, so saved Menus still point at the right
Recipes and Eaters: stock, ingredients, eaters, recipes, menus, occasions, the weather, the
barcode cache and the settings. It all happens in one transaction, so a failure leaves the
database empty, and it refuses to run on a database that already holds any data.

The SQLite file is opened read-only. Columns that an older SQLite file lacks get the value a new
row would get; an absolute calorie goal from before adjustments is turned into an adjustment.
"""

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from emu_the_menu.db import ACCOUNT_TABLES, DATA_TABLES, Database, borrowed
from emu_the_menu.eaters import DEFAULT_CALORIE_GOALS, Category, nearest_adjustment


class ImportRefusedError(RuntimeError):
    """The import cannot go ahead, and nothing was changed."""


@dataclass(frozen=True)
class TableCount:
    table: str
    source: int  # rows in the SQLite file (0 if it has no such table)
    copied: int  # rows in Postgres afterwards


def import_sqlite(source: str | Path, database: str | Database | None = None) -> list[TableCount]:
    """Copy every table of the SQLite file at `source` into the empty database.

    Returns the rows per table, before and after. Raises ImportRefusedError if the file is not an
    emu database or the target already holds data, and nothing is changed.
    """
    path = Path(source)
    if not path.is_file():
        raise ImportRefusedError(f"There is no file at {path}")
    reader = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tables = _source_tables(reader)
        if "ingredients" not in tables:
            raise ImportRefusedError(f"{path} is not an emu database (it has no ingredients)")
        with borrowed(database) as db:
            _refuse_if_not_empty(db)
            with db.transaction():
                counts = [_copy_table(reader, db, table, tables) for table in DATA_TABLES]
                _move_sequences(db)
    finally:
        reader.close()
    return counts


def _source_tables(reader: sqlite3.Connection) -> dict[str, list[str]]:
    names = [
        name
        for (name,) in reader.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        name: [row[1] for row in reader.execute(f'PRAGMA table_info("{name}")')] for name in names
    }


def _refuse_if_not_empty(db: Database) -> None:
    holding = [
        table
        for table in (*DATA_TABLES, *ACCOUNT_TABLES)
        if db.execute(f"SELECT EXISTS (SELECT 1 FROM {table})").fetchone()[0]
    ]
    if holding:
        raise ImportRefusedError(
            "The database already holds data (in " + ", ".join(holding) + "). The import only"
            " runs on an empty database, so it can never mix two households or overwrite one."
        )


def _target_columns(db: Database, table: str) -> dict[str, str]:
    """The columns of a Postgres table and their types, in order."""
    return dict(
        db.execute(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position",
            (table,),
        ).fetchall()
    )


def _copy_table(
    reader: sqlite3.Connection, db: Database, table: str, tables: dict[str, list[str]]
) -> TableCount:
    if table not in tables:
        return TableCount(table, 0, 0)
    have = tables[table]
    target = _target_columns(db, table)
    columns = [c for c in target if c in have]
    selected = list(columns)
    if table == "menu_meal_taken" and "id" not in have:
        # SQLite kept the order taken by rowid; here it is the id.
        columns.insert(0, "id")
        selected.insert(0, "rowid")
    if table == "eaters" and "calorie_adjustment" not in have and "calorie_goal" in have:
        selected.append("calorie_goal")
    rows = reader.execute(f"SELECT {', '.join(selected)} FROM {table} ORDER BY rowid").fetchall()
    if table == "eaters" and len(selected) > len(columns):
        rows, columns = _goals_as_adjustments(rows, columns)
    converted = [
        tuple(_convert(value, target[column]) for value, column in zip(row, columns, strict=True))
        for row in rows
    ]
    db.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
        converted,
    )
    (copied,) = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return TableCount(table, len(rows), copied)


def _goals_as_adjustments(rows, columns):
    """Eaters from before calorie adjustments kept an absolute goal: keep it as an adjustment."""
    category_at = columns.index("category")
    out = []
    for row in rows:
        *values, goal = row
        adjustment = 0
        if goal is not None:
            default = DEFAULT_CALORIE_GOALS[Category(values[category_at])]
            adjustment = nearest_adjustment(goal - default)
        out.append((*values, adjustment))
    return out, [*columns, "calorie_adjustment"]


def _convert(value, data_type: str):
    """A SQLite value as the Postgres column wants it."""
    if value is None:
        return None
    if data_type == "boolean":
        return bool(value)
    if data_type == "date":
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    if data_type == "double precision":
        return float(value)
    if data_type == "integer":
        return int(value)
    return value


def _move_sequences(db: Database) -> None:
    """Point each id sequence past the highest id copied, so new rows do not collide."""
    for (table,) in db.execute(
        "SELECT table_name FROM information_schema.columns"
        " WHERE table_schema = current_schema() AND column_name = 'id'"
        " AND is_identity = 'YES'"
    ).fetchall():
        db.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'),"
            f" COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m emu_the_menu.import_sqlite",
        description="Copy an emu.db from the SQLite days into an empty Postgres database.",
    )
    parser.add_argument("source", help="the SQLite file, such as /tmp/emu.db")
    parser.add_argument(
        "--database-url", help="the Postgres to fill (default: EMU_DATABASE_URL)", default=None
    )
    args = parser.parse_args(argv)
    try:
        counts = import_sqlite(args.source, args.database_url)
    except ImportRefusedError as exc:
        print(f"Not imported: {exc}", file=sys.stderr)
        return 1
    width = max(len(c.table) for c in counts)
    print(f"{'table':<{width}}  {'sqlite':>7}  {'postgres':>8}")
    for c in counts:
        print(f"{c.table:<{width}}  {c.source:>7}  {c.copied:>8}")
    mismatched = [c.table for c in counts if c.source != c.copied]
    if mismatched:
        print("Counts differ for: " + ", ".join(mismatched), file=sys.stderr)
        return 1
    print(f"Imported {sum(c.copied for c in counts)} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
