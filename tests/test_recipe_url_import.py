import json
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.page_fetch import PageUnavailableError, UnsafeAddressError
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.repository import StockRepository
from emu_the_menu.web import create_app

RECIPE = {
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Tomato soup",
    "recipeYield": "6 servings",
    "recipeIngredient": ["500 g tomatoes", "1 1/2 cups vegetable stock", "salt to taste"],
    "keywords": "Soup, quick",
    "recipeInstructions": "Cook it.",
}


def html(recipe=RECIPE):
    return f'<html><script type="application/ld+json">{json.dumps(recipe)}</script></html>'


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


def make_client(db_path, fetch):
    app = create_app(
        db_path,
        today=lambda: date(2026, 10, 26),
        fetch_product=lambda code: {"status": 0},
        fetch_recipe_page=fetch,
    )
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def client(db_path):
    return make_client(db_path, lambda url: html())


def text_of(markup):
    markup = re.sub(r"<(style|script)\b.*?</\1>", "", markup, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", markup).split())


def values(page, field):
    return re.findall(rf'name="{field}"[^>]*value="([^"]*)"', page)


def import_url(client, url="https://example.com/soup"):
    return client.post("/recipes/import", data={"url": url})


def test_the_new_recipe_page_offers_an_import_box(client):
    page = client.get("/recipes/new").text
    assert 'action="/recipes/import"' in page and 'name="url"' in page


def test_importing_fills_in_the_form_without_saving_anything(client, db_path):
    response = import_url(client)
    assert response.status_code == 200
    page = response.text
    assert 'value="Tomato soup"' in page
    assert 'value="6"' in page
    assert 'value="soup, quick"' in page
    assert values(page, "ingredient")[:3] == ["tomatoes", "vegetable stock", "salt"]
    assert values(page, "quantity")[:3] == ["500", "1.5", ""]
    assert re.search(r'<option value="cup" selected', page)
    with RecipeRepository(db_path) as repo:
        assert repo.recipes() == []
    with StockRepository(db_path) as stock:
        assert stock.ingredients() == []


def test_the_page_says_where_it_came_from_and_to_check_the_quantities(client):
    page = text_of(import_url(client).text)
    assert "example.com" in page
    assert "Check every ingredient and quantity" in page


def test_lines_that_could_not_be_read_are_listed_for_the_person(client):
    page = text_of(import_url(client).text)
    assert "salt to taste" in page
    assert "Add a quantity" in page


def test_saving_the_imported_form_keeps_the_source_address(client, db_path):
    page = import_url(client).text
    assert 'name="source_url" value="https://example.com/soup"' in page
    client.post(
        "/recipes",
        data={
            "name": "Tomato soup",
            "servings": "6",
            "meal_types": ["dinner"],
            "weather": "any",
            "tags": "soup",
            "ingredient": ["tomatoes"],
            "quantity": ["500"],
            "unit": ["g"],
            "source_url": "https://example.com/soup",
        },
    )
    with RecipeRepository(db_path) as repo:
        assert repo.recipes()[0].source_url == "https://example.com/soup"
    listing = client.get("/recipes").text
    assert 'href="https://example.com/soup"' in listing
    assert 'rel="noopener noreferrer"' in listing


def test_the_edit_page_keeps_and_links_the_source(client, db_path):
    client.post(
        "/recipes",
        data={
            "name": "Soup",
            "servings": "2",
            "meal_types": ["dinner"],
            "ingredient": ["rice"],
            "quantity": ["1"],
            "unit": ["g"],
            "source_url": "https://example.com/soup",
        },
    )
    with RecipeRepository(db_path) as repo:
        recipe_id = repo.recipes()[0].id
    page = client.get(f"/recipes/{recipe_id}").text
    assert 'name="source_url" value="https://example.com/soup"' in page
    assert 'href="https://example.com/soup"' in page


def test_a_bad_source_is_rejected_when_saving(client, db_path):
    response = client.post(
        "/recipes",
        data={
            "name": "Soup",
            "servings": "2",
            "meal_types": ["dinner"],
            "ingredient": ["rice"],
            "quantity": ["1"],
            "unit": ["g"],
            "source_url": "javascript:alert(1)",
        },
    )
    assert response.status_code == 400
    assert "web address" in text_of(response.text)


def test_known_ingredients_are_reused_by_name_ignoring_plurals(db_path):
    client = make_client(db_path, lambda url: html())
    client.post(
        "/recipes",
        data={
            "name": "First",
            "servings": "2",
            "meal_types": ["dinner"],
            "ingredient": ["tomato"],
            "quantity": ["3"],
            "unit": ["count"],
        },
    )
    page = import_url(client).text
    assert values(page, "ingredient")[0] == "tomato"


# --- when the import cannot work ---------------------------------------------------------------


@pytest.mark.parametrize(
    "fetch_error, status, message",
    [
        (UnsafeAddressError("That address is not a public website"), 400, "not a public website"),
        (PageUnavailableError("Could not reach example.com"), 502, "Could not reach"),
    ],
)
def test_an_unusable_address_is_explained_and_the_form_stays_usable(
    db_path, fetch_error, status, message
):
    def fetch(url):
        raise fetch_error

    client = make_client(db_path, fetch)
    response = import_url(client)
    assert response.status_code == status
    page = response.text
    assert message in text_of(page)
    assert 'value="https://example.com/soup"' in page
    assert 'name="ingredient"' in page  # the blank form is still there


def test_a_page_without_a_recipe_is_explained(db_path):
    client = make_client(db_path, lambda url: "<html><body>Just a blog</body></html>")
    response = import_url(client)
    assert response.status_code == 400
    assert "No recipe" in text_of(response.text)


def test_a_blank_address_is_rejected_without_fetching(db_path):
    calls = []
    client = make_client(db_path, lambda url: calls.append(url) or html())
    response = import_url(client, "  ")
    assert response.status_code == 400
    assert calls == []


def test_a_private_address_is_never_fetched_by_the_real_fetcher(db_path):
    # No fake here: the real fetcher must refuse before touching the network.
    app = create_app(db_path, today=lambda: date(2026, 10, 26))
    response = TestClient(app, follow_redirects=False).post(
        "/recipes/import", data={"url": "http://127.0.0.1/export.json"}
    )
    assert response.status_code == 400
    assert "public website" in text_of(response.text)


def test_the_imported_text_is_escaped_in_the_page(db_path):
    nasty = {**RECIPE, "name": '"><script>alert(1)</script>', "keywords": "<b>x</b>"}
    client = make_client(db_path, lambda url: html(nasty))
    page = import_url(client).text
    assert "<script>alert(1)</script>" not in page


def test_lines_that_are_not_ingredients_are_left_out_and_listed(db_path):
    recipe = {
        **RECIPE,
        "recipeIngredient": ["500 g tomatoes", "1 batch base recipe", "Optional seasonings"],
    }
    client = make_client(db_path, lambda url: html(recipe))
    page = import_url(client).text
    assert values(page, "ingredient")[:1] == ["tomatoes"]
    assert "base recipe" not in values(page, "ingredient")
    assert "optional seasonings" not in values(page, "ingredient")
    note = text_of(page)
    assert "Left out because they are not ingredients" in note
    assert "1 batch base recipe" in note and "Optional seasonings" in note


def test_nothing_is_said_about_left_out_lines_when_there_are_none(client):
    assert "Left out because" not in text_of(import_url(client).text)
