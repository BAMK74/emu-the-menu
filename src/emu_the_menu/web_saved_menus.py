"""Saved Menus: the list, one Menu, and deleting it.

A saved Menu holds only which Recipe is in each Meal and each person's servings. Everything shown
(names, calories, "Not checked", the shopping list) is worked out from the Recipes, Eaters and
Stock as they are now.
"""

import random
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from emu_the_menu.cooking import Pantry, shopping_amounts, uses_for
from emu_the_menu.eaters import EaterRepository
from emu_the_menu.menu_repository import MenuNotFoundError, MenuRepository, SavedMenu
from emu_the_menu.occasion_repository import occasions_for
from emu_the_menu.occasions import MAX_HEADCOUNT
from emu_the_menu.planner import (
    Meal,
    Portion,
    choices_for,
    guest_servings_for,
    guests_for,
    plan,
    portions_for,
)
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import analyse
from emu_the_menu.repository import StockRepository
from emu_the_menu.reserve import free_stock, overlapping, stock_lots
from emu_the_menu.templating import TEMPLATES
from emu_the_menu.units import display_quantity
from emu_the_menu.weather import http_get_json
from emu_the_menu.weather_store import kinds, weather_for
from emu_the_menu.web_menu import day_label, days_of, held_text, shopping_text


def live_meals(saved: SavedMenu, database: str, today: date) -> tuple[list[Meal], list]:
    """The planner's Meals for a saved Menu and the Ingredients still to buy for them.

    An accepted Menu may use the Stock left after the Menus accepted before it; one that is not
    accepted may use what no accepted Menu is holding.
    """
    with EaterRepository(database) as eaters, RecipeRepository(database) as recipes:
        people = {e.id: e for e in eaters.list()}
        found = {r.id: recipes.get(r.id) for r in recipes.recipes()}
    held = free_stock(database, today, before=saved.accepted_order)
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
        meals.append(
            Meal(
                m.day, m.slot, recipe, portions, m.reason, m.guests, m.guest_servings,
                m.id, m.cooked_on, m.short,
            )
        )  # fmt: skip
    return meals, shopping_text(shopping_amounts(meals, held))


