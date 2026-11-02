"""Clicking an ingredient to rename it, and choosing which proposed tidy-ups to apply."""

import re
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.allergens import Allergen
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import IngredientNotFoundError, StockRepository
from emu_the_menu.units import IncompatibleUnitError
from emu_the_menu.web import create_app


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


def save_recipe(db_path, lines, name="Dish"):
    with RecipeRepository(db_path) as repo:
        return repo.add(name, 2, [MealType.DINNER], Weather.ANY, (), lines)


def ingredient(db_path, name):
    with StockRepository(db_path) as stock:
        return next(i for i in stock.ingredients() if i.name == name)


def names(db_path):
    with StockRepository(db_path) as stock:
        return sorted(i.name for i in stock.ingredients())


# --- the rule ------------------------------------------------------------------------------------


def test_an_ingredient_can_be_renamed(db_path):
    save_recipe(db_path, [("zhoug", 10, "g")])
    with StockRepository(db_path) as stock:
        kept = stock.rename_ingredient(ingredient(db_path, "zhoug").id, "  Green   Zhoug ")
        assert stock.get_ingredient(kept).name == "green zhoug"
    assert names(db_path) == ["green zhoug"]


def test_renaming_keeps_what_was_ticked_and_the_recipes(db_path):
    save_recipe(db_path, [("zhoug", 10, "g")])
    zhoug = ingredient(db_path, "zhoug")
    with StockRepository(db_path) as stock:
        stock.set_ingredient_details(zhoug.id, 20, (), (Allergen.CELERY,))
        stock.rename_ingredient(zhoug.id, "green zhoug")
        renamed = stock.get_ingredient(zhoug.id)
    assert renamed.reviewed and renamed.kcal == 20 and renamed.allergens == (Allergen.CELERY,)
    with RecipeRepository(db_path) as repo:
        assert repo.get(1).lines[0].ingredient.name == "green zhoug"


def test_only_the_case_can_change(db_path):
    save_recipe(db_path, [("zhoug", 10, "g")])
    with StockRepository(db_path) as stock:
        stock.rename_ingredient(ingredient(db_path, "zhoug").id, "ZHOUG")
    assert names(db_path) == ["zhoug"]


@pytest.mark.parametrize("bad", ["", "   ", "x" * 61])
def test_a_blank_or_very_long_name_is_rejected(db_path, bad):
    save_recipe(db_path, [("zhoug", 10, "g")])
    with StockRepository(db_path) as stock, pytest.raises(ValueError, match="name"):
        stock.rename_ingredient(ingredient(db_path, "zhoug").id, bad)
    assert names(db_path) == ["zhoug"]


def test_a_name_that_offers_a_choice_is_rejected(db_path):
    save_recipe(db_path, [("zhoug", 10, "g")])
    with StockRepository(db_path) as stock, pytest.raises(ValueError, match="choice"):
        stock.rename_ingredient(ingredient(db_path, "zhoug").id, "zhoug or harissa")


def test_renaming_to_a_name_that_exists_in_the_same_unit_merges_into_it(db_path):
    save_recipe(db_path, [("pepper flakes", 1, "g"), ("chili flakes", 2, "g")])
    flakes, chili = ingredient(db_path, "pepper flakes"), ingredient(db_path, "chili flakes")
    with StockRepository(db_path) as stock:
        stock.set_ingredient_details(flakes.id, None, (), (Allergen.CELERY,))
        kept = stock.rename_ingredient(flakes.id, "chili flakes")
        assert kept == chili.id
        merged = stock.get_ingredient(kept)
    assert names(db_path) == ["chili flakes"]
    assert merged.allergens == (Allergen.CELERY,) and merged.reviewed


def test_renaming_to_a_name_in_another_kind_of_unit_is_refused(db_path):
    save_recipe(db_path, [("tomatoes", 2, "count"), ("passata", 200, "ml")])
    with StockRepository(db_path) as stock, pytest.raises(IncompatibleUnitError):
        stock.rename_ingredient(ingredient(db_path, "passata").id, "tomatoes")
    assert names(db_path) == ["passata", "tomatoes"]


def test_renaming_a_missing_ingredient_is_reported(db_path):
    with StockRepository(db_path) as stock, pytest.raises(IngredientNotFoundError):
        stock.rename_ingredient(999, "salt")


# --- clicking an ingredient ----------------------------------------------------------------------


@pytest.fixture
def client(db_path):
    app = create_app(
        db_path, today=lambda: date(2026, 11, 2), fetch_product=lambda code: {"status": 0}
    )
    return TestClient(app, follow_redirects=False)


