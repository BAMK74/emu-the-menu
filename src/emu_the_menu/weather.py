"""Weather for the Household's town, from Open-Meteo (no account or key needed).

Only the town name and the coordinates found for it are sent, never anything about the people
or the food. A day is `warm` or `cold` by its highest temperature; in between it is neither, and
Recipes tagged for a kind of weather get no push either way. Standard library only.
"""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from emu_the_menu.recipes import Weather

# The built-in limits, in °C. A day whose high reaches `warm_from` is warm; one whose high stays at
# or under `cold_up_to` is cold. Both can be changed in Settings.
WARM_FROM = 20.0
COLD_UP_TO = 12.0
LIMIT_RANGE = (-30.0, 50.0)  # what Settings accepts, in °C
FORECAST_DAYS = 16  # the most Open-Meteo gives

_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"
_USER_AGENT = "EmuTheMenu/0.1 (household-app; https://github.com/BAMK74/emu-the-menu)"
_TIMEOUT_SECONDS = 8

GetJson = Callable[[str], dict]


class PlaceNotFoundError(LookupError):
    """No town of that name was found."""


class WeatherUnavailableError(RuntimeError):
    """The weather service could not be reached or gave an unreadable answer."""


@dataclass(frozen=True)
class Place:
    name: str
    region: str  # state or province, may be empty
    country_code: str
    latitude: float
    longitude: float

    @property
    def label(self) -> str:
        return ", ".join(part for part in (self.name, self.region, self.country_code) if part)


@dataclass(frozen=True)
class DayWeather:
    day: date
    high: float  # °C
    low: float  # °C
    kind: Weather | None = None  # warm, cold or neither, by the Household's limits


@dataclass(frozen=True)
class Limits:
    """Where warm and cold start, in °C."""

    warm_from: float = WARM_FROM
    cold_up_to: float = COLD_UP_TO


DEFAULT_LIMITS = Limits()


def c_to_f(celsius: float) -> float:
    return celsius * 9 / 5 + 32


def f_to_c(fahrenheit: float) -> float:
    return (fahrenheit - 32) * 5 / 9


def classify(high: float, limits: Limits = DEFAULT_LIMITS) -> Weather | None:
    """Warm, cold, or None for a day that is neither."""
    if high >= limits.warm_from:
        return Weather.WARM
    if high <= limits.cold_up_to:
        return Weather.COLD
    return None


UNITS = ("C", "F")
DEFAULT_UNIT = "C"


def parse_unit(text: str) -> str:
    """ "C" or "F" (any case). Raises ValueError for anything else."""
    unit = text.strip().upper()
    if unit not in UNITS:
        raise ValueError("Choose Celsius or Fahrenheit")
    return unit


def to_unit(celsius: float, unit: str) -> float:
    """A temperature kept in °C, in the unit shown to people."""
    return c_to_f(celsius) if unit == "F" else celsius


def from_unit(value: float, unit: str) -> float:
    return f_to_c(value) if unit == "F" else value


def degrees(celsius: float, unit: str = DEFAULT_UNIT) -> int:
    """A temperature as a whole number in the unit shown to people, for the Menu pages."""
    return round(to_unit(celsius, unit))


def parse_limit(text: str, label: str, unit: str = DEFAULT_UNIT) -> float | None:
    """A limit typed in Settings, in `unit` (a dot or a comma), returned in °C.

    Blank means the built-in figure.
    """
    text = text.strip().replace(",", ".")
    if not text:
        return None
    name = "Fahrenheit" if unit == "F" else "Celsius"
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{label} must be a number of degrees {name}") from None
    low, high = (round(to_unit(c, unit), 1) for c in LIMIT_RANGE)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be between {low:g} and {high:g} °{unit}")
    return round(from_unit(value, unit), 2)


def parse_limits(warm_text: str, cold_text: str, unit: str = DEFAULT_UNIT) -> Limits:
    """Both limits from Settings, typed in `unit`, kept in °C. Raises ValueError with a message."""
    warm = parse_limit(warm_text, "Warm from", unit)
    cold = parse_limit(cold_text, "Cold up to", unit)
    limits = Limits(WARM_FROM if warm is None else warm, COLD_UP_TO if cold is None else cold)
    if limits.cold_up_to >= limits.warm_from:
        raise ValueError("Cold up to must be lower than Warm from")
    return limits


def http_get_json(url: str) -> dict:
    """Read one JSON answer. Raises OSError or ValueError on failure."""
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        return json.load(response)


def _number(value, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("out of range")
    return float(value)


def find_place(query: str, country_code: str | None, get: GetJson = http_get_json) -> Place:
    """The best match for a town name, inside `country_code` when one is given."""
    query = " ".join(query.split())
    if not query:
        raise PlaceNotFoundError("Enter a town")
    if len(query) > 80:
        raise PlaceNotFoundError("That town name is too long")
    params = {"name": query, "count": 1, "language": "en", "format": "json"}
    if country_code:
        params["countryCode"] = country_code
    try:
        answer = get(f"{_GEOCODE}?{urlencode(params)}")
        results = answer.get("results") or []
        if not results:
            raise PlaceNotFoundError(f"No town called {query} was found. Check the spelling.")
        top = results[0]
        return Place(
            name=str(top["name"])[:80],
            region=str(top.get("admin1") or "")[:80],
            country_code=str(top.get("country_code") or country_code or "")[:2].upper(),
            latitude=_number(top["latitude"], -90, 90),
            longitude=_number(top["longitude"], -180, 180),
        )
    except PlaceNotFoundError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise WeatherUnavailableError("The town lookup could not be reached") from exc


def fetch_forecast(place: Place, get: GetJson = http_get_json) -> list[DayWeather]:
    """The coming days' highs and lows, in the town's time zone. Missing days are skipped."""
    params = {
        "latitude": place.latitude,
        "longitude": place.longitude,
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
        "forecast_days": FORECAST_DAYS,
    }
    try:
        daily = get(f"{_FORECAST}?{urlencode(params)}")["daily"]
        days = []
        for text, high, low in zip(
            daily["time"], daily["temperature_2m_max"], daily["temperature_2m_min"], strict=True
        ):
            if high is None or low is None:
                continue
            days.append(
                DayWeather(date.fromisoformat(text), _number(high, -90, 60), _number(low, -90, 60))
            )
        return days
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise WeatherUnavailableError("The weather service could not be reached") from exc
