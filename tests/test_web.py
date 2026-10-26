from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.repository import StockRepository
from emu_the_menu.web import create_app


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path, today=lambda: date(2026, 10, 26)), follow_redirects=False)


def add(
    client,
    ingredient="flour",
    quantity="500",
    unit="g",
    location="pantry",
    expires_on="",
    in_amount="",
    in_unit="days",
    no_expiry=False,
):
    return client.post(
        "/items",
        data={
            "ingredient": ingredient,
            "quantity": quantity,
            "unit": unit,
            "location": location,
            "expires_on": expires_on,
            "in_amount": in_amount,
            "in_unit": in_unit,
            **({"no_expiry": "on"} if no_expiry else {}),
        },
    )


def stored(db_path):
    with StockRepository(db_path) as repo:
        return repo.list_items()


def test_empty_list_page_renders_the_add_form(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="ingredient"' in response.text


def test_adding_an_item_redirects_and_shows_it(client, db_path):
    response = add(client, "Flour", "1.5", "kg", "pantry", expires_on="2026-11-01")
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    [item] = stored(db_path)
    assert (item.ingredient, item.quantity) == ("flour", 1500)
    page = client.get("/").text
    assert "flour" in page
    assert "01/11/2026" in page


def test_list_is_ordered_by_expiry(client):
    add(client, "zucchini", expires_on="2026-12-01")
    add(client, "yoghurt")
    add(client, "apple", expires_on="2026-11-01")
    page = client.get("/").text.split("<ul>")[1]  # the stock list, not the suggestions
    assert page.index("apple") < page.index("zucchini") < page.index("yoghurt")


@pytest.mark.parametrize(
    "bad",
    [
        {"unit": "furlong"},
        {"quantity": "-3"},
        {"expires_on": "next tuesday"},
        {"expires_on": "28/10"},
        {"location": "garage"},
        {"ingredient": "   "},
    ],
)
def test_bad_input_is_rejected_with_a_message_and_nothing_stored(client, db_path, bad):
    response = add(client, **bad)
    assert response.status_code == 400
    assert "error" in response.text
    assert stored(db_path) == []


def test_unit_family_clash_is_a_warning_with_a_fix_not_an_error(client, db_path):
    add(client, "chicken", "500", "ml", "fridge")
    response = add(client, "chicken", "300", "g", "fridge")
    assert response.status_code == 409
    assert 'class="warning"' in response.text
    assert 'class="error"' not in response.text
    assert "chicken is stored in ml, but you entered g" in response.text
    assert "Store chicken in g instead" in response.text
    assert len(stored(db_path)) == 1


def test_the_warning_fix_switches_the_unit_and_adds_the_entry(client, db_path):
    add(client, "chicken", "500", "ml", "fridge")
    response = client.post(
        "/items",
        data={
            "ingredient": "chicken",
            "quantity": "300",
            "unit": "g",
            "location": "fridge",
            "switch_unit": "on",
        },
    )
    assert response.status_code == 303
    [item] = stored(db_path)
    assert (item.quantity, item.base_unit.value) == (800, "g")


def test_setting_a_quantity_in_another_unit_family_warns_and_can_be_fixed(client, db_path):
    add(client, "chicken", "500", "ml", "fridge")
    [item] = stored(db_path)
    response = client.post(f"/items/{item.id}/quantity", data={"quantity": "2", "unit": "kg"})
    assert response.status_code == 409
    assert "Store chicken in kg instead" in response.text
    assert stored(db_path)[0].base_unit.value == "ml"
    fix = client.post(
        f"/items/{item.id}/quantity", data={"quantity": "2", "unit": "kg", "switch_unit": "on"}
    )
    assert fix.status_code == 303
    [fixed] = stored(db_path)
    assert (fixed.quantity, fixed.base_unit.value) == (2000, "g")


def test_adjusting_quantity(client, db_path):
    add(client)
    [item] = stored(db_path)
    response = client.post(f"/items/{item.id}/quantity", data={"quantity": "2", "unit": "kg"})
    assert response.status_code == 303
    assert stored(db_path)[0].quantity == pytest.approx(2000)


def test_deleting_an_item(client, db_path):
    add(client)
    [item] = stored(db_path)
    assert client.post(f"/items/{item.id}/delete").status_code == 303
    assert stored(db_path) == []


def test_changing_a_missing_item_is_404(client):
    assert client.post("/items/999/delete").status_code == 404
    assert (
        client.post("/items/999/quantity", data={"quantity": "1", "unit": "g"}).status_code == 404
    )


def test_expiry_can_be_set_relative_to_today(client, db_path):
    add(client, "spinach", "1", "count", "fridge", in_amount="1", in_unit="weeks")
    assert stored(db_path)[0].expires_on == date(2026, 11, 2)


def test_leaving_both_expiry_fields_blank_means_no_expiry(client, db_path):
    add(client)
    assert stored(db_path)[0].expires_on is None


def test_no_expiry_checkbox_ignores_the_other_expiry_fields(client, db_path):
    add(client, no_expiry=True, expires_on="2026-11-01", in_amount="3")
    assert stored(db_path)[0].expires_on is None


@pytest.mark.parametrize(
    "bad",
    [
        {"in_amount": "0"},
        {"in_amount": "two"},
        {"in_amount": "2", "in_unit": "years"},
        {"in_amount": "2", "expires_on": "2026-11-01"},
    ],
)
def test_bad_expiry_is_rejected(client, db_path, bad):
    response = add(client, **bad)
    assert response.status_code == 400
    assert stored(db_path) == []


def test_relative_expiry_is_remembered_and_offered_next_time(client):
    add(client, "spinach", "1", "count", "fridge", in_amount="1", in_unit="weeks")
    page = client.get("/").text
    assert '"spinach": {"shelf_life": {"amount": 1, "unit": "weeks"}, "unit": "count"}' in page


def test_a_calendar_date_does_not_overwrite_the_remembered_shelf_life(client):
    add(client, "spinach", "1", "count", "fridge", in_amount="1", in_unit="weeks")
    add(client, "spinach", "1", "count", "fridge", expires_on="2026-11-20")
    assert '"shelf_life": {"amount": 1, "unit": "weeks"}' in client.get("/").text


def test_no_expiry_does_not_overwrite_the_remembered_shelf_life(client):
    add(client, "spinach", "1", "count", "fridge", in_amount="1", in_unit="weeks")
    add(client, "spinach", "1", "count", "fridge", no_expiry=True)
    assert '"shelf_life": {"amount": 1, "unit": "weeks"}' in client.get("/").text


def test_form_labels_say_what_each_field_is(client):
    page = client.get("/").text
    assert "Expires" in page
    assert "Ingredient" in page and "Quantity" in page


def test_ingredients_added_before_are_suggested(client):
    add(client, "flour")
    add(client, "milk", "1", "l", "fridge")
    page = client.get("/").text
    assert '<datalist id="ingredient-suggestions">' in page
    assert '<option value="flour">' in page and '<option value="milk">' in page
    assert '"milk": {"shelf_life": null, "unit": "ml"}' in page
