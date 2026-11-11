"""The drawings beside stock items, recipes and meals. They are decoration picked from words in a
name, so they never say anything about allergens or diets."""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.illustrations import food_icon, recipe_icon, slot_icon
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import StockRepository
from emu_the_menu.stock import Location
from emu_the_menu.web import create_app


@pytest.mark.parametrize(
    ("name", "icon"),
    [
        ("carrots", "veg"),
        ("chopped tomatoes", "veg"),
        ("apples", "fruit"),
        ("basmati rice", "grain"),
        ("cheddar cheese", "dairy"),
        ("eggs", "egg"),
        ("salmon fillet", "fish"),
        ("chicken stock", "meat"),
        ("sourdough bread", "bread"),
        ("fresh basil", "herb"),
        ("baking powder", "jar"),
    ],
)
def test_a_food_gets_the_drawing_its_name_suggests(name, icon):
    assert food_icon(name) == icon


def test_a_recipe_takes_the_drawing_of_its_heartiest_ingredient():
    assert recipe_icon(["onion", "beef mince", "rice"]) == "meat"
    assert recipe_icon(["lettuce", "lemon"]) == "veg"
    assert recipe_icon(["baking powder"]) == "bowl"
    assert recipe_icon([]) == "bowl"


def test_a_place_in_the_day_has_its_own_drawing():
    assert slot_icon("breakfast") == "sunrise"
    assert slot_icon("dinner") == "pot"
    assert slot_icon("tea") == "bowl"


def off(code):
    return {"status": 0}


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


@pytest.fixture
def client(db_path):
    app = create_app(db_path, today=lambda: date(2026, 10, 10), fetch_product=off)
    return TestClient(app, follow_redirects=False)


def icons(page):
    return re.findall(r'<svg class="ico"[^>]*><use href="/static/art\.svg#i-(\w+)"/></svg>', page)


def test_stock_items_and_recipes_carry_their_drawings(client, db_path):
    with StockRepository(db_path) as stock:
        stock.add("carrots", 500, "g", Location.PANTRY)
        stock.add("milk", 1, "l", Location.FRIDGE)
    with RecipeRepository(db_path) as recipes:
        recipes.add("Carrot soup", 2, (MealType.DINNER,), Weather.ANY, (), [("carrots", 200, "g")])
        recipes.add("Omelette", 1, (MealType.LUNCH,), Weather.ANY, (), [("eggs", 2, "count")])
    assert sorted(icons(client.get("/").text)) == ["dairy", "veg"]
    assert sorted(icons(client.get("/recipes").text)) == ["egg", "veg"]
    assert client.get("/static/art.svg").status_code == 200


def test_the_drawings_exist_in_the_art(client):
    art = client.get("/static/art.svg").text
    for icon in ("veg", "fruit", "grain", "dairy", "egg", "fish", "meat", "bread", "jar", "herb"):
        assert f'id="i-{icon}"' in art
    for icon in ("bowl", "pot", "sunrise", "sandwich", "cookie"):
        assert f'id="i-{icon}"' in art
