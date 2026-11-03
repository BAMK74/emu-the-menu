"""The Household's town and the weather last fetched for it, in the same SQLite file.

The forecast is kept so Menus can be planned offline and so the weather service is asked at most
once every few hours. If a refresh fails, the last forecast is used for the days it still covers.
"""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from emu_the_menu.recipes import Weather
from emu_the_menu.weather import (
    DEFAULT_UNIT,
    UNITS,
    DayWeather,
    GetJson,
    Limits,
    Place,
    WeatherUnavailableError,
    classify,
    fetch_forecast,
    http_get_json,
)

REFRESH_AFTER = timedelta(hours=6)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weather_days (
    day  TEXT PRIMARY KEY,
    high REAL NOT NULL,
    low  REAL NOT NULL
);
"""


class WeatherRepository:
    def __init__(self, path: str | Path):
        self._db = sqlite3.connect(path)
        self._db.executescript(_SCHEMA)

    def __enter__(self) -> "WeatherRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    def _setting(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def place(self) -> Place | None:
        """The town weather is read for, or None if not set."""
        try:
            name = self._setting("place_name")
            lat, lon = self._setting("place_lat"), self._setting("place_lon")
            if name is None or lat is None or lon is None:
                return None
            return Place(
                name,
                self._setting("place_region") or "",
                self._setting("place_country") or "",
                float(lat),
                float(lon),
            )
        except ValueError:
            return None

    def set_place(self, place: Place | None) -> None:
        """Save the town (forgetting the old forecast), or clear it with None."""
        keys = ("place_name", "place_region", "place_country", "place_lat", "place_lon")
        with self._db:
            self._db.execute("DELETE FROM weather_days")
            self._db.execute("DELETE FROM settings WHERE key = 'weather_fetched'")
            self._db.executemany("DELETE FROM settings WHERE key = ?", [(k,) for k in keys])
            if place is not None:
                self._db.executemany(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    [
                        ("place_name", place.name),
                        ("place_region", place.region),
                        ("place_country", place.country_code),
                        ("place_lat", repr(place.latitude)),
                        ("place_lon", repr(place.longitude)),
                    ],
                )

    def limits(self) -> Limits:
        """Where warm and cold start, in °C (the built-in figures until changed)."""
        built_in = Limits()
        try:
            warm = self._setting("warm_from")
            cold = self._setting("cold_up_to")
            return Limits(
                built_in.warm_from if warm is None else float(warm),
                built_in.cold_up_to if cold is None else float(cold),
            )
        except ValueError:
            return built_in

    def unit(self) -> str:
        """ "C" or "F": how temperatures are shown and typed. Limits are always kept in °C."""
        value = self._setting("temperature_unit")
        return value if value in UNITS else DEFAULT_UNIT

    def set_unit(self, unit: str) -> None:
        if unit not in UNITS:
            raise ValueError("Choose Celsius or Fahrenheit")
        with self._db:
            self._db.execute(
                "INSERT INTO settings (key, value) VALUES ('temperature_unit', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (unit,),
            )

    def set_limits(self, limits: Limits) -> None:
        with self._db:
            for key, value in (("warm_from", limits.warm_from), ("cold_up_to", limits.cold_up_to)):
                self._db.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, repr(value)),
                )

    def days(self, today: date) -> list[DayWeather]:
        """The stored forecast from `today` on, each day marked warm or cold by the limits."""
        limits = self.limits()
        rows = self._db.execute(
            "SELECT day, high, low FROM weather_days WHERE day >= ? ORDER BY day",
            (today.isoformat(),),
        )
        return [
            DayWeather(date.fromisoformat(d), high, low, classify(high, limits))
            for d, high, low in rows
        ]

    def refresh(
        self, get: GetJson = http_get_json, now: datetime | None = None, force: bool = False
    ) -> bool:
        """Fetch the forecast if the stored one is old. True if the service answered.

        With no town set, nothing is fetched. A failed fetch keeps what is stored.
        """
        place = self.place()
        if place is None:
            return False
        now = now or datetime.now(UTC)
        stamp = self._setting("weather_fetched")
        if stamp and not force:
            try:
                if now - datetime.fromisoformat(stamp) < REFRESH_AFTER:
                    return True
            except ValueError:
                pass
        try:
            days = fetch_forecast(place, get)
        except WeatherUnavailableError:
            return False
        with self._db:
            self._db.execute("DELETE FROM weather_days")
            self._db.executemany(
                "INSERT INTO weather_days (day, high, low) VALUES (?, ?, ?)",
                [(d.day.isoformat(), d.high, d.low) for d in days],
            )
            self._db.execute(
                "INSERT INTO settings (key, value) VALUES ('weather_fetched', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (now.isoformat(),),
            )
        return True


def weather_for(
    db_path: str | Path, today: date, get: GetJson = http_get_json
) -> dict[date, DayWeather]:
    """The forecast by day, refreshed first if it is old. Empty with no town or no data."""
    with WeatherRepository(db_path) as repo:
        if repo.place() is None:
            return {}
        repo.refresh(get)
        return {d.day: d for d in repo.days(today)}


def kinds(forecast: dict[date, DayWeather]) -> dict[date, Weather | None]:
    """The warm or cold days of a forecast, for the planner."""
    return {day: w.kind for day, w in forecast.items()}
