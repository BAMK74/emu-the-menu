"""The Menu page: plan a few days of Meals for the people ticked, from the Recipes and the Stock.

The plan is made fresh from the address, so the same settings and seed always give the same page.
Nothing is saved until "Save this menu", which makes the same plan again from the same settings
and keeps it (see `web_saved_menus`). Reshuffling just changes the seed.
"""

from dataclasses import dataclass
from datetime import date
from itertools import groupby
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.datastructures import FormData

from emu_the_menu.cooking import shopping_amounts
from emu_the_menu.eaters import Eater, EaterRepository
from emu_the_menu.menu_repository import MenuRepository
from emu_the_menu.occasion_repository import occasions_for
from emu_the_menu.planner import Menu, PlanSettings, Slot, plan
from emu_the_menu.posted import posted_form
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import analyse
from emu_the_menu.reserve import free_stock, holdings
from emu_the_menu.templating import TEMPLATES
from emu_the_menu.units import display_quantity
from emu_the_menu.weather import http_get_json
from emu_the_menu.weather_store import kinds, weather_for

DEFAULT_DAYS = 7
MAX_DAYS = 28
MAX_ALLOWANCE = 50
MAX_COOLDOWN = 60
_DEFAULTS = PlanSettings()
_DEFAULT_SLOTS = [Slot.BREAKFAST, Slot.LUNCH, Slot.DINNER]


def _whole(text: str, low: int, high: int, message: str) -> int:
    try:
        number = int(text.strip())
    except ValueError:
        raise ValueError(message) from None
    if not low <= number <= high:
        raise ValueError(message)
    return number


def day_label(day: date) -> str:
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def shopping_text(pairs) -> list[str]:
    """Shopping list entries such as "carrots 1.2 kg" from (Ingredient, base amount) pairs."""
    found = []
    for ingredient, amount in pairs:
        quantity, unit = display_quantity(amount, ingredient.base_unit)
        found.append(f"{ingredient.name} {quantity:g} {unit}".strip())
    return found


def held_text(database: str, today: date, before: int | None = None):
    """What accepted Menus hold, as (menu, ["carrots 400 g", ...]) for the page to say so."""
    return [(menu, shopping_text(items)) for menu, items in holdings(database, today, before)]


def days_of(meals):
    """Meals grouped by day for the template, each with its unchecked Ingredients."""

    def view(meal):
        unchecked = ()
        if meal.recipe:
            unchecked = analyse(meal.recipe.for_analysis, meal.recipe.servings).unchecked
        return meal, unchecked

    return [(day, [view(m) for m in group]) for day, group in groupby(meals, key=lambda m: m.day)]


@dataclass(frozen=True)
class PlanRequest:
    """The settings of a plan, checked."""

    start: date
    days: int
    slots: list[Slot]
    people: list[Eater]
    allowance: int
    cooldown: int
    seed: int

    def run(self, database: str, today: date, forecast=None, occasions=None) -> Menu:
        """Plan, on the Stock that accepted Menus are not holding."""
        with RecipeRepository(database) as recipes, MenuRepository(database) as menus:
            return plan(
                self.start,
                self.days,
                self.slots,
                recipes.recipes(),
                self.people,
                free_stock(database, today),
                seed=self.seed,
                settings=PlanSettings(self.allowance, self.cooldown),
                last_served=menus.last_served(),
                weather=kinds(forecast or {}),
                occasions=occasions,
            )

    def query(self, seed: int) -> list[tuple[str, object]]:
        return [
            ("go", "1"),
            ("start", self.start.isoformat()),
            ("days", self.days),
            *[("meal_types", m.value) for m in self.slots],
            *[("eater", p.id) for p in self.people],
            ("allowance", self.allowance),
            ("cooldown", self.cooldown),
            ("seed", seed),
        ]


