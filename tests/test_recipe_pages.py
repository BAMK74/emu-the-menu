import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.allergens import Allergen
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.repository import StockRepository
from emu_the_menu.web import create_app


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


@pytest.fixture
def client(db_path):
    app = create_app(
        db_path, today=lambda: date(2026, 10, 26), fetch_product=lambda code: {"status": 0}
    )
    return TestClient(app, follow_redirects=False)


def text_of(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def body_of(html):
    """The page without its style and script blocks, so text searches see only the content."""
    return re.sub(r"<(style|script)\b.*?</\1>", "", html, flags=re.S)


def form(**overrides):
    data = {
        "name": "Tomato rice",
        "servings": "2",
        "meal_types": ["dinner"],
        "weather": "any",
        "tags": "",
        "ingredient": ["rice", "tomatoes", "", ""],
        "quantity": ["200", "4", "", ""],
        "unit": ["g", "count", "g", "g"],
    }
    data.update(overrides)
    return data


def create(client, **overrides):
    return client.post("/recipes", data=form(**overrides))


def recipes(db_path):
    with RecipeRepository(db_path) as repo:
        return repo.recipes()


def review(db_path, name, kcal=None, groups=(), allergens=()):
    with StockRepository(db_path) as stock:
        ingredient = next(i for i in stock.ingredients() if i.name == name)
        stock.set_ingredient_details(ingredient.id, kcal, groups, allergens)


def cards(client):
    page = client.get("/recipes").text.split("<ul>")[1]
    return [li.split("</li>")[0] for li in page.split("<li>")[1:]]


# --- navigation and the empty page ------------------------------------------------------------


def test_the_header_links_to_recipes_and_marks_the_page(client):
    for path in ("/", "/eaters", "/ingredients", "/recipes"):
        assert 'href="/recipes"' in client.get(path).text
    marked = re.findall(r'<a href="([^"]+)"[^>]*aria-current="page"', client.get("/recipes").text)
    assert marked == ["/recipes", "/recipes"]  # the header link and the Recipes tab


def test_an_empty_page_invites_the_first_recipe(client):
    page = text_of(body_of(client.get("/recipes").text))
    assert "No recipes yet" in page
    assert 'href="/recipes/new"' in client.get("/recipes").text


# --- the form ---------------------------------------------------------------------------------


def test_the_new_recipe_form_has_every_field(client):
    page = client.get("/recipes/new").text
    for field in ("name", "servings", "weather", "tags"):
        assert f'name="{field}"' in page
    assert 'name="meal_types"' in page
    assert 'name="ingredient"' in page and 'name="quantity"' in page and 'name="unit"' in page
    assert len(re.findall(r'name="meal_types"', page)) == 4


def test_the_form_suggests_existing_ingredient_names(client, db_path):
    create(client)
    page = client.get("/recipes/new").text
    assert '<option value="rice">' in page and '<option value="tomatoes">' in page


# --- saving -----------------------------------------------------------------------------------


def test_saving_stores_the_recipe_and_returns_to_the_list(client, db_path):
    response = create(client, tags="Quick, kids", weather="warm")
    assert response.status_code == 303 and response.headers["location"] == "/recipes"
    [recipe] = recipes(db_path)
    assert (recipe.name, recipe.servings, recipe.weather.value) == ("Tomato rice", 2, "warm")
    assert recipe.tags == ("quick", "kids")
    assert [(line.ingredient.name, line.quantity) for line in recipe.lines] == [
        ("rice", 200),
        ("tomatoes", 4),
    ]


def test_blank_ingredient_rows_are_ignored(client, db_path):
    create(client)
    assert len(recipes(db_path)[0].lines) == 2


def test_a_comma_works_as_the_decimal_mark(client, db_path):
    create(client, ingredient=["flour"], quantity=["1,5"], unit=["kg"])
    assert recipes(db_path)[0].lines[0].quantity == 1500


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"name": " "}, "name"),
        ({"servings": "0"}, "Servings"),
        ({"meal_types": []}, "meal type"),
        ({"weather": "rainy"}, "eather"),
        ({"ingredient": ["", ""], "quantity": ["", ""], "unit": ["g", "g"]}, "ingredient"),
        ({"ingredient": ["rice"], "quantity": [""], "unit": ["g"]}, "quantity"),
        ({"ingredient": [""], "quantity": ["5"], "unit": ["g"]}, "ingredient"),
        ({"ingredient": ["rice"], "quantity": ["lots"], "unit": ["g"]}, "quantity"),
        ({"ingredient": ["rice"], "quantity": ["0"], "unit": ["g"]}, "more than 0"),
        ({"ingredient": ["rice"], "quantity": ["1"], "unit": ["pinch"]}, "unit"),
    ],
)
def test_bad_input_is_rejected_with_a_message_and_nothing_is_saved(
    client, db_path, overrides, message
):
    response = create(client, **overrides)
    assert response.status_code == 400
    assert message in text_of(body_of(response.text))
    assert recipes(db_path) == []
    with StockRepository(db_path) as stock:
        assert stock.ingredients() == []


