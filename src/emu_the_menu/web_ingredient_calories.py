"""Entering calories for many Ingredients at once.

Calories are needed for portions and the calorie fit of a Menu, and a Recipe shows "Calories
unknown" until every Ingredient has them. This page lists the Ingredients without calories (the
most used in Recipes first, or just one Recipe's) with a box for each. Foods in `calorie_hints`
come pre-filled with a rough figure, labelled as a suggestion; a blank box saves nothing, and
every typed or suggested figure is saved when the person presses Save. Calories are saved on their
own (`StockRepository.set_calories`): they never mark an Ingredient as reviewed, because they say
nothing about allergens.
"""

import math
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from emu_the_menu.calorie_hints import suggest_kcal
from emu_the_menu.ingredients import kcal_basis, parse_kcal
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.repository import StockRepository
from emu_the_menu.templating import TEMPLATES

PAGE_SIZE = 40


def _whole(text: str, default: int = 0) -> int:
    return int(text) if text.strip().isdecimal() else default


def create_router(db_path: str | Path) -> APIRouter:
    router = APIRouter()

    def missing(recipe_id: int):
        """Ingredients without calories, most used first, and the Recipe asked for (or None)."""
        used: dict[int, int] = {}
        chosen = None
        with RecipeRepository(db_path) as recipes:
            for recipe in recipes.recipes():
                for line in recipe.lines:
                    used[line.ingredient.id] = used.get(line.ingredient.id, 0) + 1
                if recipe.id == recipe_id:
                    chosen = recipe
        with StockRepository(db_path) as stock:
            found = [i for i in stock.ingredients() if i.kcal is None]
        if chosen is not None:
            wanted = {line.ingredient.id for line in chosen.lines}
            found = [i for i in found if i.id in wanted]
        found.sort(key=lambda i: (-used.get(i.id, 0), i.name))
        return found, used, chosen

    def render(
        request: Request, recipe_id: int, number: int, done: int = 0, error=None, typed=None
    ):
        found, used, chosen = missing(recipe_id)
        pages = max(1, math.ceil(len(found) / PAGE_SIZE))
        number = min(max(1, number), pages)
        shown = found[(number - 1) * PAGE_SIZE : number * PAGE_SIZE]
        typed = typed or {}
        rows = []
        for i in shown:
            hint = suggest_kcal(i.name, i.base_unit)
            value = typed.get(i.id)
            rows.append(
                {
                    "ingredient": i,
                    "used": used.get(i.id, 0),
                    "basis": kcal_basis(i.base_unit),
                    "hint": hint,
                    "value": value if value is not None else ("" if hint is None else f"{hint:g}"),
                    "suggested": value is None and hint is not None,
                }
            )
        base = {"recipe": chosen.id} if chosen else {}
        return TEMPLATES.TemplateResponse(
            request,
            "ingredient_calories.html",
            {
                "rows": rows,
                "total": len(found),
                "recipe": chosen,
                "page": number,
                "pages": pages,
                "done": done,
                "error": error,
                "page_link": lambda n: "/ingredients/calories?" + urlencode({**base, "page": n}),
            },
            status_code=400 if error else 200,
        )

    @router.get("/ingredients/calories", response_class=HTMLResponse)
    def show(request: Request, recipe: str = "", page: str = "", done: str = ""):
        return render(request, _whole(recipe), _whole(page, 1), _whole(done))

    @router.post("/ingredients/calories")
    async def save(request: Request):
        form = await request.form()
        recipe_id = _whole(str(form.get("recipe", "")))
        number = _whole(str(form.get("page", "")), 1)
        # Opened here, not injected: a connection must stay in the thread that made it.
        with StockRepository(db_path) as stock:
            known = {i.id: i for i in stock.ingredients()}
            figures: dict[int, float] = {}
            typed: dict[int, str] = {}
            try:
                for key, raw in form.multi_items():
                    if not key.startswith("kcal_") or not key[5:].isdecimal():
                        continue
                    ingredient = known.get(int(key[5:]))
                    text = str(raw)
                    if ingredient is None or not text.strip():
                        continue
                    typed[ingredient.id] = text
                    try:
                        value = parse_kcal(text, ingredient.base_unit)
                    except ValueError as exc:
                        raise ValueError(f"{ingredient.name}: {exc}") from None
                    if value is not None:
                        figures[ingredient.id] = value
            except ValueError as exc:
                return render(request, recipe_id, number, error=str(exc), typed=typed)
            for ingredient_id, value in figures.items():
                stock.set_calories(ingredient_id, value)
        query = {"done": len(figures)}
        if recipe_id:
            query["recipe"] = recipe_id
        return RedirectResponse("/ingredients/calories?" + urlencode(query), status_code=303)

    return router
