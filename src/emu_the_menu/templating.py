"""The Jinja templates shared by every page."""

from datetime import date
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from emu_the_menu.disclaimer import DISCLAIMER_VERSION
from emu_the_menu.ingredients import kcal_basis
from emu_the_menu.look import LookChoice, is_southern, look_for
from emu_the_menu.repository import StockRepository
from emu_the_menu.units import display_quantity
from emu_the_menu.weather import degrees
from emu_the_menu.weather_store import WeatherRepository


def _legal_context(request: Request) -> dict:
    """Whether the current disclaimer was accepted, so every page can ask until it is."""
    db_path = getattr(request.app.state, "db_path", None)
    accepted = False
    if db_path is not None:
        with StockRepository(db_path) as repo:
            acceptance = repo.disclaimer_acceptance()
        accepted = bool(acceptance) and acceptance[0] == DISCLAIMER_VERSION
    return {"disclaimer_accepted": accepted, "disclaimer_version": DISCLAIMER_VERSION}


def _unit_context(request: Request) -> dict:
    """The temperature unit (C or F) the Household chose, for pages that show the weather."""
    db_path = getattr(request.app.state, "db_path", None)
    if db_path is None:
        return {"temp_unit": "C"}
    with WeatherRepository(db_path) as weather:
        return {"temp_unit": weather.unit()}


def _look_context(request: Request) -> dict:
    """The season (and festive overlay) every page is dressed in, from today's date, the Settings
    choice and the Household's hemisphere (the weather town's latitude, else the country)."""
    db_path = getattr(request.app.state, "db_path", None)
    today = getattr(request.app.state, "today", date.today)
    choice, southern = LookChoice(), False
    if db_path is not None:
        with StockRepository(db_path) as repo:
            choice, country = repo.look_choice(), repo.household_country()
        with WeatherRepository(db_path) as weather:
            place = weather.place()
        southern = is_southern(country, place.latitude if place else None)
    look = look_for(today(), choice, southern)
    return {"look": look, "season": look.season.value, "festive": look.festive or ""}


TEMPLATES = Jinja2Templates(
    directory=Path(__file__).parent / "templates",
    context_processors=[_legal_context, _unit_context, _look_context],
)
TEMPLATES.env.globals["degrees"] = degrees
TEMPLATES.env.globals["display_quantity"] = display_quantity
TEMPLATES.env.globals["kcal_basis"] = kcal_basis