def test_a_rejected_save_keeps_what_was_typed(client):
    response = create(client, name="Soup", tags="warming", servings="0")
    assert 'value="Soup"' in response.text
    assert 'value="warming"' in response.text
    assert 'value="tomatoes"' in response.text


def test_a_unit_of_the_wrong_kind_for_a_known_ingredient_is_rejected(client, db_path):
    create(client, name="First", ingredient=["chicken"], quantity=["500"], unit=["g"])
    response = create(client, name="Second", ingredient=["chicken"], quantity=["5"], unit=["ml"])
    assert response.status_code == 400
    assert "chicken" in text_of(body_of(response.text))
    assert [r.name for r in recipes(db_path)] == ["First"]


def test_a_duplicate_name_is_rejected(client):
    create(client)
    response = create(client, name="tomato RICE", ingredient=["salt"])
    assert response.status_code == 400
    assert "already" in text_of(body_of(response.text))


# --- the list ---------------------------------------------------------------------------------


def test_the_list_shows_servings_meal_types_weather_and_tags(client):
    create(client, meal_types=["lunch", "dinner"], weather="cold", tags="festive")
    card = text_of(cards(client)[0])
    for word in ("Tomato rice", "2 servings", "Lunch", "Dinner", "Cold weather", "festive"):
        assert word in card


def test_calories_per_serving_show_once_every_ingredient_has_them(client, db_path):
    create(client)
    assert "Calories unknown" in text_of(cards(client)[0])
    review(db_path, "rice", 360)
    review(db_path, "tomatoes", 20)
    # 200 g of rice is 720 kcal and 4 tomatoes are 80 kcal, shared by 2 servings.
    assert "400 kcal per serving" in text_of(cards(client)[0])


def test_a_recipe_with_an_unchecked_ingredient_says_not_checked_and_never_safe(client, db_path):
    create(client)
    review(db_path, "rice", 360)
    card = text_of(cards(client)[0])
    assert "Not checked" in card
    assert "tomatoes" in card
    assert not re.search(r"\b(safe|vegan|vegetarian|free)\b", card, re.I)


def test_allergens_and_diets_show_once_everything_is_checked(client, db_path):
    create(client, ingredient=["rice", "cheese"], quantity=["100", "50"], unit=["g", "g"])
    review(db_path, "rice")
    review(db_path, "cheese", 300, [FoodGroup.DAIRY], [Allergen.MILK])
    card = text_of(cards(client)[0])
    assert "Milk" in card
    assert "Vegetarian" in card and "Pescatarian" in card
    assert "Vegan" not in card
    assert "Not checked" not in card


def test_a_checked_recipe_with_no_allergens_says_none_ticked(client, db_path):
    create(client, ingredient=["rice"], quantity=["100"], unit=["g"])
    review(db_path, "rice", 360)
    assert "No allergens ticked" in text_of(cards(client)[0])


def test_the_list_warns_to_read_labels(client):
    create(client)
    page = text_of(body_of(client.get("/recipes").text))
    assert "always read the label" in page
    assert 'href="/disclaimer"' in client.get("/recipes").text


# --- editing and deleting ---------------------------------------------------------------------


def test_the_edit_page_shows_the_saved_values(client, db_path):
    create(client, tags="quick", weather="cold", meal_types=["lunch"])
    recipe_id = recipes(db_path)[0].id
    page = client.get(f"/recipes/{recipe_id}").text
    assert 'value="Tomato rice"' in page and 'value="quick"' in page
    assert re.search(r'name="meal_types" value="lunch" checked', page)
    assert not re.search(r'name="meal_types" value="dinner" checked', page)
    assert re.search(r'<option value="cold" selected', page)
    assert 'value="rice"' in page and 'value="200"' in page


def test_quantities_are_shown_in_the_unit_people_read(client, db_path):
    create(client, ingredient=["flour"], quantity=["1500"], unit=["g"])
    page = client.get(f"/recipes/{recipes(db_path)[0].id}").text
    assert 'value="1.5"' in page
    assert re.search(r'<option value="kg" selected', page)