def text_of(html):
    html = re.sub(r"<(style|script)\b.*?</\1>", "", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_the_popup_and_the_page_have_a_name_box_with_the_current_name(client, db_path):
    save_recipe(db_path, [("zhoug", 10, "g")])
    zhoug = ingredient(db_path, "zhoug")
    for path in (f"/ingredients/{zhoug.id}/panel", f"/ingredients/{zhoug.id}"):
        page = client.get(path).text
        assert re.search(r'name="name"[^>]*value="zhoug"', page), path


def test_saving_with_a_new_name_renames_it(client, db_path):
    save_recipe(db_path, [("zhoug", 10, "g")])
    zhoug = ingredient(db_path, "zhoug")
    response = client.post(f"/ingredients/{zhoug.id}", data={"name": "green zhoug", "kcal": "20"})
    assert response.status_code == 303
    assert names(db_path) == ["green zhoug"]
    assert ingredient(db_path, "green zhoug").kcal == 20


def test_saving_without_a_name_keeps_the_name(client, db_path):
    save_recipe(db_path, [("zhoug", 10, "g")])
    zhoug = ingredient(db_path, "zhoug")
    client.post(f"/ingredients/{zhoug.id}", data={"kcal": "20"})
    assert names(db_path) == ["zhoug"]


def test_a_bad_name_shows_the_message_and_changes_nothing(client, db_path):
    save_recipe(db_path, [("zhoug", 10, "g")])
    zhoug = ingredient(db_path, "zhoug")
    response = client.post(
        f"/ingredients/{zhoug.id}", data={"name": "zhoug or harissa", "kcal": "20"}
    )
    assert response.status_code == 400
    assert "choice" in text_of(response.text)
    assert 'value="zhoug or harissa"' in response.text
    assert names(db_path) == ["zhoug"] and ingredient(db_path, "zhoug").kcal is None


def test_renaming_onto_another_ingredient_merges_and_returns_to_the_list(client, db_path):
    save_recipe(db_path, [("pepper flakes", 1, "g"), ("chili flakes", 2, "g")])
    flakes = ingredient(db_path, "pepper flakes")
    response = client.post(f"/ingredients/{flakes.id}", data={"name": "chili flakes"})
    assert response.status_code == 303
    assert names(db_path) == ["chili flakes"]


def test_renaming_onto_a_name_in_another_kind_of_unit_says_why(client, db_path):
    save_recipe(db_path, [("tomatoes", 2, "count"), ("passata", 200, "ml")])
    passata = ingredient(db_path, "passata")
    response = client.post(f"/ingredients/{passata.id}", data={"name": "tomatoes"})
    assert response.status_code == 400
    assert "tomatoes" in text_of(response.text)
    assert names(db_path) == ["passata", "tomatoes"]


# --- choosing which tidy-ups to apply -----------------------------------------------------------

# --- choosing which tidy-ups to apply --------------------------------------------------------


def messy(db_path):
    """Two groups to tidy: black pepper twins, and a cilantro that has a describing word."""
    save_recipe(db_path, [("black pepper", 1, "g")])
    db = sqlite3.connect(db_path)
    db.execute(
        "INSERT INTO ingredients (name, base_unit) VALUES ('freshly cracked black pepper', 'g')"
    )
    db.execute("INSERT INTO ingredients (name, base_unit) VALUES ('chopped fresh cilantro', 'g')")
    db.commit()
    db.close()


def keys(page):
    return re.findall(r'name="apply" value="(\d+)"', page)


def test_each_proposed_change_has_a_ticked_box_and_an_editable_name(client, db_path):
    messy(db_path)
    page = client.get("/ingredients").text
    assert len(keys(page)) == 2
    assert len(re.findall(r'name="apply" value="\d+" checked', page)) == 2
    assert len(re.findall(r'name="target_\d+"', page)) == 2
    assert 'name="selected" value="1"' in page


def test_the_names_in_the_list_open_the_ingredient(client, db_path):
    messy(db_path)
    cilantro = ingredient(db_path, "chopped fresh cilantro")
    assert f'href="/ingredients/{cilantro.id}"' in client.get("/ingredients").text


def test_only_the_ticked_changes_are_applied(client, db_path):
    messy(db_path)
    cilantro = ingredient(db_path, "chopped fresh cilantro")
    page = client.get("/ingredients").text
    pepper_key = next(k for k in keys(page) if int(k) != cilantro.id)
    client.post(
        "/ingredients/tidy",
        data={
            "selected": "1",
            "apply": [str(cilantro.id)],
            f"target_{cilantro.id}": "cilantro",
            f"target_{pepper_key}": "black pepper",
        },
    )
    assert "chopped fresh cilantro" not in names(db_path)
    assert "freshly cracked black pepper" in names(db_path)


def test_a_different_name_can_be_typed_for_a_change(client, db_path):
    messy(db_path)
    cilantro = ingredient(db_path, "chopped fresh cilantro")
    client.post(
        "/ingredients/tidy",
        data={
            "selected": "1",
            "apply": [str(cilantro.id)],
            f"target_{cilantro.id}": "coriander leaves",
        },
    )
    assert "coriander leaves" in names(db_path)


def test_nothing_ticked_changes_nothing(client, db_path):
    messy(db_path)
    before = names(db_path)
    client.post("/ingredients/tidy", data={"selected": "1"})
    assert names(db_path) == before


def test_without_a_selection_everything_is_tidied_as_before(client, db_path):
    messy(db_path)
    client.post("/ingredients/tidy")
    assert names(db_path) == ["black pepper", "cilantro"]


def test_a_change_that_cannot_be_made_is_skipped_and_the_rest_still_happen(client, db_path):
    messy(db_path)
    save_recipe(db_path, [("tomatoes", 2, "count")], name="Other")
    cilantro = ingredient(db_path, "chopped fresh cilantro")
    twin = ingredient(db_path, "freshly cracked black pepper")
    keys_now = keys(client.get("/ingredients").text)
    pepper_key = next(k for k in keys_now if int(k) != cilantro.id)
    response = client.post(
        "/ingredients/tidy",
        data={
            "selected": "1",
            "apply": [str(cilantro.id), pepper_key],
            f"target_{cilantro.id}": "tomatoes",  # a counted ingredient already has this name
            f"target_{pepper_key}": "black pepper",
        },
    )
    assert response.status_code == 303
    assert "chopped fresh cilantro" in names(db_path)  # skipped
    assert twin.name not in names(db_path)  # the other change went through


def test_a_typed_name_that_offers_a_choice_is_skipped(client, db_path):
    messy(db_path)
    cilantro = ingredient(db_path, "chopped fresh cilantro")
    client.post(
        "/ingredients/tidy",
        data={"selected": "1", "apply": [str(cilantro.id)], f"target_{cilantro.id}": "a or b"},
    )
    assert "chopped fresh cilantro" in names(db_path)


# --- Reset, Cancel and Save beside the new name of each change ---------------------------------


def row_of(page, key):
    return page.split(f'name="apply" value="{key}"')[1].split("</li>")[0]


def test_each_change_has_reset_cancel_and_save_after_its_new_name(client, db_path):
    messy(db_path)
    page = client.get("/ingredients").text
    for key in keys(page):
        row = row_of(page, key)
        name_box = row.index(f'name="target_{key}"')
        reset, cancel, save = (
            row.index(m)
            for m in ("data-tidy-reset", "data-tidy-cancel", f'name="only" value="{key}"')
        )
        assert name_box < reset < cancel < save
        assert ">Reset<" in row and ">Cancel<" in row and ">Save<" in row


def test_the_save_beside_a_change_applies_only_that_change_under_the_typed_name(client, db_path):
    messy(db_path)
    cilantro = ingredient(db_path, "chopped fresh cilantro")
    page = client.get("/ingredients").text
    other = next(k for k in keys(page) if int(k) != cilantro.id)
    client.post(
        "/ingredients/tidy",
        data={
            "selected": "1",
            "only": str(cilantro.id),
            "apply": [str(cilantro.id), other],  # the other one is ticked too, but not saved
            f"target_{cilantro.id}": "fresh coriander",
            f"target_{other}": "black pepper",
        },
    )
    assert "fresh coriander" in names(db_path)
    assert "freshly cracked black pepper" in names(db_path)  # untouched


def test_the_save_beside_a_change_works_even_if_its_box_was_unticked(client, db_path):
    messy(db_path)
    cilantro = ingredient(db_path, "chopped fresh cilantro")
    client.post(
        "/ingredients/tidy",
        data={"selected": "1", "only": str(cilantro.id), f"target_{cilantro.id}": "cilantro"},
    )
    assert "cilantro" in names(db_path)


def test_a_save_for_a_change_that_no_longer_exists_does_nothing(client, db_path):
    messy(db_path)
    before = names(db_path)
    assert (
        client.post("/ingredients/tidy", data={"selected": "1", "only": "99999"}).status_code == 303
    )
    assert names(db_path) == before
