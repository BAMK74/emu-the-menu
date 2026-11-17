"""The Jinja templates shared by every page."""

from datetime import date
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from markupsafe import Markup, escape

from emu_the_menu.disclaimer import DISCLAIMER_VERSION
from emu_the_menu.illustrations import food_icon, recipe_icon, slot_icon
from emu_the_menu.ingredients import kcal_basis
from emu_the_menu.look import LookChoice, is_southern, look_for
from emu_the_menu.repository import StockRepository
from emu_the_menu.units import display_quantity
from emu_the_menu.weather import degrees
from emu_the_menu.weather_store import WeatherRepository


def _legal_context(request: Request) -> dict:
    """Whether the current disclaimer was accepted, so every page can ask until it is."""
    database = getattr(request.app.state, "database", None)
    accepted = False
    if database is not None:
        with StockRepository(database) as repo:
            acceptance = repo.disclaimer_acceptance()
        accepted = bool(acceptance) and acceptance[0] == DISCLAIMER_VERSION
    return {"disclaimer_accepted": accepted, "disclaimer_version": DISCLAIMER_VERSION}


def _unit_context(request: Request) -> dict:
    """The temperature unit (C or F) the Household chose, for pages that show the weather."""
    database = getattr(request.app.state, "database", None)
    if database is None:
        return {"temp_unit": "C"}
    with WeatherRepository(database) as weather:
        return {"temp_unit": weather.unit()}


def _look_context(request: Request) -> dict:
    """The season (and festive overlay) every page is dressed in, from today's date, the Settings
    choice and the Household's hemisphere (the weather town's latitude, else the country)."""
    database = getattr(request.app.state, "database", None)
    today = getattr(request.app.state, "today", date.today)
    choice, southern = LookChoice(), False
    if database is not None:
        with StockRepository(database) as repo:
            choice, country = repo.look_choice(), repo.household_country()
        with WeatherRepository(database) as weather:
            place = weather.place()
        southern = is_southern(country, place.latitude if place else None)
    look = look_for(today(), choice, southern)
    return {"look": look, "season": look.season.value, "festive": look.festive or ""}


def _account_context(request: Request) -> dict:
    """Who is signed in (None on the sign-in page), and the token their forms must carry."""
    session = getattr(request.state, "session", None)
    return {
        "account": session.account if session else None,
        "csrf_token": session.csrf_token if session else "",
    }


TEMPLATES = Jinja2Templates(
    directory=Path(__file__).parent / "templates",
    context_processors=[_legal_context, _unit_context, _look_context, _account_context],
)


@pass_context
def _csrf_field(context) -> Markup:
    """The hidden field every form that posts carries (see `auth.guard`)."""
    return Markup('<input type="hidden" name="csrf_token" value="{}">').format(
        escape(context.get("csrf_token", ""))
    )


TEMPLATES.env.globals["csrf_field"] = _csrf_field


def _icon(name: str) -> Markup:
    """A drawing from static/art.svg, coloured by the season of the page."""
    return Markup(
        f'<svg class="ico" viewBox="0 0 48 48" aria-hidden="true" focusable="false">'
        f'<use href="/static/art.svg#i-{name}"/></svg>'
    )


TEMPLATES.env.globals["icon"] = _icon
TEMPLATES.env.globals["food_icon"] = food_icon
TEMPLATES.env.globals["slot_icon"] = slot_icon
TEMPLATES.env.globals["recipe_icon"] = lambda r: recipe_icon(
    line.ingredient.name for line in r.lines
)
TEMPLATES.env.globals["degrees"] = degrees
TEMPLATES.env.globals["display_quantity"] = display_quantity
TEMPLATES.env.globals["kcal_basis"] = kcal_basis
