"""How Occasions change the Meals of a day: extra calories, guests and tags."""

import itertools
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.eaters import Category, Eater, EaterRepository
from emu_the_menu.menu_repository import MenuRepository
from emu_the_menu.occasion_repository import OccasionRepository
from emu_the_menu.occasions import Occasion
from emu_the_menu.planner import PlanSettings, Slot, plan
from emu_the_menu.recipe_repository import Recipe, RecipeLine, RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import Ingredient, StockRepository
from emu_the_menu.stock import Location
from emu_the_menu.units import BaseUnit
from emu_the_menu.web import create_app

TODAY = date(2026, 11, 2)
NEXT = date(2026, 11, 3)
_ids = itertools.count(100)
MEAT = Ingredient("beef", BaseUnit.GRAM, id=1, kcal=100, reviewed=True)  # 100 kcal per 100 g


def stew(name="Stew", tags=()):
    """500 kcal a serving."""
    return Recipe(
        next(_ids), name, 1, (MealType.DINNER,), Weather.ANY, tuple(tags), (RecipeLine(MEAT, 500),)
    )


def sam(goal=2000, name="Sam"):
    return Eater(next(_ids), name, Category.MAN, default_goal=goal)


def run(recipes, eaters, occasions=None, days=2, seed=0):
    return plan(
        TODAY,
        days,
        [Slot.DINNER],
        recipes,
        eaters,
        {},
        seed=seed,
        settings=PlanSettings(shopping_allowance=99, cooldown_days=0),
        occasions=occasions,
    )


def occasion(**kwargs):
    return Occasion("Party", NEXT, **kwargs)


def test_without_an_occasion_nothing_changes():
    first, second = run([stew()], [sam()]).meals
    assert first.total_servings == second.total_servings == 4 and first.guests == 0


def test_extra_calories_raise_that_days_goal_only():
    first, second = run([stew()], [sam()], {NEXT: occasion(extra_percent=25)}).meals
    assert first.portions[0].servings == 4
    assert second.portions[0].servings == 5  # 2500 kcal / 500


def test_guests_are_cooked_for_as_average_adults():
    first, second = run([stew()], [sam()], {NEXT: occasion(headcount=3)}).meals
    assert first.guests == 0 and first.guest_servings == 0
    assert second.guests == 2
    assert second.guest_servings == 8  # two guests, 2000 kcal each, 4 servings each
    assert second.total_servings == 12 and second.scale == 12


def test_guests_get_the_extra_calories_too():
    _, second = run([stew()], [sam()], {NEXT: occasion(headcount=2, extra_percent=20)}).meals
    assert second.portions[0].servings == 4.75  # 2400 kcal / 500 = 4.8, in quarters of a serving
    assert second.guest_servings == 4.75


def test_a_headcount_no_bigger_than_the_household_adds_no_guests():
    two = [sam(), sam(name="Pat")]
    for headcount in (None, 1, 2):
        _, second = run([stew()], two, {NEXT: occasion(headcount=headcount)}).meals
        assert second.guests == 0 and second.guest_servings == 0


def test_without_calories_a_guest_is_one_serving():
    unknown = Ingredient("mystery", BaseUnit.GRAM, id=2, reviewed=True)  # no calories known
    plain = Recipe(
        next(_ids), "Mystery", 1, (MealType.DINNER,), Weather.ANY, (), (RecipeLine(unknown, 100),)
    )
    _, second = run([plain], [sam()], {NEXT: occasion(headcount=4)}).meals
    assert second.guests == 3 and second.guest_servings == 3


@pytest.mark.parametrize("seed", range(8))
def test_a_recipe_with_the_occasions_tag_is_favoured_that_day(seed):
    plain, festive = stew("Plain"), stew("Feast", tags=["festive"])
    _, second = run([plain, festive], [sam()], {NEXT: occasion(tags=("Festive",))}, seed=seed).meals
    assert second.recipe.name == "Feast"


def test_tags_match_ignoring_plurals():
    plain, bbq = stew("Plain"), stew("Grill", tags=["barbecue"])
    for seed in range(5):
        _, second = run(
            [plain, bbq], [sam()], {NEXT: occasion(tags=("barbecues",))}, seed=seed
        ).meals
        assert second.recipe.name == "Grill"


def test_other_days_are_not_pushed_by_the_tags():
    seen = {
        run(
            [stew("Plain"), stew("Feast", tags=["festive"])],
            [sam()],
            {NEXT: occasion(tags=("festive",))},
            seed=s,
        )
        .meals[0]
        .recipe.name
        for s in range(12)
    }
    assert seen == {"Plain", "Feast"}  # day one is still a coin toss


# ---- saved and shown ----


