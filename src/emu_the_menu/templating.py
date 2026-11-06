"""The Jinja templates shared by every page."""

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from emu_the_menu.disclaimer import DISCLAIMER_VERSION
from emu_the_menu.illustrations import food_icon, recipe_icon, slot_icon
from emu_the_menu.ingredients import kcal_basis
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


TEMPLATES = Jinja2Templates(
    directory=Path(__file__).parent / "templates",
    context_processors=[_legal_context, _unit_context],
)
TEMPLATES.env.globals["degrees"] = degrees
TEMPLATES.env.globals["display_quantity"] = display_quantity
TEMPLATES.env.globals["kcal_basis"] = kcal_basis


def _icon(name: str, size: str = "") -> Markup:
    """A drawing from static/art.svg, coloured by the page's season."""
    extra = f" {size}" if size else ""
    return Markup(
        f'<svg class="ico{extra}" viewBox="0 0 48 48" aria-hidden="true" focusable="false">'
        f'<use href="/static/art.svg#i-{name}"/></svg>'
    )


TEMPLATES.env.globals["icon"] = _icon
TEMPLATES.env.globals["food_icon"] = food_icon
TEMPLATES.env.globals["slot_icon"] = slot_icon
TEMPLATES.env.globals["recipe_icon"] = lambda r: recipe_icon(line.ingredient.name for line in r.lines)