def test_saving_an_edit_replaces_the_recipe(client, db_path):
    create(client)
    recipe_id = recipes(db_path)[0].id
    response = client.post(
        f"/recipes/{recipe_id}",
        data=form(
            name="Tomato risotto", servings="4", ingredient=["rice"], quantity=["300"], unit=["g"]
        ),
    )
    assert response.status_code == 303
    [recipe] = recipes(db_path)
    assert (recipe.id, recipe.name, recipe.servings) == (recipe_id, "Tomato risotto", 4)
    assert [line.ingredient.name for line in recipe.lines] == ["rice"]


def test_a_rejected_edit_changes_nothing(client, db_path):
    create(client)
    recipe_id = recipes(db_path)[0].id
    response = client.post(f"/recipes/{recipe_id}", data=form(name="Changed", servings="99"))
    assert response.status_code == 400
    assert recipes(db_path)[0].name == "Tomato rice"


def test_deleting_removes_the_recipe_but_keeps_its_ingredients(client, db_path):
    create(client)
    recipe_id = recipes(db_path)[0].id
    response = client.post(f"/recipes/{recipe_id}/delete")
    assert response.status_code == 303 and response.headers["location"] == "/recipes"
    assert recipes(db_path) == []
    with StockRepository(db_path) as stock:
        assert len(stock.ingredients()) == 2


def test_a_missing_recipe_is_a_404_for_the_page_saving_and_deleting(client):
    assert client.get("/recipes/99").status_code == 404
    assert client.post("/recipes/99", data=form()).status_code == 404
    assert client.post("/recipes/99/delete").status_code == 404


def test_recipes_appear_in_the_json_export(client):
    create(client, tags="quick")
    exported = client.get("/export.json").json()["recipes"]
    assert exported[0]["name"] == "Tomato rice"
    assert exported[0]["lines"][0] == {
        "ingredient": "rice", "quantity": 200, "unit": "g", "alternative_group": None,
    }  # fmt: skip


def test_new_ingredients_from_a_recipe_appear_on_the_ingredients_page_as_unchecked(client):
    create(client)
    page = text_of(body_of(client.get("/ingredients").text))
    assert "Rice" in page or "rice" in page
    assert "2 not checked yet" in page


# --- who can eat it ---------------------------------------------------------------------------


def eater(db_path, name="Sam", **kwargs):
    from emu_the_menu.eaters import Category, EaterRepository

    with EaterRepository(db_path) as repo:
        repo.add(name, Category.MAN, **kwargs)


def verdicts(client):
    return text_of(cards(client)[0])


def test_without_eaters_there_are_no_verdicts(client):
    create(client)
    assert "No problem found" not in verdicts(client)
    assert "Not for" not in verdicts(client)


def test_an_eater_with_a_ticked_allergen_is_blocked_with_the_reason(client, db_path):
    create(client, ingredient=["cheese"], quantity=["50"], unit=["g"])
    review(db_path, "cheese", 300, [FoodGroup.DAIRY], [Allergen.MILK])
    eater(db_path, "Sam", allergens=[Allergen.MILK])
    card = verdicts(client)
    assert "Not for Sam" in card
    assert "contains milk (cheese)" in card
    assert "No problem found for Sam" not in card


def test_an_eater_with_nothing_in_the_way_gets_no_problem_found_not_safe(client, db_path):
    create(client, ingredient=["rice"], quantity=["100"], unit=["g"])
    review(db_path, "rice", 360)
    eater(db_path, "Sam")
    eater(db_path, "Anna")
    card = verdicts(client)
    assert "No problem found for Sam, Anna" in card
    assert not re.search(r"\bsafe\b", card, re.I)


def test_an_unchecked_recipe_is_blocked_for_someone_with_allergies(client, db_path):
    create(client, ingredient=["rice"], quantity=["100"], unit=["g"])
    eater(db_path, "Sam", allergens=[Allergen.MILK])
    card = verdicts(client)
    assert "Not for Sam" in card and "not checked: rice" in card


def test_cautions_are_shown_for_eaters_who_are_not_blocked(client, db_path):
    create(client, ingredient=["rice"], quantity=["100"], unit=["g"])
    review(db_path, "rice", 360)
    eater(db_path, "Sam", allergies=["kiwi"])
    card = verdicts(client)
    assert "No problem found for Sam" in card
    assert "check the labels for kiwi" in card


def test_dislikes_are_noted_without_blocking(client, db_path):
    create(client, ingredient=["mushrooms"], quantity=["100"], unit=["g"])
    review(db_path, "mushrooms", 20)
    eater(db_path, "Sam", dislikes=["mushroom"])
    card = verdicts(client)
    assert "No problem found for Sam" in card
    assert "mushroom is disliked" in card
