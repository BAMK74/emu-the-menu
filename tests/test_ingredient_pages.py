import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.allergens import Allergen
from emu_the_menu.food_groups import FoodGroup
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


def stock(client, name, quantity="500", unit="g", location="pantry"):
    return client.post(
        "/items",
        data={"ingredient": name, "quantity": quantity, "unit": unit, "location": location},
    )


def ingredient(db_path, name):
    with StockRepository(db_path) as repo:
        return next(i for i in repo.ingredients() if i.name == name)


def text_of(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def card(client, name):
    page = client.get("/ingredients").text.split("<ul>")[1]
    return next(li for li in page.split("<li>") if f"<strong>{name}</strong>" in li)


def chips(client, name):
    return [
        (css.split()[-1], " ".join(t.split()))
        for css, t in re.findall(r'<span class="(chip[^"]*)">([^<]*)</span>', card(client, name))
    ]


def boxes(html, field):
    """(value, ticked) for each checkbox of a field."""
    return [
        (value, bool(ticked))
        for value, ticked in re.findall(
            rf'<input type="checkbox" name="{field}" value="([^"]+)"( checked)?>', html
        )
    ]


def ticked(html, field):
    return [value for value, on in boxes(html, field) if on]


def save(client, ingredient_id, kcal="", food_groups=(), allergens=()):
    return client.post(
        f"/ingredients/{ingredient_id}",
        data={"kcal": kcal, "food_groups": list(food_groups), "allergens": list(allergens)},
    )


# --- the page and the header ------------------------------------------------------------------


def test_the_header_links_to_ingredients_and_marks_the_page(client):
    for path in ("/", "/eaters", "/settings", "/ingredients"):
        assert 'href="/ingredients"' in client.get(path).text
    marked = re.findall(
        r'<a[^>]*aria-current="page"[^>]*>([^<]*)</a>', client.get("/ingredients").text
    )
    assert marked == ["Ingredients"]


def test_an_empty_page_explains_where_ingredients_come_from(client):
    response = client.get("/ingredients")
    assert response.status_code == 200
    assert "No ingredients yet" in text_of(response.text)
    assert "adding stock" in text_of(response.text)


def test_every_ingredient_ever_added_is_listed_as_a_clickable_card(client, db_path):
    stock(client, "chicken")
    stock(client, "rice", "1", "kg")
    page = client.get("/ingredients").text
    for name in ("chicken", "rice"):
        item = ingredient(db_path, name)
        url = f"/ingredients/{item.id}"
        link = f'<a class="card-link" href="{url}" data-panel="{url}/panel">'
        assert link in page
    assert '<dialog id="ingredient-dialog"' in page
    assert '<em class="hint">Click an ingredient to edit it.</em>' in page


def test_an_ingredient_stays_listed_after_its_stock_is_used_up(client, db_path):
    stock(client, "chicken")
    with StockRepository(db_path) as repo:
        repo.use("chicken", 500, "g")
    assert "<strong>chicken</strong>" in client.get("/ingredients").text


def test_the_page_warns_that_allergen_tags_are_set_by_people(client):
    stock(client, "chicken")
    text = text_of(client.get("/ingredients").text)
    for phrase in (
        "Tick every allergen an ingredient contains",
        "including hidden ones",
        "cannot check this for you",
        "always read the label",
    ):
        assert phrase in text, phrase
    assert 'href="/disclaimer"' in client.get("/ingredients").text


# --- what a card shows ------------------------------------------------------------------------


def test_a_new_ingredient_says_it_has_no_calories_and_is_not_checked_yet(client):
    stock(client, "chicken")
    entry = card(client, "chicken")
    assert "No calories yet" in text_of(entry)
    assert ("chip-soon", "Not checked yet") in chips(client, "chicken")


def test_a_saved_ingredient_shows_calories_food_groups_and_allergens(client, db_path):
    stock(client, "chicken")
    save(client, ingredient(db_path, "chicken").id, "165", ["poultry"], ["celery", "eggs"])
    assert "165 kcal" in text_of(card(client, "chicken"))
    found = chips(client, "chicken")
    assert ("chip", "per 100 g") in found
    assert ("chip", "Poultry") in found
    assert ("chip-allergen", "Celery") in found and ("chip-allergen", "Eggs") in found
    assert ("chip-soon", "Not checked yet") not in found


def test_a_reviewed_ingredient_with_no_allergens_says_so(client, db_path):
    stock(client, "rice", "1", "kg")
    save(client, ingredient(db_path, "rice").id, "360")
    assert ("chip-none", "No allergens ticked") in chips(client, "rice")


def test_calories_are_measured_against_the_unit_of_the_ingredient(client, db_path):
    stock(client, "milk", "1", "l", "fridge")
    stock(client, "egg", "6", "count", "fridge")
    save(client, ingredient(db_path, "milk").id, "42")
    save(client, ingredient(db_path, "egg").id, "70")
    assert ("chip", "per 100 ml") in chips(client, "milk")
    assert ("chip", "per piece") in chips(client, "egg")


def overview(client):
    match = re.search(r'<p class="summary">(.*?)</p>', client.get("/ingredients").text, re.S)
    return text_of(match.group(1)) if match else None


def test_the_overview_counts_what_still_needs_doing(client, db_path):
    for name in ("chicken", "rice", "milk"):
        stock(client, name)
    save(client, ingredient(db_path, "rice").id, "360")
    assert overview(client) == "3 ingredients 2 without calories 2 not checked yet"


def test_the_overview_leaves_out_zero_counts_and_uses_the_singular(client, db_path):
    stock(client, "rice")
    save(client, ingredient(db_path, "rice").id, "360")
    assert overview(client) == "1 ingredient"


# --- the popup and the page of its own --------------------------------------------------------


def test_the_popup_is_a_fragment_with_the_form(client, db_path):
    stock(client, "chicken")
    item = ingredient(db_path, "chicken")
    response = client.get(f"/ingredients/{item.id}/panel")
    assert response.status_code == 200
    assert "<html" not in response.text
    assert f'action="/ingredients/{item.id}"' in response.text
    assert "chicken" in response.text.lower()
    assert "data-close-dialog" in response.text


@pytest.mark.parametrize(
    ("unit", "label"),
    [("g", "Calories per 100 g"), ("ml", "Calories per 100 ml"), ("count", "Calories per piece")],
)
def test_the_calories_box_says_what_they_are_measured_against(client, db_path, unit, label):
    stock(client, "thing", "5", unit)
    item = ingredient(db_path, "thing")
    assert f"{label} (kcal)" in text_of(client.get(f"/ingredients/{item.id}/panel").text)


def test_the_popup_shows_the_saved_values(client, db_path):
    stock(client, "chicken")
    item = ingredient(db_path, "chicken")
    save(client, item.id, "165.5", ["meat", "poultry"], ["celery"])
    html = client.get(f"/ingredients/{item.id}/panel").text
    assert re.search(r'name="kcal"[^>]*value="165.5"', html, re.S)
    assert ticked(html, "food_groups") == ["meat", "poultry"]
    assert ticked(html, "allergens") == ["celery"]


def test_the_popup_offers_every_food_group_and_allergen_as_a_pill(client, db_path):
    stock(client, "chicken")
    html = client.get(f"/ingredients/{ingredient(db_path, 'chicken').id}/panel").text
    assert [v for v, _ in boxes(html, "food_groups")] == [g.value for g in FoodGroup]
    assert [v for v, _ in boxes(html, "allergens")] == [a.value for a in Allergen]
    assert 'title="Gluten (wheat, rye, barley, oats)"' in html
    assert 'title="Poultry (chicken, turkey, duck)"' in html


def test_the_popup_tells_people_to_tick_hidden_allergens_and_read_labels(client, db_path):
    stock(client, "soy sauce", "250", "ml")
    html = client.get(f"/ingredients/{ingredient(db_path, 'soy sauce').id}/panel").text
    text = text_of(html)
    assert "including hidden ones" in text
    assert "always read the label" in text.lower()


def test_an_unchecked_ingredient_has_likely_tags_ticked_and_says_so(client, db_path):
    stock(client, "chicken")
    html = client.get(f"/ingredients/{ingredient(db_path, 'chicken').id}/panel").text
    assert ticked(html, "food_groups") == ["poultry"]
    assert "Ticked from the name. Check them before saving" in text_of(html)


def test_the_suggestions_are_not_saved_until_the_person_saves(client, db_path):
    stock(client, "chicken")
    client.get(f"/ingredients/{ingredient(db_path, 'chicken').id}/panel")
    after = ingredient(db_path, "chicken")
    assert (after.food_groups, after.allergens, after.reviewed) == ((), (), False)


def test_suggested_allergens_are_ticked_too(client, db_path):
    stock(client, "peanut butter")
    html = client.get(f"/ingredients/{ingredient(db_path, 'peanut butter').id}/panel").text
    assert ticked(html, "allergens") == ["peanuts"]


def test_no_suggestion_note_when_nothing_is_suggested(client, db_path):
    stock(client, "xyzzy")
    html = client.get(f"/ingredients/{ingredient(db_path, 'xyzzy').id}/panel").text
    assert "Ticked from the name" not in html
    assert ticked(html, "food_groups") == [] and ticked(html, "allergens") == []


def test_a_reviewed_ingredient_never_gets_suggestions_again(client, db_path):
    stock(client, "chicken")
    item = ingredient(db_path, "chicken")
    save(client, item.id, "165")  # a person looked and ticked nothing
    html = client.get(f"/ingredients/{item.id}/panel").text
    assert ticked(html, "food_groups") == []
    assert "Ticked from the name" not in html


def test_the_full_page_holds_the_same_form_for_use_without_scripting(client, db_path):
    stock(client, "chicken")
    item = ingredient(db_path, "chicken")
    response = client.get(f"/ingredients/{item.id}")
    assert response.status_code == 200
    assert "<html" in response.text
    assert f'action="/ingredients/{item.id}"' in response.text
    assert ticked(response.text, "food_groups") == ["poultry"]


def test_a_missing_ingredient_is_a_404_for_the_popup_the_page_and_saving(client):
    assert client.get("/ingredients/999/panel").status_code == 404
    assert client.get("/ingredients/999").status_code == 404
    assert save(client, 999, "10").status_code == 404


# --- saving -----------------------------------------------------------------------------------


def test_saving_stores_everything_and_returns_to_the_list(client, db_path):
    stock(client, "chicken")
    item = ingredient(db_path, "chicken")
    response = save(client, item.id, "165", ["poultry"], ["celery"])
    assert response.status_code == 303
    assert response.headers["location"] == "/ingredients"
    saved = ingredient(db_path, "chicken")
    assert (saved.kcal, saved.food_groups, saved.allergens, saved.reviewed) == (
        165.0,
        (FoodGroup.POULTRY,),
        (Allergen.CELERY,),
        True,
    )


def test_a_comma_works_as_the_decimal_mark(client, db_path):
    stock(client, "chicken")
    item = ingredient(db_path, "chicken")
    save(client, item.id, "53,5")
    assert ingredient(db_path, "chicken").kcal == 53.5


def test_leaving_calories_blank_is_fine(client, db_path):
    stock(client, "chicken")
    item = ingredient(db_path, "chicken")
    save(client, item.id, "165")
    assert save(client, item.id, "").status_code == 303
    assert ingredient(db_path, "chicken").kcal is None


def test_unticking_everything_is_saved_as_none_and_counts_as_checked(client, db_path):
    stock(client, "chicken")
    item = ingredient(db_path, "chicken")
    save(client, item.id, "165", ["poultry"], ["celery"])
    save(client, item.id, "165")
    saved = ingredient(db_path, "chicken")
    assert (saved.food_groups, saved.allergens, saved.reviewed) == ((), (), True)


@pytest.mark.parametrize(
    "bad",
    [
        {"kcal": "lots"},
        {"kcal": "-5"},
        {"kcal": "951"},
        {"food_groups": ["beef"]},
        {"allergens": ["kiwi"]},
    ],
)
def test_bad_input_is_rejected_with_a_message_and_nothing_changes(client, db_path, bad):
    stock(client, "chicken")
    item = ingredient(db_path, "chicken")
    save(client, item.id, "165", ["poultry"], ["celery"])
    response = save(client, item.id, **{"kcal": "10", **bad})
    assert response.status_code == 400
    assert 'class="error"' in response.text
    kept = ingredient(db_path, "chicken")
    assert (kept.kcal, kept.food_groups, kept.allergens) == (
        165,
        (FoodGroup.POULTRY,),
        (Allergen.CELERY,),
    )


def test_a_per_piece_ingredient_allows_more_calories_than_a_per_100_g_one(client, db_path):
    stock(client, "pizza", "2", "count")
    assert save(client, ingredient(db_path, "pizza").id, "1200").status_code == 303
    stock(client, "lard", "500", "g")
    assert save(client, ingredient(db_path, "lard").id, "1200").status_code == 400


def test_a_rejected_save_keeps_what_was_typed_on_the_full_page(client, db_path):
    stock(client, "chicken")
    item = ingredient(db_path, "chicken")
    response = save(client, item.id, "lots", ["meat", "fish"], ["milk"])
    assert "<html" in response.text
    assert re.search(r'name="kcal"[^>]*value="lots"', response.text, re.S)
    assert ticked(response.text, "food_groups") == ["meat", "fish"]
    assert ticked(response.text, "allergens") == ["milk"]


def test_the_json_export_includes_the_ingredient_details(client, db_path):
    stock(client, "chicken")
    save(client, ingredient(db_path, "chicken").id, "165", ["poultry"], ["celery"])
    [chicken] = client.get("/export.json").json()["ingredients"]
    assert (chicken["kcal"], chicken["food_groups"], chicken["allergens"], chicken["reviewed"]) == (
        165,
        ["poultry"],
        ["celery"],
        True,
    )
