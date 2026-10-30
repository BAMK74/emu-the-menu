"""The Recipes pages: a list of cards, and one form to add or edit a Recipe.

Calories, allergens and diets are never typed in. They come from the Recipe's Ingredients, and a
Recipe containing an Ingredient nobody has reviewed says "not checked", never "safe".
"""

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from emu_the_menu.eaters import EaterRepository
from emu_the_menu.page_fetch import PageUnavailableError, UnsafeAddressError, fetch_page
from emu_the_menu.recipe_import import (
    NoRecipeFoundError,
    extract_recipe,
    match_ingredient,
    parse_ingredient_line,
)
from emu_the_menu.recipe_repository import (
    LineInput,
    Recipe,
    RecipeNotFoundError,
    RecipeRepository,
)
from emu_the_menu.recipes import (
    MealType,
    Weather,
    analyse,
    parse_meal_types,
    parse_servings,
    parse_tags,
    parse_weather,
)
from emu_the_menu.repository import StockRepository
from emu_the_menu.suitability import assess
from emu_the_menu.templating import TEMPLATES
from emu_the_menu.units import UnknownUnitError, display_quantity, known_units


def read_lines(
    ingredients: list[str], quantities: list[str], units: list[str]
) -> tuple[list[LineInput], list[dict]]:
    """The typed ingredient rows as lines to save, and as form values to show again.

    A row with neither an ingredient nor a quantity is an unused blank row and is skipped.
    Raises ValueError for a half-filled row or a quantity that is not a number.
    """
    typed = [
        {"ingredient": name, "quantity": quantity, "unit": unit}
        for name, quantity, unit in zip(ingredients, quantities, units, strict=False)
        if name.strip() or quantity.strip()
    ]
    lines: list[LineInput] = []
    for row in typed:
        name, quantity = row["ingredient"].strip(), row["quantity"].strip()
        if not name:
            raise ValueError("Every ingredient line needs an ingredient")
        if not quantity:
            raise ValueError(f"Enter a quantity for {name}")
        if not re.fullmatch(r"\d+(?:[.,]\d+)?", quantity):
            raise ValueError(f"The quantity of {name} must be a number such as 200 or 1.5")
        lines.append((name, float(quantity.replace(",", ".")), row["unit"]))
    return lines, typed


