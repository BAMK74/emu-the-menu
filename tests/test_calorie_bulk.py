import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.allergens import Allergen
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import IngredientNotFoundError, StockRepository
from emu_the_menu.web import create_app

# --- saving calories alone --------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


def ingredient(db_path, name):
    with StockRepository(db_path) as stock:
        return next(i for i in stock.ingredients() if i.name == name)


def add_recipe(db_path, name="Soup", lines=(("carrots", 200, "g"),)):
    with RecipeRepository(db_path) as recipes:
        return recipes.add(name, 2, (MealType.DINNER,), Weather.ANY, (), list(lines))


def test_set_calories_changes_only_the_calories(db_path):
    add_recipe(db_path)
    carrots = ingredient(db_path, "carrots")
    with StockRepository(db_path) as stock:
        stock.set_ingredient_details(carrots.id, 10, (FoodGroup.MEAT,), (Allergen.CELERY,))
        stock.set_calories(carrots.id, 41)
    after = ingredient(db_path, "carrots")
    assert after.kcal == 41
    assert after.food_groups == (FoodGroup.MEAT,) and after.allergens == (Allergen.CELERY,)
    assert after.reviewed


def test_set_calories_does_not_mark_an_ingredient_as_reviewed(db_path):
    add_recipe(db_path)
    carrots = ingredient(db_path, "carrots")
    with StockRepository(db_path) as stock:
        stock.set_calories(carrots.id, 41)
    after = ingredient(db_path, "carrots")
    assert after.kcal == 41 and not after.reviewed


def test_set_calories_checks_the_figure_for_the_unit(db_path):
    add_recipe(db_path)
    carrots = ingredient(db_path, "carrots")
    with StockRepository(db_path) as stock:
        with pytest.raises(ValueError):
            stock.set_calories(carrots.id, 5000)
        with pytest.raises(ValueError):
            stock.set_calories(carrots.id, -1)
    assert ingredient(db_path, "carrots").kcal is None


def test_set_calories_for_an_unknown_ingredient_is_reported(db_path):
    with StockRepository(db_path) as stock, pytest.raises(IngredientNotFoundError):
        stock.set_calories(99, 10)


def test_blank_calories_clear_the_figure(db_path):
    add_recipe(db_path)
    carrots = ingredient(db_path, "carrots")
    with StockRepository(db_path) as stock:
        stock.set_calories(carrots.id, 41)
        stock.set_calories(carrots.id, None)
    assert ingredient(db_path, "carrots").kcal is None


# --- the bulk page ----------------------------------------------------------------------------


@pytest.fixture
def client(db_path):
    app = create_app(
        db_path, today=lambda: date(2026, 11, 2), fetch_product=lambda c: {"status": 0}
    )
    return TestClient(app, follow_redirects=False)


