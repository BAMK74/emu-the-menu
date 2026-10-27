"""Eaters: the people a Household cooks for, with a Category, calorie goal and food restrictions.

An Eater's Category gives a default calorie goal that can be overridden. Allergies are hard
exclusions and dislikes are soft penalties when Menus are built later. Both are stored as plain
lowercase terms such as "peanuts". How those terms match Recipe ingredients is decided when
Recipes exist.
"""

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Category(StrEnum):
    MAN = "man"
    WOMAN = "woman"
    TEEN = "teen"
    CHILD = "child"


# Rough daily energy needs, in kcal. Each Eater can override their own.
DEFAULT_CALORIE_GOALS: dict[Category, int] = {
    Category.MAN: 2500,
    Category.WOMAN: 2000,
    Category.TEEN: 2200,
    Category.CHILD: 1600,
}
MIN_CALORIE_GOAL = 500
MAX_CALORIE_GOAL = 6000
MAX_NAME_LENGTH = 40


class EaterNotFoundError(LookupError):
    """No Eater has the given id."""


@dataclass(frozen=True)
class Eater:
    id: int | None
    name: str
    category: Category
    calorie_goal_override: int | None = None
    allergies: tuple[str, ...] = ()
    dislikes: tuple[str, ...] = ()

    @property
    def default_calorie_goal(self) -> int:
        return DEFAULT_CALORIE_GOALS[self.category]

    @property
    def calorie_goal(self) -> int:
        return self.calorie_goal_override or self.default_calorie_goal

    @property
    def has_custom_goal(self) -> bool:
        return self.calorie_goal_override is not None


def clean_name(name: str) -> str:
    """Trim and collapse spaces. Raises ValueError for a blank or very long name."""
    name = " ".join(name.split())
    if not name:
        raise ValueError("Enter a name")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"A name can be at most {MAX_NAME_LENGTH} characters")
    return name


def _clean_terms(items: Iterable[str]) -> tuple[str, ...]:
    """Lowercase, collapse spaces, drop blanks and repeats, keeping the order typed."""
    seen: dict[str, None] = {}
    for item in items:
        term = " ".join(item.split()).lower()
        if term:
            seen.setdefault(term)
    return tuple(seen)


def parse_terms(text: str) -> tuple[str, ...]:
    """Read a typed list like "Peanuts, shellfish; milk", split on commas, semicolons and lines."""
    return _clean_terms(re.split(r"[,;\n]", text))


def _check_goal(goal: int | None) -> int | None:
    if goal is not None and not MIN_CALORIE_GOAL <= goal <= MAX_CALORIE_GOAL:
        raise ValueError(
            f"The calorie goal should be between {MIN_CALORIE_GOAL} and {MAX_CALORIE_GOAL}"
        )
    return goal


def parse_calorie_goal(text: str) -> int | None:
    """Read a typed calorie goal. Blank means no override, so the Category default is used."""
    text = text.strip()
    if not text:
        return None
    if not re.fullmatch(r"\d+", text):
        raise ValueError("Enter the calorie goal as a whole number, or leave it blank")
    return _check_goal(int(text))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS eaters (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    category     TEXT NOT NULL,
    calorie_goal INTEGER
);
CREATE TABLE IF NOT EXISTS eater_terms (
    eater_id INTEGER NOT NULL REFERENCES eaters(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL CHECK (kind IN ('allergy', 'dislike')),
    term     TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (eater_id, kind, term)
);
"""


class EaterRepository:
    """Eaters in the same SQLite file as the Stock, on a connection of its own."""

    def __init__(self, path: str | Path):
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)

    def __enter__(self) -> "EaterRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    def list(self) -> list[Eater]:
        """Everyone, in the order they were added."""
        rows = self._db.execute("SELECT id, name, category, calorie_goal FROM eaters ORDER BY id")
        return [self._eater(*row) for row in rows.fetchall()]

    def get(self, eater_id: int) -> Eater | None:
        row = self._db.execute(
            "SELECT id, name, category, calorie_goal FROM eaters WHERE id = ?", (eater_id,)
        ).fetchone()
        return self._eater(*row) if row else None

    def add(
        self,
        name: str,
        category: Category,
        calorie_goal: int | None = None,
        allergies: Iterable[str] = (),
        dislikes: Iterable[str] = (),
    ) -> Eater:
        name, goal = clean_name(name), _check_goal(calorie_goal)
        try:
            with self._db:
                cursor = self._db.execute(
                    "INSERT INTO eaters (name, category, calorie_goal) VALUES (?, ?, ?)",
                    (name, Category(category).value, goal),
                )
                self._save_terms(cursor.lastrowid, allergies, dislikes)
        except sqlite3.IntegrityError:
            raise ValueError(f"Someone called {name} is already in the household") from None
        return self.get(cursor.lastrowid)

    def update(
        self,
        eater_id: int,
        name: str,
        category: Category,
        calorie_goal: int | None = None,
        allergies: Iterable[str] = (),
        dislikes: Iterable[str] = (),
    ) -> Eater:
        name, goal = clean_name(name), _check_goal(calorie_goal)
        if self.get(eater_id) is None:
            raise EaterNotFoundError(eater_id)
        try:
            with self._db:
                self._db.execute(
                    "UPDATE eaters SET name = ?, category = ?, calorie_goal = ? WHERE id = ?",
                    (name, Category(category).value, goal, eater_id),
                )
                self._db.execute("DELETE FROM eater_terms WHERE eater_id = ?", (eater_id,))
                self._save_terms(eater_id, allergies, dislikes)
        except sqlite3.IntegrityError:
            raise ValueError(f"Someone called {name} is already in the household") from None
        return self.get(eater_id)

    def delete(self, eater_id: int) -> None:
        with self._db:
            cursor = self._db.execute("DELETE FROM eaters WHERE id = ?", (eater_id,))
            if cursor.rowcount == 0:
                raise EaterNotFoundError(eater_id)

    def _save_terms(self, eater_id: int, allergies: Iterable[str], dislikes: Iterable[str]) -> None:
        allergy_terms = _clean_terms(allergies)
        # An allergy is a hard exclusion, so the same term is not also kept as a dislike.
        dislike_terms = tuple(t for t in _clean_terms(dislikes) if t not in allergy_terms)
        for kind, terms in (("allergy", allergy_terms), ("dislike", dislike_terms)):
            for position, term in enumerate(terms):
                self._db.execute(
                    "INSERT INTO eater_terms (eater_id, kind, term, position) VALUES (?, ?, ?, ?)",
                    (eater_id, kind, term, position),
                )

    def _eater(self, eater_id: int, name: str, category: str, goal: int | None) -> Eater:
        terms = self._db.execute(
            "SELECT kind, term FROM eater_terms WHERE eater_id = ? ORDER BY kind, position",
            (eater_id,),
        ).fetchall()
        return Eater(
            eater_id,
            name,
            Category(category),
            goal,
            allergies=tuple(term for kind, term in terms if kind == "allergy"),
            dislikes=tuple(term for kind, term in terms if kind == "dislike"),
        )
