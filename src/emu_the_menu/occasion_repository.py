"""Occasions, in the same database as the rest."""

from collections.abc import Iterable
from datetime import date, timedelta

from emu_the_menu.db import Database, attach
from emu_the_menu.holidays import Holiday
from emu_the_menu.occasions import Occasion, Source, combine


class OccasionNotFoundError(LookupError):
    pass


class OccasionRepository:
    def __init__(self, database: str | Database | None = None):
        self._db, self._owned = attach(database)

    def __enter__(self) -> "OccasionRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._owned:
            self._db.close()

    def _write(self, occasion_id, name, day, headcount, extra, tags, source) -> int:
        with self._db.transaction():
            if occasion_id is None:
                (occasion_id,) = self._db.execute(
                    "INSERT INTO occasions (name, day, headcount, extra_percent, source)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (name, day, headcount, extra, source.value),
                ).fetchone()
            else:
                self._db.execute(
                    "UPDATE occasions SET name = %s, day = %s, headcount = %s, extra_percent = %s"
                    " WHERE id = %s",
                    (name, day, headcount, extra, occasion_id),
                )
            self._db.execute("DELETE FROM occasion_tags WHERE occasion_id = %s", (occasion_id,))
            self._db.executemany(
                "INSERT INTO occasion_tags (occasion_id, tag, position) VALUES (%s, %s, %s)",
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
        with self._db.transaction():
            self._db.execute("UPDATE occasions SET source = 'manual' WHERE id = %s", (occasion_id,))
        self._write(occasion_id, name, day, headcount, extra_percent, tuple(tags), Source.MANUAL)

    def delete(self, occasion_id: int) -> None:
        with self._db.transaction():
            cursor = self._db.execute("DELETE FROM occasions WHERE id = %s", (occasion_id,))
        if cursor.rowcount == 0:
            raise OccasionNotFoundError(occasion_id)

    def _read(self, rows) -> list[Occasion]:
        occasions = []
        for oid, name, day, headcount, extra, source in rows:
            tags = tuple(
                t
                for (t,) in self._db.execute(
                    "SELECT tag FROM occasion_tags WHERE occasion_id = %s ORDER BY position", (oid,)
                )
            )
            occasions.append(Occasion(name, day, headcount, extra, tags, Source(source), oid))
        return occasions

    _COLUMNS = "SELECT id, name, day, headcount, extra_percent, source FROM occasions"

    def get(self, occasion_id: int) -> Occasion | None:
        found = self._read(self._db.execute(self._COLUMNS + " WHERE id = %s", (occasion_id,)))
        return found[0] if found else None

    def from_day(self, start: date) -> list[Occasion]:
        """Occasions on or after `start`, in date order."""
        return self._read(
            self._db.execute(self._COLUMNS + " WHERE day >= %s ORDER BY day, name", (start,))
        )

    def all(self) -> list[Occasion]:
        return self._read(self._db.execute(self._COLUMNS + " ORDER BY day, name"))

    def for_days(self, start: date, days: int) -> dict[date, Occasion]:
        """The Occasion standing for each day from `start` that has any (see `combine`)."""
        end = start + timedelta(days=days - 1)
        rows = self._read(
            self._db.execute(
                self._COLUMNS + " WHERE day BETWEEN %s AND %s ORDER BY day, id",
                (start, end),
            )
        )
        by_day: dict[date, list[Occasion]] = {}
        for occasion in rows:
            by_day.setdefault(occasion.day, []).append(occasion)
        return {day: combine(group) for day, group in by_day.items()}

    def add_holidays(self, holidays: Iterable[Holiday]) -> int:
        """Add imported holidays not already there. Returns how many were new."""
        added = 0
        with self._db.transaction():
            for holiday in holidays:
                cursor = self._db.execute(
                    "INSERT INTO occasions (name, day, source) VALUES (%s, %s, 'holiday')"
                    " ON CONFLICT DO NOTHING",
                    (holiday.name, holiday.day),
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


def occasions_for(database: str | Database | None, start: date, days: int) -> dict[date, Occasion]:
    """The Occasion standing for each day of a plan that has any."""
    with OccasionRepository(database) as repo:
        return repo.for_days(start, days)