def create_router(
    db_path: str | Path, fetch_recipe_page: Callable[[str], str] = fetch_page
) -> APIRouter:
    router = APIRouter()

    def get_repo() -> Iterator[RecipeRepository]:
        with RecipeRepository(db_path) as repo:
            yield repo

    def ingredient_names() -> list[str]:
        with StockRepository(db_path) as stock:
            return [i.name for i in stock.ingredients()]

    def facts(recipe: Recipe):
        return analyse([(line.ingredient, line.quantity) for line in recipe.lines], recipe.servings)

    def blank_form() -> dict:
        return {
            "name": "",
            "servings": "4",
            "meal_types": ["dinner"],
            "weather": "any",
            "tags": "",
            "source_url": "",
            "lines": [],
        }

    def form_of(recipe: Recipe) -> dict:
        lines = []
        for line in recipe.lines:
            quantity, unit = display_quantity(line.quantity, line.ingredient.base_unit)
            lines.append(
                {"ingredient": line.ingredient.name, "quantity": f"{quantity:g}", "unit": unit}
            )
        return {
            "name": recipe.name,
            "servings": str(recipe.servings),
            "meal_types": [m.value for m in recipe.meal_types],
            "weather": recipe.weather.value,
            "tags": ", ".join(recipe.tags),
            "source_url": recipe.source_url or "",
            "lines": lines,
        }

    def page(
        request: Request,
        form: dict,
        recipe: Recipe | None,
        error=None,
        status=200,
        imported: dict | None = None,
        import_url: str = "",
    ):
        # Always leave a few blank rows to type into, even without scripting.
        shown = list(form["lines"]) + [{"ingredient": "", "quantity": "", "unit": "g"}] * 3
        return TEMPLATES.TemplateResponse(
            request,
            "recipe.html",
            {
                "recipe": recipe,
                "form": {**form, "lines": shown},
                "error": error,
                "meal_choices": list(MealType),
                "weather_choices": list(Weather),
                "units": known_units(),
                "suggestions": ingredient_names(),
                "facts": facts(recipe) if recipe else None,
                "imported": imported,
                "import_url": import_url,
            },
            status_code=status,
        )

    def find(repo: RecipeRepository, recipe_id: int) -> Recipe:
        recipe = repo.get(recipe_id)
        if recipe is None:
            raise HTTPException(404, "No such recipe")
        return recipe

    def save(
        request: Request,
        repo: RecipeRepository,
        recipe_id: int | None,
        name: str,
        servings: str,
        meal_types: list[str],
        weather: str,
        tags: str,
        ingredient: list[str],
        quantity: list[str],
        unit: list[str],
        source_url: str,
    ):
        typed = {
            "name": name,
            "servings": servings,
            "meal_types": meal_types,
            "weather": weather,
            "tags": tags,
            "source_url": source_url,
            "lines": [],
        }
        recipe = repo.get(recipe_id) if recipe_id is not None else None
        try:
            lines, typed["lines"] = read_lines(ingredient, quantity, unit)
            args = (
                name,
                parse_servings(servings),
                parse_meal_types(meal_types),
                parse_weather(weather),
                parse_tags(tags),
                lines,
            )
            if recipe_id is None:
                repo.add(*args, source_url=source_url)
            else:
                repo.update(recipe_id, *args, source_url=source_url)
        except RecipeNotFoundError:
            raise HTTPException(404, "No such recipe") from None
        except (ValueError, UnknownUnitError) as exc:
            if not typed["lines"]:  # the error came from reading the rows
                typed["lines"] = [
                    {"ingredient": n, "quantity": q, "unit": u}
                    for n, q, u in zip(ingredient, quantity, unit, strict=False)
                    if n.strip() or q.strip()
                ]
            return page(request, typed, recipe, str(exc), 400)
        return RedirectResponse("/recipes", status_code=303)

    @router.get("/recipes", response_class=HTMLResponse)
    def recipes(request: Request, repo: RecipeRepository = Depends(get_repo)):
        with EaterRepository(db_path) as eater_repo:
            eaters = eater_repo.list()
        shown = []
        for recipe in repo.recipes():
            recipe_facts = facts(recipe)
            who = [(eater, assess(recipe, recipe_facts, eater)) for eater in eaters]
            shown.append((recipe, recipe_facts, who))
        return TEMPLATES.TemplateResponse(request, "recipes.html", {"recipes": shown})

    @router.get("/recipes/new", response_class=HTMLResponse)
    def new(request: Request):
        return page(request, blank_form(), None)

    @router.post("/recipes/import")
    def import_recipe(request: Request, url: str = Form("")):
        """Fill in the form from a recipe web page. Nothing is saved until a person saves."""
        url = url.strip()
        blank = blank_form()
        if not url:
            return page(request, blank, None, "Enter the web address of a recipe", 400)
        try:
            imported = extract_recipe(fetch_recipe_page(url))
        except UnsafeAddressError as exc:
            return page(request, blank, None, str(exc), 400, import_url=url)
        except PageUnavailableError as exc:
            return page(request, blank, None, str(exc), 502, import_url=url)
        except NoRecipeFoundError as exc:
            return page(request, blank, None, str(exc), 400, import_url=url)
        known = ingredient_names()
        rows, unreadable = [], []
        for text in imported.lines:
            line = parse_ingredient_line(text)
            name = match_ingredient(line.name, known)
            if line.readable:
                rows.append(
                    {"ingredient": name, "quantity": f"{line.quantity:g}", "unit": line.unit}
                )
            else:
                unreadable.append(text)
                rows.append({"ingredient": name, "quantity": "", "unit": "g"})
        form = {
            **blank,
            "name": imported.name,
            "servings": str(imported.servings or blank["servings"]),
            "tags": ", ".join(imported.tags),
            "source_url": url,
            "lines": rows,
        }
        info = {
            "host": urlsplit(url).hostname,
            "unreadable": unreadable,
            "servings_found": imported.servings is not None,
        }
        return page(request, form, None, imported=info)

    @router.post("/recipes")
    def create(
        request: Request,
        name: str = Form(""),
        servings: str = Form(""),
        meal_types: list[str] = Form([]),
        weather: str = Form(""),
        tags: str = Form(""),
        ingredient: list[str] = Form([]),
        quantity: list[str] = Form([]),
        unit: list[str] = Form([]),
        source_url: str = Form(""),
        repo: RecipeRepository = Depends(get_repo),
    ):
        return save(
            request, repo, None, name, servings, meal_types, weather, tags, ingredient,
            quantity, unit, source_url,
        )  # fmt: skip

    @router.get("/recipes/{recipe_id}", response_class=HTMLResponse)
    def edit(request: Request, recipe_id: int, repo: RecipeRepository = Depends(get_repo)):
        recipe = find(repo, recipe_id)
        return page(request, form_of(recipe), recipe)

    @router.post("/recipes/{recipe_id}")
    def update(
        request: Request,
        recipe_id: int,
        name: str = Form(""),
        servings: str = Form(""),
        meal_types: list[str] = Form([]),
        weather: str = Form(""),
        tags: str = Form(""),
        ingredient: list[str] = Form([]),
        quantity: list[str] = Form([]),
        unit: list[str] = Form([]),
        source_url: str = Form(""),
        repo: RecipeRepository = Depends(get_repo),
    ):
        find(repo, recipe_id)
        return save(
            request, repo, recipe_id, name, servings, meal_types, weather, tags, ingredient,
            quantity, unit, source_url,
        )  # fmt: skip

    @router.post("/recipes/{recipe_id}/delete")
    def delete(recipe_id: int, repo: RecipeRepository = Depends(get_repo)):
        try:
            repo.delete(recipe_id)
        except RecipeNotFoundError:
            raise HTTPException(404, "No such recipe") from None
        return RedirectResponse("/recipes", status_code=303)

    return router