def create_router(database: str, today=date.today, get_json=http_get_json) -> APIRouter:
    router = APIRouter()

    @router.get("/menus", response_class=HTMLResponse)
    def menus(request: Request):
        with MenuRepository(database) as repo:
            listed = repo.menus()
            accepted = repo.accepted_menus()
        clashes = {m.id: [o.name for o in overlapping(m, accepted, today())] for m in accepted}
        return TEMPLATES.TemplateResponse(
            request, "menus.html", {"menus": listed, "label": day_label, "clashes": clashes}
        )

    @router.get("/menus/{menu_id}", response_class=HTMLResponse)
    def one(request: Request, menu_id: int):
        return show(request, menu_id)

    def show(request: Request, menu_id: int, error: str | None = None, status: int = 200):
        with MenuRepository(database) as repo:
            saved = repo.get(menu_id)
            accepted = repo.accepted_menus()
        if saved is None:
            raise HTTPException(404, "No such menu")
        meals, shopping = live_meals(saved, database, today())
        clashes = overlapping(saved, accepted, today()) if saved.accepted_on else []
        return TEMPLATES.TemplateResponse(
            request,
            "saved_menu.html",
            {
                "saved": saved,
                "days_shown": days_of(meals),
                "forecast": weather_for(database, today(), get_json),
                "occasions": occasions_for(database, saved.start, saved.days),
                "shopping": shopping,
                "clashes": clashes,
                "holds": held_text(database, today(), saved.accepted_order),
                "holds_for": "this menu",
                "change_base": f"/menus/{saved.id}/meals",
                "label": day_label,
                "error": error,
            },
            status_code=status,
        )

    def find_meal(repo: MenuRepository, menu_id: int, meal_id: int):
        saved = repo.get(menu_id)
        meal = next((m for m in saved.meals if m.id == meal_id), None) if saved else None
        if saved is None or meal is None:
            raise HTTPException(404, "No such meal")
        return saved, meal

    def table_of(saved, meal, people):
        """Who a Meal is for. Older Menus kept nobody for an empty Meal, so the people of the
        Menu's other Meals stand in; with nobody known, nothing may be chosen for it."""
        ids = [p.eater_id for p in meal.portions] or sorted(
            {p.eater_id for m in saved.meals for p in m.portions}
        )
        return [people[i] for i in ids if i in people]

    @router.get("/menus/{menu_id}/meals/{meal_id}", response_class=HTMLResponse)
    def change_page(request: Request, menu_id: int, meal_id: int, error: str = ""):
        return _change(request, menu_id, meal_id, None)

    def _change(request: Request, menu_id: int, meal_id: int, error: str | None, status=200):
        with MenuRepository(database) as repo:
            saved, meal = find_meal(repo, menu_id, meal_id)
        with EaterRepository(database) as eaters, RecipeRepository(database) as recipes:
            people = {e.id: e for e in eaters.list()}
            everything = recipes.recipes()
        diners = table_of(saved, meal, people)
        slots = sorted({m.slot for m in saved.meals if m.day == meal.day})
        occasion = occasions_for(database, meal.day, 1).get(meal.day)
        options = []
        for recipe in choices_for(everything, diners, meal.slot) if diners else []:
            facts = analyse(recipe.for_analysis, recipe.servings)
            options.append(
                (recipe, facts, portions_for(recipe, diners, meal.slot, slots, occasion))
            )
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
        with MenuRepository(database) as repo:
            saved, meal = find_meal(repo, menu_id, meal_id)
            with EaterRepository(database) as eaters, RecipeRepository(database) as recipes:
                people = {e.id: e for e in eaters.list()}
                everything = recipes.recipes()
            diners = table_of(saved, meal, people)
            # The same rules as the list, checked again: a posted id is not trusted.
            chosen = next(
                (
                    r
                    for r in (choices_for(everything, diners, meal.slot) if diners else [])
                    if str(r.id) == wanted
                ),
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
            occasion = occasions_for(database, meal.day, 1).get(meal.day)
            portions = portions_for(chosen, diners, meal.slot, slots, occasion)
            guests, guest_servings = guests_for(chosen, diners, meal.slot, slots, occasion)
            try:
                repo.set_meal(
                    menu_id,
                    meal_id,
                    chosen.id,
                    [(p.eater.id, p.servings) for p in portions],
                    guests,
                    guest_servings,
                )
            except ValueError as exc:
                return _change(request, menu_id, meal_id, str(exc), 400)
        return RedirectResponse(f"/menus/{menu_id}", status_code=303)

    @router.post("/menus/{menu_id}/meals/{meal_id}/reshuffle")
    def reshuffle(request: Request, menu_id: int, meal_id: int):
        """Pick another recipe for one meal, by the planner's own rules and scoring.

        The planner is run for that day alone with the current recipe left out, so the result
        suits everyone at the table, respects the Cooldown (from cooked meals) and prefers what
        is in Stock. A new seed each time means pressing it again gives another.
        """
        with MenuRepository(database) as repo:
            saved, meal = find_meal(repo, menu_id, meal_id)
            if meal.cooked_on is not None:
                raise HTTPException(400, "A cooked meal cannot be changed")
            with EaterRepository(database) as eaters, RecipeRepository(database) as recipes:
                people = {e.id: e for e in eaters.list()}
                everything = recipes.recipes()
            diners = table_of(saved, meal, people)
            others = [r for r in everything if r.id != meal.recipe_id]
            day_slots = sorted({m.slot for m in saved.meals if m.day == meal.day})
            picked = None
            if diners:
                held = free_stock(database, today(), ignore_meal=meal.id)
                result = plan(
                    meal.day,
                    1,
                    day_slots,
                    others,
                    diners,
                    held,
                    seed=random.SystemRandom().randrange(10**9),
                    last_served=repo.last_served(),
                    weather=kinds(weather_for(database, today(), get_json)),
                    occasions=occasions_for(database, meal.day, 1),
                )
                picked = next((m for m in result.meals if m.slot is meal.slot), None)
            if picked is None or picked.recipe is None:
                why = picked.reason if picked else "Nobody is recorded as eating this meal"
                return _change(request, menu_id, meal_id, f"No other recipe fits: {why}", 400)
            repo.set_meal(
                menu_id,
                meal_id,
                picked.recipe.id,
                [(p.eater.id, p.servings) for p in picked.portions],
                picked.guests,
                picked.guest_servings,
            )
        return RedirectResponse(f"/menus/{menu_id}", status_code=303)

    def cook(menu_id: int, meal_id: int, day: date | None):
        try:
            with MenuRepository(database) as repo:
                repo.set_cooked(menu_id, meal_id, day)
        except MenuNotFoundError:
            raise HTTPException(404, "No such meal") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return RedirectResponse(f"/menus/{menu_id}", status_code=303)

    @router.post("/menus/{menu_id}/meals/{meal_id}/guests")
    async def set_guests(request: Request, menu_id: int, meal_id: int):
        """Cook a Meal for more (or fewer) guests, each an average adult as with Occasions.

        Only the Meal's guests change: the shopping list, what an accepted Menu holds and what
        cooking takes from Stock all follow from its servings.
        """
        form = await request.form()
        # The "Remove guests" button sends the Meal back to the household only.
        typed = "0" if form.get("remove") else str(form.get("guests", "")).strip()
        with MenuRepository(database) as repo:
            saved, meal = find_meal(repo, menu_id, meal_id)
            if not typed.isdigit() or int(typed) > MAX_HEADCOUNT:
                message = f"Guests must be a whole number from 0 to {MAX_HEADCOUNT}"
                return show(request, menu_id, message, 400)
            if meal.recipe_id is None:
                return show(request, menu_id, "Choose a recipe for the meal first", 400)
            with RecipeRepository(database) as recipes:
                recipe = recipes.get(meal.recipe_id)
            slots = sorted({m.slot for m in saved.meals if m.day == meal.day})
            occasion = occasions_for(database, meal.day, 1).get(meal.day)
            servings = guest_servings_for(recipe, int(typed), meal.slot, slots, occasion)
            try:
                repo.set_guests(menu_id, meal_id, int(typed), servings)
            except ValueError as exc:
                return show(request, menu_id, str(exc), 400)
        return RedirectResponse(f"/menus/{menu_id}", status_code=303)

    @router.post("/menus/{menu_id}/meals/{meal_id}/cooked")
    def cooked(menu_id: int, meal_id: int):
        """Record that the meal was cooked today and take what it used from Stock.

        This is what starts the Cooldown. Stock is used as far as it goes and what it could not
        cover is kept with the meal; cooking is never refused for lack of Stock.
        """
        with MenuRepository(database) as repo:
            saved = repo.get(menu_id)
            meal = next((m for m in saved.meals if m.id == meal_id), None) if saved else None
            if meal is None:
                raise HTTPException(404, "No such meal")
            if meal.cooked_on is not None:
                return RedirectResponse(f"/menus/{menu_id}", status_code=303)  # stock only once
            if meal.recipe_id is None:
                raise HTTPException(400, "A meal with no recipe cannot be cooked")
            with RecipeRepository(database) as recipes:
                recipe = recipes.get(meal.recipe_id)
            scale = (sum(p.servings for p in meal.portions) + meal.guest_servings) / recipe.servings
            short = []
            took = []
            with StockRepository(database) as stock:
                pantry = Pantry(stock_lots(stock))
                today_ = today()
                held = {i.id: pantry.available(i.id, today_) for g in recipe.slots for i in g}
                for use in uses_for(recipe, scale, held):
                    unit = use.ingredient.base_unit
                    lots = stock.take_available(
                        use.ingredient.name, use.needed, unit.value, on=today_
                    )
                    took.extend(lots)
                    got = sum(lot.quantity for lot in lots)
                    if use.needed - got > 1e-6:
                        amount, label = display_quantity(use.needed - got, unit)
                        short.append(f"{use.ingredient.name} {amount:g} {label}".strip())
            repo.set_cooked(menu_id, meal_id, today_, "; ".join(short), took)
        return RedirectResponse(f"/menus/{menu_id}", status_code=303)

    @router.post("/menus/{menu_id}/meals/{meal_id}/not-cooked")
    def not_cooked(menu_id: int, meal_id: int):
        """Undo cooking: the Stock the meal took goes back where it came from."""
        try:
            with MenuRepository(database) as repo:
                lots = repo.taken(menu_id, meal_id)
                if lots:
                    with StockRepository(database) as stock:
                        for lot in lots:
                            stock.put_back(lot)
                repo.set_cooked(menu_id, meal_id, None)
        except MenuNotFoundError:
            raise HTTPException(404, "No such meal") from None
        return RedirectResponse(f"/menus/{menu_id}", status_code=303)

    def set_accepted(menu_id: int, accept: bool):
        try:
            with MenuRepository(database) as repo:
                if accept:
                    repo.accept(menu_id, today())
                else:
                    repo.unaccept(menu_id)
        except MenuNotFoundError:
            raise HTTPException(404, "No such menu") from None
        return RedirectResponse(f"/menus/{menu_id}", status_code=303)

    @router.post("/menus/{menu_id}/accept")
    def accept(menu_id: int):
        """Accept the menu: its meals still to cook set aside the stock they need."""
        return set_accepted(menu_id, True)

    @router.post("/menus/{menu_id}/unaccept")
    def unaccept(menu_id: int):
        return set_accepted(menu_id, False)

    @router.post("/menus/{menu_id}/delete")
    def delete(menu_id: int):
        try:
            with MenuRepository(database) as repo:
                repo.delete(menu_id)
        except MenuNotFoundError:
            raise HTTPException(404, "No such menu") from None
        return RedirectResponse("/menus", status_code=303)

    return router
