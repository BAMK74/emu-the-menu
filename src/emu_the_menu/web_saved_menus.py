"""Saved Menus: the list, one Menu, and deleting it.

A saved Menu holds only which Recipe is in each Meal and each person's servings. Everything shown
(names, calories, "Not checked", the shopping list) is worked out from the Recipes, Eaters and
Stock as they are now.
"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from emu_the_menu.eaters import EaterRepository
from emu_the_menu.menu_repository import MenuNotFoundError, MenuRepository, SavedMenu
from emu_the_menu.planner import Meal, Portion, choices_for, portions_for, shopping_for
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import analyse
from emu_the_menu.repository import StockRepository
from emu_the_menu.templating import TEMPLATES
from emu_the_menu.web_menu import day_label, days_of, stock_map


def live_meals(saved: SavedMenu, db_path: str | Path) -> tuple[list[Meal], list]:
    """The planner's Meals for a saved Menu and the Ingredients still to buy for them."""
    with EaterRepository(db_path) as eaters, RecipeRepository(db_path) as recipes:
        people = {e.id: e for e in eaters.list()}
        found = {r.id: recipes.get(r.id) for r in recipes.recipes()}
    with StockRepository(db_path) as stock:
        held = stock_map(stock)
    meals = []
    for m in saved.meals:
        recipe = found.get(m.recipe_id) if m.recipe_id is not None else None
        per_serving = None
        if recipe:
            per_serving = analyse(recipe.for_analysis, recipe.servings).calories_per_serving
        portions = tuple(
            Portion(
                people[p.eater_id], p.servings, p.servings * per_serving if per_serving else None
            )
            for p in m.portions
            if p.eater_id in people
        )
        meals.append(Meal(m.day, m.slot, recipe, portions, m.reason, m.id, m.cooked_on))
    return meals, shopping_for(meals, held)


def create_router(db_path: str | Path, today=date.today) -> APIRouter:
    router = APIRouter()

    @router.get("/menus", response_class=HTMLResponse)
    def menus(request: Request):
        with MenuRepository(db_path) as repo:
            listed = repo.menus()
        return TEMPLATES.TemplateResponse(
            request, "menus.html", {"menus": listed, "label": day_label}
        )

    @router.get("/menus/{menu_id}", response_class=HTMLResponse)
    def one(request: Request, menu_id: int):
        with MenuRepository(db_path) as repo:
            saved = repo.get(menu_id)
        if saved is None:
            raise HTTPException(404, "No such menu")
        meals, shopping = live_meals(saved, db_path)
        return TEMPLATES.TemplateResponse(
            request,
            "saved_menu.html",
            {
                "saved": saved,
                "days_shown": days_of(meals),
                "shopping": shopping,
                "change_base": f"/menus/{saved.id}/meals",
                "label": day_label,
            },
        )

    def find_meal(repo: MenuRepository, menu_id: int, meal_id: int):
        saved = repo.get(menu_id)
        meal = next((m for m in saved.meals if m.id == meal_id), None) if saved else None
        if saved is None or meal is None:
            raise HTTPException(404, "No such meal")
        return saved, meal

    def eaters_of(meal, people):
        return [people[p.eater_id] for p in meal.portions if p.eater_id in people]

    @router.get("/menus/{menu_id}/meals/{meal_id}", response_class=HTMLResponse)
    def change_page(request: Request, menu_id: int, meal_id: int, error: str = ""):
        return _change(request, menu_id, meal_id, None)

    def _change(request: Request, menu_id: int, meal_id: int, error: str | None, status=200):
        with MenuRepository(db_path) as repo:
            saved, meal = find_meal(repo, menu_id, meal_id)
        with EaterRepository(db_path) as eaters, RecipeRepository(db_path) as recipes:
            people = {e.id: e for e in eaters.list()}
            everything = recipes.recipes()
        diners = eaters_of(meal, people)
        slots = sorted({m.slot for m in saved.meals if m.day == meal.day})
        options = []
        for recipe in choices_for(everything, diners, meal.slot):
            facts = analyse(recipe.for_analysis, recipe.servings)
            options.append((recipe, facts, portions_for(recipe, diners, meal.slot, slots)))
        return TEMPLATES.TemplateResponse(
            request,
            "change_meal.html",
            {
                "saved": saved,
                "meal": meal,
                "diners": diners,
                "options": options,
                "error": error,
                "label": day_label,
            },
            status_code=status,
        )

    @router.post("/menus/{menu_id}/meals/{meal_id}")
    async def swap(request: Request, menu_id: int, meal_id: int):
        form = await request.form()
        wanted = str(form.get("recipe", ""))
        # Opened here, not injected: a connection must stay in the thread that made it.
        with MenuRepository(db_path) as repo:
            saved, meal = find_meal(repo, menu_id, meal_id)
            with EaterRepository(db_path) as eaters, RecipeRepository(db_path) as recipes:
                people = {e.id: e for e in eaters.list()}
                everything = recipes.recipes()
            diners = eaters_of(meal, people)
            # The same rules as the list, checked again: a posted id is not trusted.
            chosen = next(
                (r for r in choices_for(everything, diners, meal.slot) if str(r.id) == wanted),
                None,
            )
            if chosen is None:
                return _change(
                    request,
                    menu_id,
                    meal_id,
                    "That recipe does not suit everyone at the table",
                    400,
                )
            slots = sorted({m.slot for m in saved.meals if m.day == meal.day})
            portions = portions_for(chosen, diners, meal.slot, slots)
            try:
                repo.set_meal(
                    menu_id, meal_id, chosen.id, [(p.eater.id, p.servings) for p in portions]
                )
            except ValueError as exc:
                return _change(request, menu_id, meal_id, str(exc), 400)
        return RedirectResponse(f"/menus/{menu_id}", status_code=303)

    def cook(menu_id: int, meal_id: int, day: date | None):
        try:
            with MenuRepository(db_path) as repo:
                repo.set_cooked(menu_id, meal_id, day)
        except MenuNotFoundError:
            raise HTTPException(404, "No such meal") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return RedirectResponse(f"/menus/{menu_id}", status_code=303)

    @router.post("/menus/{menu_id}/meals/{meal_id}/cooked")
    def cooked(menu_id: int, meal_id: int):
        """Record that the meal was cooked today. This is what starts the Cooldown."""
        return cook(menu_id, meal_id, today())

    @router.post("/menus/{menu_id}/meals/{meal_id}/not-cooked")
    def not_cooked(menu_id: int, meal_id: int):
        return cook(menu_id, meal_id, None)

    @router.post("/menus/{menu_id}/delete")
    def delete(menu_id: int):
        try:
            with MenuRepository(db_path) as repo:
                repo.delete(menu_id)
        except MenuNotFoundError:
            raise HTTPException(404, "No such menu") from None
        return RedirectResponse("/menus", status_code=303)

    return router
