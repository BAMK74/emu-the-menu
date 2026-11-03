"""Occasions in the same SQLite file as the rest, on a connection of their own."""

import sqlite3
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

from emu_the_menu.holidays import Holiday
from emu_the_menu.occasions import Occasion, Source, combine

_SCHEMA = """
CREATE TABLE IF NOT EXISTS occasions (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    day           TEXT NOT NULL,
    headcount     INTEGER,
    extra_percent INTEGER NOT NULL DEFAULT 0,
    source        TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'holiday'))
);
CREATE INDEX IF NOT EXISTS occasions_day ON occasions (day);
CREATE UNIQUE INDEX IF NOT EXISTS occasions_holiday
    ON occasions (day, name COLLATE NOCASE) WHERE source = 'holiday';
CREATE TABLE IF NOT EXISTS occasion_tags (
    occasion_id INTEGER NOT NULL REFERENCES occasions(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    position    INTEGER NOT NULL,
    PRIMARY KEY (occasion_id, tag)
);
"""


class OccasionNotFoundError(LookupError):
    pass


class OccasionRepository:
    def __init__(self, path: str | Path):
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)

    def __enter__(self) -> "OccasionRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    def _write(self, occasion_id, name, day, headcount, extra, tags, source) -> int:
        with self._db:
            if occasion_id is None:
                occasion_id = self._db.execute(
                    "INSERT INTO occasions (name, day, headcount, extra_percent, source)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (name, day.isoformat(), headcount, extra, source.value),
                ).lastrowid
            else:
                self._db.execute(
                    "UPDATE occasions SET name = ?, day = ?, headcount = ?, extra_percent = ?"
                    " WHERE id = ?",
                    (name, day.isoformat(), headcount, extra, occasion_id),
                )
            self._db.execute("DELETE FROM occasion_tags WHERE occasion_id = ?", (occasion_id,))
            self._db.executemany(
                "INSERT INTO occasion_tags (occasion_id, tag, position) VALUES (?, ?, ?)",
                [(occasion_id, tag, n) for n, tag in enumerate(tags)],
            )
        return occasion_id

    def add(
        self,
        name: str,
        day: date,
        headcount: int | None = None,
        extra_percent: int = 0,
        tags: Iterable[str] = (),
        source: Source = Source.MANUAL,
    ) -> int:
        return self._write(None, name, day, headcount, extra_percent, tuple(tags), source)

    def update(
        self,
        occasion_id: int,
        name: str,
        day: date,
        headcount: int | None,
        extra_percent: int,
        tags: Iterable[str],
    ) -> None:
        """Change an Occasion. An imported holiday that is edited becomes a manual entry, so a
        later import never overwrites it."""
        if self.get(occasion_id) is None:
            raise OccasionNotFoundError(occasion_id)
        with self._db:
            self._db.execute("UPDATE occasions SET source = 'manual' WHERE id = ?", (occasion_id,))
        self._write(occasion_id, name, day, headcount, extra_percent, tuple(tags), Source.MANUAL)

    def delete(self, occasion_id: int) -> None:
        with self._db:
            cursor = self._db.execute("DELETE FROM occasions WHERE id = ?", (occasion_id,))
        if cursor.rowcount == 0:
            raise OccasionNotFoundError(occasion_id)

    def _read(self, rows) -> list[Occasion]:
        occasions = []
        for oid, name, day, headcount, extra, source in rows:
            tags = tuple(
                t
                for (t,) in self._db.execute(
                    "SELECT tag FROM occasion_tags WHERE occasion_id = ? ORDER BY position", (oid,)
                )
            )
            occasions.append(
                Occasion(name, date.fromisoformat(day), headcount, extra, tags, Source(source), oid)
            )
        return occasions

    _COLUMNS = "SELECT id, name, day, headcount, extra_percent, source FROM occasions"

    def get(self, occasion_id: int) -> Occasion | None:
        found = self._read(self._db.execute(self._COLUMNS + " WHERE id = ?", (occasion_id,)))
        return found[0] if found else None

    def from_day(self, start: date) -> list[Occasion]:
        """Occasions on or after `start`, in date order."""
        return self._read(
            self._db.execute(
                self._COLUMNS + " WHERE day >= ? ORDER BY day, name", (start.isoformat(),)
            )
        )

    def all(self) -> list[Occasion]:
        return self._read(self._db.execute(self._COLUMNS + " ORDER BY day, name"))

    def for_days(self, start: date, days: int) -> dict[date, Occasion]:
        """The Occasion standing for each day from `start` that has any (see `combine`)."""
        end = start + timedelta(days=days - 1)
        rows = self._read(
            self._db.execute(
                self._COLUMNS + " WHERE day BETWEEN ? AND ? ORDER BY day, id",
                (start.isoformat(), end.isoformat()),
            )
        )
        by_day: dict[date, list[Occasion]] = {}
        for occasion in rows:
            by_day.setdefault(occasion.day, []).append(occasion)
        return {day: combine(group) for day, group in by_day.items()}

    def add_holidays(self, holidays: Iterable[Holiday]) -> int:
        """Add imported holidays not already there. Returns how many were new."""
        added = 0
        with self._db:
            for holiday in holidays:
                cursor = self._db.execute(
                    "INSERT OR IGNORE INTO occasions (name, day, source) VALUES (?, ?, 'holiday')",
                    (holiday.name, holiday.day.isoformat()),
                )
                added += cursor.rowcount
        return added

    def export_data(self) -> list[dict]:
        return [
            {
                "name": o.name,
                "date": o.day.isoformat(),
                "headcount": o.headcount,
                "extra_calories_percent": o.extra_percent,
                "tags": list(o.tags),
                "source": o.source.value,
            }
            for o in self.all()
        ]
