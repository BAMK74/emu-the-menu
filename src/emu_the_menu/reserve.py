"""Stock set aside by accepted Menus.

An accepted Menu holds the Stock its Meals still need, so that a Menu planned afterwards does not
count on it. What is held is worked out fresh each time from the Menu's Meals, so changing a Meal
changes what is held and nothing can get stuck. Menus are served in the order they were accepted,
and within a Menu the Meals in date order. A Meal that is cooked has taken real Stock already, and
a Meal on a past day that was never cooked no longer holds anything.
"""

from datetime import date
from pathlib import Path

from emu_the_menu.cooking import Pantry, uses_for
from emu_the_menu.menu_repository import MenuRepository
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.repository import StockRepository

Lots = dict[int, list[tuple[float, date | None]]]


def stock_lots(stock: StockRepository) -> Lots:
    """Ingredient id to what is held: (quantity, expiry or None) for each stock item."""
    ids = {i.name: i.id for i in stock.ingredients()}
    lots: Lots = {}
    for item in stock.list_items():
        if item.quantity > 0 and item.ingredient in ids:
            lots.setdefault(ids[item.ingredient], []).append((item.quantity, item.expires_on))
    return lots


def free_stock(
    db_path: str | Path,
    today: date,
    before: int | None = None,
    ignore_meal: int | None = None,
) -> Lots:
    """The Stock not held by accepted Menus.

    `before` counts only Menus accepted earlier than that place in line (what a Menu may itself
    use), and `ignore_meal` leaves one Meal out (the one being replaced).
    """
    with StockRepository(db_path) as stock:
        lots = stock_lots(stock)
    with MenuRepository(db_path) as menus:
        accepted = [
            m
            for m in menus.accepted_menus()
            if before is None or (m.accepted_order is not None and m.accepted_order < before)
        ]
    if not accepted:
        return lots
    with RecipeRepository(db_path) as recipes:
        found = {}
        pantry = Pantry(lots)
        for menu in accepted:
            for meal in menu.meals:
                if (
                    meal.recipe_id is None
                    or meal.cooked_on is not None
                    or meal.day < today
                    or meal.id == ignore_meal
                ):
                    continue
                if meal.recipe_id not in found:
                    found[meal.recipe_id] = recipes.get(meal.recipe_id)
                recipe = found[meal.recipe_id]
                if recipe is None:
                    continue
                servings = sum(p.servings for p in meal.portions) + meal.guest_servings
                held = {
                    i.id: pantry.available(i.id, meal.day) for slot in recipe.slots for i in slot
                }
                for use in uses_for(recipe, servings / recipe.servings, held):
                    pantry.take(use.ingredient.id, use.taken, meal.day)
    return pantry.export()
