"""The Menu page: plan a few days of Meals for the people ticked, from the Recipes and the Stock.

The plan is made fresh from the address, so the same settings and seed always give the same page.
Nothing is saved yet: reshuffling just changes the seed.
"""

from datetime import date
from itertools import groupby
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from emu_the_menu.eaters import EaterRepository
from emu_the_menu.planner import PlanSettings, Slot, plan
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import analyse
from emu_the_menu.repository import StockRepository
from emu_the_menu.templating import TEMPLATES

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


def create_router(db_path: str | Path, today=date.today) -> APIRouter:
    router = APIRouter()

    def stock_map(stock: StockRepository) -> dict[int, date | None]:
        """Ingredient id to when what is held runs out: None if any of it never expires."""
        ids = {i.name: i.id for i in stock.ingredients()}
        held: dict[int, list[date | None]] = {}
        for item in stock.list_items():
            if item.quantity > 0 and item.ingredient in ids:
                held.setdefault(ids[item.ingredient], []).append(item.expires_on)
        return {
            ingredient_id: None if None in dates else max(dates)  # type: ignore[type-var]
            for ingredient_id, dates in held.items()
        }

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
        with EaterRepository(db_path) as eater_repo:
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
        }

        def answer(error: str | None = None, status: int = 200, **extra):
            return TEMPLATES.TemplateResponse(
                request, "menu.html", {**context, "error": error, **extra}, status_code=status
            )

        if not everyone:
            return answer()
        try:
            first = date.fromisoformat(form["start"])
        except ValueError:
            return answer("Enter a valid start date", 400)
        try:
            length = _whole(days, 1, MAX_DAYS, f"Choose between 1 and {MAX_DAYS} days")
            shop = _whole(
                allowance, 0, MAX_ALLOWANCE, f"The shopping allowance must be 0 to {MAX_ALLOWANCE}"
            )
            cool = _whole(
                cooldown, 0, MAX_COOLDOWN, f"The cooldown must be 0 to {MAX_COOLDOWN} days"
            )
            number = _whole(seed, 0, 10**9, "The seed must be a number")
        except ValueError as exc:
            return answer(str(exc), 400)
        valid_meals = {s.value for s in Slot}
        if not chosen_meals or not set(chosen_meals) <= valid_meals:
            return answer("Choose meal types from the list, at least one", 400)
        people = [e for e in everyone if e.id in chosen_eaters]
        if not people:
            return answer("Choose who is eating", 400)

        meal_types_chosen = [s for s in Slot if s.value in chosen_meals]
        with RecipeRepository(db_path) as recipes, StockRepository(db_path) as stock:
            result = plan(
                first,
                length,
                meal_types_chosen,
                recipes.recipes(),
                people,
                stock_map(stock),
                seed=number,
                settings=PlanSettings(shop, cool),
            )

        def view(meal):
            unchecked = ()
            if meal.recipe:
                unchecked = analyse(meal.recipe.for_analysis, meal.recipe.servings).unchecked
            return meal, unchecked

        by_day = [
            (day, [view(m) for m in meals])
            for day, meals in groupby(result.meals, key=lambda m: m.day)
        ]
        reshuffle = "/menu?" + urlencode(
            [
                ("go", "1"),
                ("start", form["start"]),
                ("days", length),
                *[("meal_types", m.value) for m in meal_types_chosen],
                *[("eater", p.id) for p in people],
                ("allowance", shop),
                ("cooldown", cool),
                ("seed", number + 1),
            ]
        )
        return answer(days_shown=by_day, shopping=result.shopping, reshuffle=reshuffle)

    return router
