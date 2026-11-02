from datetime import date, timedelta

import pytest

from emu_the_menu.eaters import Category, EaterRepository
from emu_the_menu.menu_repository import MenuRepository
from emu_the_menu.planner import Meal, Menu, Portion, Slot
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import StockRepository
from emu_the_menu.reserve import free_stock
from emu_the_menu.stock import Location

TODAY = date(2026, 11, 2)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


@pytest.fixture
def world(db_path):
    """One eater, a recipe of 200 g carrots for 2 servings, and 1 kg of carrots in stock."""
    with EaterRepository(db_path) as eaters:
        sam = eaters.add("Sam", Category.MAN)
    with RecipeRepository(db_path) as recipes:
        recipe_id = recipes.add(
            "Carrot soup", 2, (MealType.DINNER,), Weather.ANY, (), [("carrots", 200, "g")]
        )
        recipe = recipes.get(recipe_id)
    with StockRepository(db_path) as stock:
        stock.add("carrots", 1000, "g", Location.PANTRY)
        carrots = stock.ingredients()[0].id
    return sam, recipe, carrots


def save(db_path, world, name="Menu", days=(0,), servings=2.0):
    sam, recipe, _ = world
    meals = tuple(
        Meal(TODAY + timedelta(days=d), Slot.DINNER, recipe, (Portion(sam, servings, None),))
        for d in days
    )
    with MenuRepository(db_path) as menus:
        return menus.save(name, TODAY, len(days), Menu(meals, ()))


def accept(db_path, menu_id):
    with MenuRepository(db_path) as menus:
        menus.accept(menu_id, TODAY)


def left(db_path, world):
    _, _, carrots = world
    lots = free_stock(db_path, TODAY).get(carrots, [])
    return sum(q for q, _ in lots)


def test_with_nothing_accepted_all_the_stock_is_free(db_path, world):
    save(db_path, world)
    assert left(db_path, world) == 1000


def test_an_accepted_menu_holds_what_its_meals_need(db_path, world):
    accept(db_path, save(db_path, world, servings=2.0))  # one whole recipe: 200 g
    assert left(db_path, world) == 800


def test_the_servings_cooked_scale_what_is_held(db_path, world):
    accept(db_path, save(db_path, world, servings=5.0))  # 2.5 recipes: 500 g
    assert left(db_path, world) == 500


def test_every_meal_of_an_accepted_menu_holds_stock(db_path, world):
    accept(db_path, save(db_path, world, days=(0, 1, 2)))
    assert left(db_path, world) == 400


def test_a_cooked_meal_holds_nothing_more(db_path, world):
    menu_id = save(db_path, world, days=(0, 1))
    accept(db_path, menu_id)
    with MenuRepository(db_path) as menus:
        meal = menus.get(menu_id).meals[0]
        menus.set_cooked(menu_id, meal.id, TODAY)
    assert left(db_path, world) == 800  # one meal still to cook; the cooked one took real stock


def test_a_meal_on_a_past_day_that_was_never_cooked_lets_go(db_path, world):
    accept(db_path, save(db_path, world, days=(-1, 0)))
    assert left(db_path, world) == 800


def test_a_menu_that_is_not_accepted_holds_nothing(db_path, world):
    save(db_path, world)
    accept_me = save(db_path, world, name="Other")
    assert left(db_path, world) == 1000
    accept(db_path, accept_me)
    assert left(db_path, world) == 800


def test_unaccepting_gives_the_stock_back(db_path, world):
    menu_id = save(db_path, world)
    accept(db_path, menu_id)
    with MenuRepository(db_path) as menus:
        menus.unaccept(menu_id)
    assert left(db_path, world) == 1000


def test_menus_only_see_what_the_ones_accepted_before_them_hold(db_path, world):
    first = save(db_path, world, name="First")
    second = save(db_path, world, name="Second")
    accept(db_path, first)
    accept(db_path, second)
    _, _, carrots = world
    with MenuRepository(db_path) as menus:
        second_order = menus.get(second).accepted_order
        first_order = menus.get(first).accepted_order
    free_for_second = free_stock(db_path, TODAY, before=second_order)
    free_for_first = free_stock(db_path, TODAY, before=first_order)
    assert sum(q for q, _ in free_for_second[carrots]) == 800
    assert sum(q for q, _ in free_for_first[carrots]) == 1000


def test_a_meal_being_replaced_does_not_hold_stock_against_its_replacement(db_path, world):
    menu_id = save(db_path, world, days=(0, 1))
    accept(db_path, menu_id)
    _, _, carrots = world
    with MenuRepository(db_path) as menus:
        meal = menus.get(menu_id).meals[0]
    lots = free_stock(db_path, TODAY, ignore_meal=meal.id)[carrots]
    assert sum(q for q, _ in lots) == 800  # only the other meal still holds its 200 g


def test_expired_stock_is_not_there_to_hold(db_path, world):
    sam, recipe, carrots = world
    with StockRepository(db_path) as stock:
        stock.add("carrots", 500, "g", Location.FRIDGE, TODAY - timedelta(days=1))
    accept(db_path, save(db_path, world))
    lots = free_stock(db_path, TODAY)[carrots]
    live = sum(q for q, e in lots if e is None or e >= TODAY)
    assert live == 800