@pytest.fixture
def client(db_url):
    app = create_app(db_url, today=lambda: TODAY, fetch_product=lambda code: {"status": 0})
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def kitchen(db_url):
    """Sam (goal 2500), a 500 kcal stew, and a party of 3 tomorrow."""
    with EaterRepository(db_url) as eaters:
        eater_id = eaters.add("Sam", Category.MAN).id
    with RecipeRepository(db_url) as recipes:
        recipes.add("Stew", 1, (MealType.DINNER,), Weather.ANY, (), [("beef", 500, "g")])
    with StockRepository(db_url) as stock:
        stock.add("beef", 20000, "g", Location.FREEZER)
        beef = next(i for i in stock.ingredients() if i.name == "beef")
        stock.set_ingredient_details(beef.id, 100, (), ())
    with OccasionRepository(db_url) as occasions:
        occasions.add("Birthday party", NEXT, 3)
    return eater_id


def text_of(html):
    html = re.sub(r"<(style|script)\b.*?</\1>", "", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def query(eater):
    return f"go=1&start=2026-11-02&days=2&meal_types=dinner&eater={eater}&seed=0&cooldown=0"


def test_the_menu_page_names_the_occasion_and_the_guests(client, kitchen):
    text = text_of(client.get("/menu?" + query(kitchen)).text)
    assert "Birthday party" in text
    assert "Includes 2 guests (8 servings)" in text and "ask them before you cook" in text
    assert "Cook 13 servings" in text and "Cook 5 servings" in text


def test_saving_keeps_the_guests_and_the_saved_page_shows_them(client, db_url, kitchen):
    saved = client.post(
        "/menu/save",
        data={"go": "1", "start": "2026-11-02", "days": "2", "meal_types": "dinner",
              "eater": str(kitchen), "seed": "0", "cooldown": "0"},
    )  # fmt: skip
    location = saved.headers["location"]
    with MenuRepository(db_url) as menus:
        meals = menus.get(int(location.rsplit("/", 1)[1])).meals
    assert [(m.guests, m.guest_servings) for m in meals] == [(0, 0), (2, 8)]
    text = text_of(client.get(location).text)
    assert "Birthday party" in text and "Includes 2 guests (8 servings)" in text
    assert "Cook 13 servings" in text


def make_menu(client, kitchen):
    return client.post(
        "/menu/save",
        data={"go": "1", "start": "2026-11-02", "days": "2", "meal_types": "dinner",
              "eater": str(kitchen), "seed": "0", "cooldown": "0"},
    ).headers["location"]  # fmt: skip


def test_cooking_a_meal_with_guests_uses_stock_for_all_of_them(client, db_url, kitchen):
    location = make_menu(client, kitchen)
    menu_id = int(location.rsplit("/", 1)[1])
    with MenuRepository(db_url) as menus:
        party = menus.get(menu_id).meals[1]
    client.post(f"/menus/{menu_id}/meals/{party.id}/cooked")
    with StockRepository(db_url) as stock:
        left = sum(item.quantity for item in stock.list_items() if item.ingredient == "beef")
    assert left == 20000 - 13 * 500


def test_the_shopping_list_counts_the_guests(client, db_url, kitchen):
    with StockRepository(db_url) as stock:
        item = next(i for i in stock.list_items() if i.ingredient == "beef")
        stock.set_quantity(item.id, 6000, "g")  # 5 servings (2500 g) fit, but not 5 + 13
    text = text_of(client.get("/menu?" + query(kitchen)).text)
    assert "beef 3 kg" in text  # 18 servings x 500 g = 9 kg, 6 kg held


def test_accepting_holds_stock_for_the_guests_too(client, db_url, kitchen):
    from emu_the_menu.reserve import free_stock

    location = make_menu(client, kitchen)
    menu_id = int(location.rsplit("/", 1)[1])
    client.post(f"/menus/{menu_id}/accept")
    lots = free_stock(db_url, TODAY)
    with StockRepository(db_url) as stock:
        beef = next(i for i in stock.ingredients() if i.name == "beef")
    held = sum(quantity for quantity, _ in lots[beef.id])
    assert held == 20000 - 18 * 500


def test_changing_a_meal_recomputes_guests(client, db_url, kitchen):
    with RecipeRepository(db_url) as recipes:
        recipes.add("Light stew", 1, (MealType.DINNER,), Weather.ANY, (), [("beef", 250, "g")])
    location = make_menu(client, kitchen)
    menu_id = int(location.rsplit("/", 1)[1])
    with MenuRepository(db_url) as menus:
        party = menus.get(menu_id).meals[1]
        light = next(r for r in RecipeRepository(db_url).recipes() if r.name == "Light stew")
    page = client.get(f"/menus/{menu_id}/meals/{party.id}")
    assert page.status_code == 200
    client.post(f"/menus/{menu_id}/meals/{party.id}", data={"recipe": str(light.id)})
    with MenuRepository(db_url) as menus:
        changed = menus.get(menu_id).meals[1]
    assert changed.recipe_id == light.id
    assert changed.guests == 2 and changed.guest_servings == 16  # 250 kcal servings, 8 each
