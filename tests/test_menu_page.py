import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.allergens import Allergen
from emu_the_menu.eaters import Category, EaterRepository
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import StockRepository
from emu_the_menu.stock import Location
from emu_the_menu.web import create_app

TODAY = date(2026, 11, 2)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


@pytest.fixture
def client(db_path):
    app = create_app(db_path, today=lambda: TODAY, fetch_product=lambda code: {"status": 0})
    return TestClient(app, follow_redirects=False)


def text_of(html):
    html = re.sub(r"<(style|script)\b.*?</\1>", "", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def add_eater(db_path, name="Sam", **kwargs):
    with EaterRepository(db_path) as repo:
        return repo.add(name, Category.MAN, **kwargs)


def add_recipe(db_path, name, lines, meals=(MealType.DINNER,), servings=1, weather=Weather.ANY):
    with RecipeRepository(db_path) as repo:
        return repo.add(name, servings, meals, weather, (), lines)


def review(db_path, name, kcal=100, groups=(), allergens=()):
    with StockRepository(db_path) as stock:
        found = next(i for i in stock.ingredients() if i.name == name)
        stock.set_ingredient_details(found.id, kcal, groups, allergens)


def page_text(client, query=""):
    return text_of(client.get("/menu" + query).text)


# --- the page ---------------------------------------------------------------------------------


def test_the_header_links_to_the_menu_and_marks_the_page(client):
    for path in ("/", "/recipes", "/eaters", "/menu"):
        assert 'href="/menu"' in client.get(path).text
    marked = re.findall(r'<a href="([^"]+)"[^>]*aria-current="page"', client.get("/menu").text)
    assert marked == ["/menu"]


def test_without_eaters_it_asks_for_them_first(client):
    assert "Add the people you cook for" in page_text(client)


def test_a_week_of_three_meals_is_planned_by_default(client, db_path):
    add_eater(db_path)
    page = client.get("/menu").text
    assert len(re.findall(r'class="meal"', page)) == 21
    assert page.count('class="day"') == 7


def test_with_no_recipes_every_meal_says_why(client, db_path):
    add_eater(db_path)
    assert "No recipes are tagged for dinner" in page_text(client)


# --- a recipe in the plan ---------------------------------------------------------------------


def test_a_recipe_is_placed_with_each_eaters_portion(client, db_path):
    add_eater(db_path, "Sam")
    add_recipe(db_path, "Rice bowl", [("rice", 1000, "g")], servings=2)
    review(db_path, "rice", 100)  # 500 kcal per serving
    page = page_text(client, "?meal_types=dinner&days=1&go=1&eater=1")
    assert "Rice bowl" in page
    assert re.search(r"Sam \d+(\.\d+)? servings? \d+ kcal", page)


def test_only_the_ticked_eaters_are_planned_for(client, db_path):
    sam = add_eater(db_path, "Sam")
    anna = add_eater(db_path, "Anna", allergens=[Allergen.MILK])
    add_recipe(db_path, "Cheesy", [("cheese", 100, "g")])
    review(db_path, "cheese", 300, [FoodGroup.DAIRY], [Allergen.MILK])
    both = f"?meal_types=dinner&days=1&go=1&eater={sam.id}&eater={anna.id}"
    assert "Cheesy" not in page_text(client, both)
    assert "No recipe suits everyone" in page_text(client, both)
    only_sam = f"?meal_types=dinner&days=1&go=1&eater={sam.id}"
    assert "Cheesy" in page_text(client, only_sam)


def test_recipes_with_unchecked_ingredients_are_flagged_in_the_plan(client, db_path):
    add_eater(db_path)
    add_recipe(db_path, "Mystery", [("sauce", 100, "g")])
    page = page_text(client, "?meal_types=dinner&days=1&go=1&eater=1")
    assert "Mystery" in page
    assert "not checked: sauce" in page.lower()


def test_the_plan_always_says_to_read_the_labels(client, db_path):
    add_eater(db_path)
    assert "always read the label" in page_text(client).lower()


# --- shopping and stock -----------------------------------------------------------------------


def test_the_shopping_list_has_what_stock_lacks(client, db_path):
    add_eater(db_path)
    add_recipe(db_path, "Rice and beans", [("rice", 100, "g"), ("beans", 100, "g")])
    with StockRepository(db_path) as stock:
        stock.add("rice", 1, "kg", Location.PANTRY)
    page = page_text(client, "?meal_types=dinner&days=1&go=1&eater=1")
    shopping = page.split("Shopping list")[1]
    assert "beans" in shopping and "rice" not in shopping.split("Always read")[0]


def test_the_shopping_allowance_is_a_setting(client, db_path):
    add_eater(db_path)
    add_recipe(db_path, "Big", [(f"item{n}", 10, "g") for n in range(4)])
    base = "?meal_types=dinner&days=1&go=1&eater=1"
    assert "Shopping Allowance" in page_text(client, base + "&allowance=2")
    assert "Shopping Allowance" not in page_text(client, base + "&allowance=4")


def test_the_cooldown_is_a_setting(client, db_path):
    add_eater(db_path)
    add_recipe(db_path, "Only one", [("rice", 100, "g")])
    base = "?meal_types=dinner&days=2&go=1&eater=1&allowance=9"
    assert "Cooldown:" in page_text(client, base + "&cooldown=14")
    assert "Cooldown:" not in page_text(client, base + "&cooldown=0")


# --- reshuffling and the form -----------------------------------------------------------------


def test_the_same_seed_gives_the_same_page_and_the_form_keeps_its_values(client, db_path):
    add_eater(db_path)
    for n in range(6):
        add_recipe(db_path, f"Dish {n}", [(f"stuff{n}", 100, "g")])
    query = "?meal_types=dinner&days=4&go=1&eater=1&seed=5&allowance=20"
    assert client.get("/menu" + query).text == client.get("/menu" + query).text
    page = client.get("/menu" + query).text
    assert (
        'name="days" type="number" min="1" max="28" step="1" inputmode="numeric" value="4"' in page
    )


def test_a_reshuffle_link_changes_the_seed_and_keeps_the_settings(client, db_path):
    add_eater(db_path)
    page = client.get("/menu?meal_types=dinner&days=3&go=1&eater=1&seed=5").text
    assert re.search(r'href="/menu\?[^"]*seed=\d+', page)
    link = re.search(r'href="(/menu\?[^"]*)"[^>]*>Reshuffle', page).group(1)
    assert "seed=5" not in link and "days=3" in link and "meal_types=dinner" in link


@pytest.mark.parametrize(
    "query, message",
    [
        ("?go=1&days=0&eater=1&meal_types=dinner", "days"),
        ("?go=1&days=99&eater=1&meal_types=dinner", "days"),
        ("?go=1&days=x&eater=1&meal_types=dinner", "days"),
        ("?go=1&start=notadate&eater=1&meal_types=dinner", "date"),
        ("?go=1&eater=1", "meal type"),
        ("?go=1&meal_types=dinner", "who"),
        ("?go=1&eater=1&meal_types=brunch", "meal type"),
        ("?go=1&eater=1&meal_types=dinner&allowance=-1", "allowance"),
        ("?go=1&eater=1&meal_types=dinner&cooldown=500", "cooldown"),
    ],
)
def test_bad_settings_are_explained_and_no_plan_is_made(client, db_path, query, message):
    add_eater(db_path)
    response = client.get("/menu" + query)
    assert response.status_code == 400
    assert message in text_of(response.text).lower()
    assert 'class="meal"' not in response.text


def test_the_menu_starts_today_unless_told_otherwise(client, db_path):
    add_eater(db_path)
    assert "Mon 2 Nov" in page_text(client)
    assert "Wed 4 Nov" in page_text(
        client, "?go=1&eater=1&meal_types=dinner&start=2026-11-04&days=1"
    )


def test_the_meal_says_how_much_to_cook(client, db_path):
    add_eater(db_path, "Sam")
    add_recipe(db_path, "Rice bowl", [("rice", 1000, "g")], servings=2)
    review(db_path, "rice", 100)
    page = page_text(client, "?meal_types=dinner&days=1&go=1&eater=1")
    assert re.search(r"Cook \d+(\.\d+)? servings \(\d+(\.\d+)?× the recipe\)", page)


# --- snacks -----------------------------------------------------------------------------------


def test_the_form_offers_three_snack_slots_and_they_are_off_by_default(client, db_path):
    add_eater(db_path)
    page = client.get("/menu").text
    for value in ("morning-snack", "afternoon-snack", "evening-snack"):
        assert re.search(rf'name="meal_types" value="{value}"(?! checked)', page)
    assert len(re.findall(r'class="meal"', page)) == 21


def test_ticked_snack_slots_are_planned_with_snack_recipes_and_labelled(client, db_path):
    add_eater(db_path)
    add_recipe(db_path, "Apple slices", [("apples", 150, "g")], meals=(MealType.SNACK,))
    add_recipe(db_path, "Nuts", [("nuts", 30, "g")], meals=(MealType.SNACK,))
    query = (
        "?go=1&days=1&eater=1&cooldown=0&allowance=9"
        "&meal_types=morning-snack&meal_types=afternoon-snack&meal_types=evening-snack"
    )
    page = page_text(client, query)
    for label in ("Morning snack", "Afternoon snack", "Evening snack"):
        assert label in page
    assert "Apple slices" in page or "Nuts" in page
    assert "No recipes are tagged" not in page


def test_slots_appear_in_the_order_of_the_day(client, db_path):
    add_eater(db_path)
    query = (
        "?go=1&days=1&eater=1&meal_types=evening-snack&meal_types=dinner"
        "&meal_types=breakfast&meal_types=morning-snack&meal_types=lunch&meal_types=afternoon-snack"
    )
    labels = re.findall(r'class="meal-type">([^<]+)<', client.get("/menu" + query).text)
    assert labels == [
        "Breakfast", "Morning snack", "Lunch", "Afternoon snack", "Dinner", "Evening snack"
    ]  # fmt: skip


def test_the_reshuffle_link_keeps_the_snack_slots(client, db_path):
    add_eater(db_path)
    page = client.get("/menu?go=1&days=1&eater=1&meal_types=morning-snack&seed=2").text
    link = re.search(r'href="(/menu\?[^"]*)"[^>]*>Reshuffle', page).group(1)
    assert "meal_types=morning-snack" in link