def parse_plan(
    everyone: list[Eater],
    start: str,
    days: str,
    meal_types: list[str],
    eater: list[int],
    allowance: str,
    cooldown: str,
    seed: str,
) -> PlanRequest:
    """Check the settings of a plan. Raises ValueError with a message to show."""
    try:
        first = date.fromisoformat(start)
    except ValueError:
        raise ValueError("Enter a valid start date") from None
    length = _whole(days, 1, MAX_DAYS, f"Choose between 1 and {MAX_DAYS} days")
    shop = _whole(
        allowance, 0, MAX_ALLOWANCE, f"The shopping allowance must be 0 to {MAX_ALLOWANCE}"
    )
    cool = _whole(cooldown, 0, MAX_COOLDOWN, f"The cooldown must be 0 to {MAX_COOLDOWN} days")
    number = _whole(seed, 0, 10**9, "The seed must be a number")
    if not meal_types or not set(meal_types) <= {s.value for s in Slot}:
        raise ValueError("Choose meal types from the list, at least one")
    people = [e for e in everyone if e.id in eater]
    if not people:
        raise ValueError("Choose who is eating")
    slots = [s for s in Slot if s.value in meal_types]
    return PlanRequest(first, length, slots, people, shop, cool, number)


def create_router(database: str, today=date.today, get_json=http_get_json) -> APIRouter:
    router = APIRouter()

    @router.get("/menu", response_class=HTMLResponse)
    def menu(
        request: Request,
        go: str = "",
        start: str = "",
        days: str = str(DEFAULT_DAYS),
        meal_types: list[str] = Query([]),
        eater: list[int] = Query([]),
        allowance: str = str(_DEFAULTS.shopping_allowance),
        cooldown: str = str(_DEFAULTS.cooldown_days),
        seed: str = "0",
    ):
        with EaterRepository(database) as eater_repo:
            everyone = eater_repo.list()
        submitted = bool(go)
        chosen_meals = meal_types if submitted else [s.value for s in _DEFAULT_SLOTS]
        chosen_eaters = eater if submitted else [e.id for e in everyone]
        form = {
            "start": start or today().isoformat(),
            "days": days,
            "meal_types": chosen_meals,
            "eaters": chosen_eaters,
            "allowance": allowance,
            "cooldown": cooldown,
            "seed": seed,
        }
        context = {
            "form": form,
            "eaters": everyone,
            "meal_choices": list(Slot),
            "max_days": MAX_DAYS,
            "days_shown": [],
            "shopping": [],
            "error": None,
            "reshuffle": None,
            "label": day_label,
            "forecast": {},
            "occasions": {},
            "holds": [],
            "holds_for": "planning a new menu",
            "save_fields": None,
        }

        def answer(error: str | None = None, status: int = 200, **extra):
            return TEMPLATES.TemplateResponse(
                request, "menu.html", {**context, "error": error, **extra}, status_code=status
            )

        if not everyone:
            return answer()
        try:
            settings = parse_plan(
                everyone,
                form["start"],
                days,
                chosen_meals,
                chosen_eaters,
                allowance,
                cooldown,
                seed,
            )
        except ValueError as exc:
            return answer(str(exc), 400)
        forecast = weather_for(database, today(), get_json)
        occasions = occasions_for(database, settings.start, settings.days)
        result = settings.run(database, today(), forecast, occasions)
        held = free_stock(database, today())
        return answer(
            days_shown=days_of(result.meals),
            forecast=forecast,
            occasions=occasions,
            holds=held_text(database, today()),
            shopping=shopping_text(shopping_amounts(result.meals, held)),
            reshuffle="/menu?" + urlencode(settings.query(settings.seed + 1)),
            save_fields=settings.query(settings.seed),
        )

    @router.post("/menu/save")
    def save(request: Request, form: FormData = Depends(posted_form)):
        """Plan again from the same settings and keep the result as a saved Menu."""
        with EaterRepository(database) as eater_repo:
            everyone = eater_repo.list()
        try:
            settings = parse_plan(
                everyone,
                str(form.get("start", "")),
                str(form.get("days", "")),
                [str(v) for v in form.getlist("meal_types")],
                [int(v) for v in form.getlist("eater") if str(v).isdigit()],
                str(form.get("allowance", _DEFAULTS.shopping_allowance)),
                str(form.get("cooldown", _DEFAULTS.cooldown_days)),
                str(form.get("seed", "0")),
            )
        except ValueError as exc:
            return HTMLResponse(f"error: {exc}", status_code=400)
        result = settings.run(
            database,
            today(),
            weather_for(database, today(), get_json),
            occasions_for(database, settings.start, settings.days),
        )
        with MenuRepository(database) as menus:
            menu_id = menus.save(
                str(form.get("name", "")), settings.start, settings.days, result, settings.people
            )
        return RedirectResponse(f"/menus/{menu_id}", status_code=303)

    return router
