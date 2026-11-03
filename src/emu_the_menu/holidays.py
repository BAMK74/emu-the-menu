"""Public holidays for the Household's country, from Nager.Date (no account or key needed).

Only the year and the country code are sent. Only holidays that apply across the whole country
are kept, because regional ones cannot be told apart without knowing the region. Standard library
only; the network is injectable for tests.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from urllib.request import Request, urlopen

_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
_USER_AGENT = "EmuTheMenu/0.1 (household-app; https://github.com/BAMK74/emu-the-menu)"
_TIMEOUT_SECONDS = 8

GetJson = Callable[[str], object]


class HolidaysUnavailableError(RuntimeError):
    """The holiday service could not be reached, or has no list for that country."""


@dataclass(frozen=True)
class Holiday:
    day: date
    name: str


def http_get_json(url: str) -> object:
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        return json.load(response)


def fetch_holidays(year: int, country_code: str, get: GetJson = http_get_json) -> list[Holiday]:
    """The nationwide public holidays of `year`, in date order."""
    code = country_code.strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError("Set the country in Settings first")
    try:
        answer = get(_URL.format(year=int(year), country=code))
        if not isinstance(answer, list):
            raise ValueError("unexpected answer")
        found: dict[tuple[date, str], Holiday] = {}
        for entry in answer:
            if entry.get("global") is False:
                continue
            name = " ".join(str(entry.get("name") or entry.get("localName") or "").split())[:80]
            day = date.fromisoformat(entry["date"])
            if name and day.year == year:
                found[(day, name)] = Holiday(day, name)
        return sorted(found.values(), key=lambda h: (h.day, h.name))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HolidaysUnavailableError(
            "The holiday list could not be fetched for this country"
        ) from exc