def text_of(html):
    html = re.sub(r"<(style|script).*?</>", "", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def kcal_box(html, ingredient_id):
    found = re.search(rf'<input[^>]*name="kcal_{ingredient_id}"[^>]*>', html)
    return found.group(0) if found else None


def stocked(db_path):
    """Two recipes, so that carrots is the most used ingredient."""
    add_recipe(db_path, "Soup", [("carrots", 200, "g"), ("mystery spice", 5, "g")])
    add_recipe(db_path, "Stew", [("carrots", 100, "g"), ("rice", 100, "g"), ("milk", 100, "ml")])


def test_the_page_lists_ingredients_without_calories_most_used_first(client, db_path):
    stocked(db_path)
    text = text_of(client.get("/ingredients/calories").text)
    assert text.index("carrots") < text.index("rice")
    assert "per 100 g" in text and "per 100 ml" in text


def test_ingredients_that_have_calories_are_not_listed(client, db_path):
    stocked(db_path)
    with StockRepository(db_path) as stock:
        stock.set_calories(ingredient(db_path, "rice").id, 360)
    assert (
        kcal_box(client.get("/ingredients/calories").text, ingredient(db_path, "rice").id) is None
    )


def test_a_known_food_comes_with_a_labelled_suggestion_and_others_are_empty(client, db_path):
    stocked(db_path)
    html = client.get("/ingredients/calories").text
    carrots = kcal_box(html, ingredient(db_path, "carrots").id)
    mystery = kcal_box(html, ingredient(db_path, "mystery spice").id)
    assert 'value="41"' in carrots
    assert "value=" not in mystery or 'value=""' in mystery
    assert "suggested" in text_of(html).lower()
    assert "rough" in text_of(html).lower()


def test_the_page_can_be_limited_to_one_recipe(client, db_path):
    stocked(db_path)
    with RecipeRepository(db_path) as recipes:
        soup = next(r for r in recipes.recipes() if r.name == "Soup")
    text = text_of(client.get(f"/ingredients/calories?recipe={soup.id}").text)
    assert "mystery spice" in text and "rice" not in text and "Soup" in text


def post(client, db_path, values, **extra):
    data = {f"kcal_{ingredient(db_path, name).id}": value for name, value in values.items()}
    return client.post("/ingredients/calories", data={**data, **extra})


def test_saving_stores_every_figure_typed_and_says_how_many(client, db_path):
    stocked(db_path)
    response = post(client, db_path, {"carrots": "41", "rice": "360,5", "milk": ""})
    assert response.status_code == 303
    assert "done=2" in response.headers["location"]
    assert ingredient(db_path, "carrots").kcal == 41
    assert ingredient(db_path, "rice").kcal == 360.5
    assert ingredient(db_path, "milk").kcal is None
    assert "Saved 2 ingredients" in text_of(client.get(response.headers["location"]).text)


def test_saving_calories_does_not_review_the_ingredient(client, db_path):
    stocked(db_path)
    post(client, db_path, {"carrots": "41"})
    assert not ingredient(db_path, "carrots").reviewed


def test_a_bad_figure_saves_nothing_and_keeps_what_was_typed(client, db_path):
    stocked(db_path)
    response = post(client, db_path, {"carrots": "41", "rice": "lots"})
    assert response.status_code == 400
    assert "rice" in text_of(response.text) and "lots" in response.text
    assert ingredient(db_path, "carrots").kcal is None


def test_a_figure_too_big_for_the_unit_is_refused(client, db_path):
    stocked(db_path)
    response = post(client, db_path, {"carrots": "5000"})
    assert response.status_code == 400 and ingredient(db_path, "carrots").kcal is None


def test_fields_for_unknown_ingredients_are_ignored(client, db_path):
    stocked(db_path)
    response = client.post("/ingredients/calories", data={"kcal_9999": "10", "kcal_x": "5"})
    assert response.status_code == 303


def test_saving_returns_to_the_same_recipe(client, db_path):
    stocked(db_path)
    with RecipeRepository(db_path) as recipes:
        soup = next(r for r in recipes.recipes() if r.name == "Soup")
    response = post(client, db_path, {"carrots": "41"}, recipe=str(soup.id))
    assert f"recipe={soup.id}" in response.headers["location"]


def test_when_nothing_is_missing_the_page_says_so(client, db_path):
    assert "Every ingredient has calories" in text_of(client.get("/ingredients/calories").text)


def test_long_lists_are_split_into_pages(client, db_path):
    add_recipe(db_path, "Many", [(f"food{n:03d}", 1, "g") for n in range(45)])
    first = client.get("/ingredients/calories").text
    assert "page=2" in first and kcal_box(first, ingredient(db_path, "food044").id) is None
    second = client.get("/ingredients/calories?page=2").text
    assert kcal_box(second, ingredient(db_path, "food044").id) is not None


def test_the_ingredients_page_links_to_the_bulk_page(client, db_path):
    stocked(db_path)
    assert 'href="/ingredients/calories"' in client.get("/ingredients").text


def test_recipes_with_unknown_calories_link_to_filling_them_in(client, db_path):
    stocked(db_path)
    with RecipeRepository(db_path) as recipes:
        soup = next(r for r in recipes.recipes() if r.name == "Soup")
    assert f"/ingredients/calories?recipe={soup.id}" in client.get("/recipes").text
    assert f"/ingredients/calories?recipe={soup.id}" in client.get(f"/recipes/{soup.id}").text
