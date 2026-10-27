import json
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.repository import StockRepository
from emu_the_menu.web import create_app


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


class FakeOpenFoodFacts:
    """Stands in for the network: set `response` or `error`, and see `calls`."""

    def __init__(self):
        self.response, self.error, self.calls = {"status": 0}, None, []

    def __call__(self, barcode):
        self.calls.append(barcode)
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def off():
    return FakeOpenFoodFacts()


@pytest.fixture
def client(db_path, off):
    app = create_app(db_path, today=lambda: date(2026, 10, 26), fetch_product=off)
    return TestClient(app, follow_redirects=False)


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


def suggestions(client):
    """The remembered-ingredient data the page hands to its script."""
    page = client.get("/").text
    match = re.search(r"const suggestions = (.*?);$", page, re.MULTILINE)
    return json.loads(match.group(1))


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
    assert suggestions(client)["spinach"]["shelf_life"] == {"amount": 1, "unit": "weeks"}
    assert suggestions(client)["spinach"]["unit"] == "count"


def test_a_calendar_date_does_not_overwrite_the_remembered_shelf_life(client):
    add(client, "spinach", "1", "count", "fridge", in_amount="1", in_unit="weeks")
    add(client, "spinach", "1", "count", "fridge", expires_on="2026-11-20")
    assert suggestions(client)["spinach"]["shelf_life"] == {"amount": 1, "unit": "weeks"}


def test_no_expiry_does_not_overwrite_the_remembered_shelf_life(client):
    add(client, "spinach", "1", "count", "fridge", in_amount="1", in_unit="weeks")
    add(client, "spinach", "1", "count", "fridge", no_expiry=True)
    assert suggestions(client)["spinach"]["shelf_life"] == {"amount": 1, "unit": "weeks"}


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
    assert suggestions(client)["milk"]["unit"] == "ml"
    assert suggestions(client)["milk"]["shelf_life"] is None


def test_the_last_quantity_entered_is_offered_next_time(client):
    add(client, "lettuce", "2.5", "head", "fridge")
    assert suggestions(client)["lettuce"]["last"] == {"quantity": 2.5, "unit": "head"}


def test_settings_page_lists_countries_with_not_set_selected_at_first(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert '<option value="AU">Australia</option>' in response.text
    assert '<option value="" selected>Not set</option>' in response.text
    assert '<option value="AU" selected>' not in response.text


def test_saving_the_household_country(client, db_path):
    response = client.post("/settings", data={"country": "AU"})
    assert response.status_code == 303
    with StockRepository(db_path) as repo:
        assert repo.household_country() == "AU"
    assert '<option value="AU" selected>Australia</option>' in client.get("/settings").text


def test_an_unknown_country_is_an_error_on_the_settings_page(client, db_path):
    response = client.post("/settings", data={"country": "ZZ"})
    assert response.status_code == 400
    assert 'class="error"' in response.text
    with StockRepository(db_path) as repo:
        assert repo.household_country() is None


def test_the_stock_page_links_to_settings(client):
    assert 'href="/settings"' in client.get("/").text


def test_settings_open_as_an_overlay_on_the_stock_page(client):
    page = client.get("/").text
    assert '<dialog id="settings-dialog"' in page
    assert 'name="country"' in page.split('<dialog id="settings-dialog"')[1]
    assert '<input type="hidden" name="next" value="/">' in page


def test_saving_from_the_overlay_returns_to_the_stock_page(client, db_path):
    response = client.post("/settings", data={"country": "NZ", "next": "/"})
    assert response.status_code == 303
    assert response.headers["location"] == "/?saved=1"
    with StockRepository(db_path) as repo:
        assert repo.household_country() == "NZ"
    assert "Settings saved" in client.get("/?saved=1").text
    assert "Settings saved" not in client.get("/").text


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example", "/items"])
def test_the_overlay_cannot_redirect_anywhere_but_the_stock_page(client, target):
    response = client.post("/settings", data={"country": "NZ", "next": target})
    assert response.headers["location"] == "/settings?saved=1"


def test_the_overlay_shows_the_saved_country(client):
    client.post("/settings", data={"country": "AU"})
    page = client.get("/").text.split('<dialog id="settings-dialog"')[1]
    assert '<option value="AU" selected>Australia</option>' in page


BARCODE = "3274080005003"


def penne(**extra):
    product = {"product_name": "Penne", "product_quantity": 500, "product_quantity_unit": "g"}
    return {"status": 1, "product": {**product, **extra}}


def lookup(client, barcode=BARCODE, **fields):
    data = {
        "barcode": barcode,
        "ingredient": "",
        "quantity": "",
        "unit": "ml",
        "location": "pantry",
    }
    return client.post("/lookup", data={**data, **fields})


def test_the_add_form_has_a_barcode_field_and_a_look_up_button(client):
    page = client.get("/").text
    assert 'name="barcode"' in page
    assert 'formaction="/lookup"' in page


def test_looking_up_a_barcode_fills_in_name_quantity_and_unit(client, off, db_path):
    off.response = penne()
    response = lookup(client)
    assert response.status_code == 200
    assert 'name="ingredient"' in response.text and 'value="Penne"' in response.text
    assert re.search(r'name="quantity"[^>]*value="500"', response.text, re.S)
    assert '<option value="g" selected>g</option>' in response.text
    assert "Open Food Facts" in response.text
    assert stored(db_path) == []  # a lookup never changes Stock


def test_a_typed_ingredient_name_is_kept(client, off):
    off.response = penne()
    response = lookup(client, ingredient="pasta")
    assert 'value="pasta"' in response.text
    assert 'value="Penne"' not in response.text


def test_other_fields_are_kept_when_looking_up(client, off):
    off.response = penne()
    response = lookup(client, location="fridge", in_amount="3", in_unit="weeks")
    assert '<option value="fridge" selected>fridge</option>' in response.text
    assert 'value="3"' in response.text
    assert '<option value="weeks" selected>weeks</option>' in response.text
    assert f'value="{BARCODE}"' in response.text


def test_an_unknown_barcode_says_so_and_keeps_the_form(client):
    response = lookup(client, ingredient="pasta")
    assert response.status_code == 200
    assert "No product found" in response.text
    assert 'value="pasta"' in response.text


def test_a_product_without_a_pack_size_asks_for_the_quantity(client, off):
    off.response = {"status": 1, "product": {"product_name": "Penne"}}
    response = lookup(client)
    assert "no pack size" in response.text
    assert 'value="Penne"' in response.text


def test_an_unreachable_service_is_a_notice_not_an_error(client, off):
    off.error = OSError("offline")
    response = lookup(client, ingredient="pasta")
    assert response.status_code == 200
    assert "Could not reach Open Food Facts" in response.text
    assert 'class="error"' not in response.text
    assert 'value="pasta"' in response.text


def test_a_bad_barcode_is_an_error(client, off):
    response = lookup(client, barcode="abc")
    assert response.status_code == 400
    assert 'class="error"' in response.text
    assert off.calls == []


def test_a_barcode_is_fetched_only_once(client, off):
    off.response = penne()
    lookup(client)
    lookup(client)
    assert len(off.calls) == 1


def test_adding_stock_never_calls_the_lookup(client, off):
    client.post(
        "/items",
        data={
            "ingredient": "pasta",
            "quantity": "500",
            "unit": "g",
            "location": "pantry",
            "barcode": BARCODE,
        },
    )
    assert off.calls == []


def test_the_zxing_library_is_served_from_the_app(client):
    response = client.get("/static/zxing.min.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "BrowserMultiFormatReader" in response.text


def test_the_add_form_can_scan_a_barcode_from_a_photo(client):
    page = client.get("/").text
    assert '<script src="/static/zxing.min.js"></script>' in page
    assert re.search(r'<input[^>]*type="file"[^>]*capture="environment"', page)
    assert 'id="scan-button"' in page
    assert 'accept="image/*"' in page


def second_scan(client, off, response, **fields):
    """Scan a second product after the first one filled in Penne, 500 g."""
    off.response = response
    return lookup(
        client,
        barcode="4006381333931",
        ingredient="Penne",
        quantity="500",
        unit="g",
        auto_ingredient="Penne",
        auto_quantity="500",
        **fields,
    )


def field_value(page, name):
    match = re.search(rf'name="{name}"[^>]*?value="([^"]*)"', page, re.S)
    return match.group(1) if match else None


def test_a_lookup_remembers_which_fields_it_filled_in(client, off):
    off.response = penne()
    page = lookup(client).text
    assert field_value(page, "auto_ingredient") == "Penne"
    assert field_value(page, "auto_quantity") == "500"


def test_scanning_a_second_product_replaces_the_first_ones_details(client, off):
    page = second_scan(client, off, penne(product_name="Fusilli", product_quantity=250)).text
    assert field_value(page, "ingredient") == "Fusilli"
    assert field_value(page, "quantity") == "250"
    assert field_value(page, "auto_ingredient") == "Fusilli"


def test_a_name_typed_after_the_first_scan_is_kept_on_the_second(client, off):
    off.response = penne(product_name="Fusilli")
    response = lookup(
        client, barcode="4006381333931", ingredient="my pasta", auto_ingredient="Penne"
    )
    assert field_value(response.text, "ingredient") == "my pasta"
    assert field_value(response.text, "auto_ingredient") == ""


def test_a_quantity_edited_after_the_first_scan_is_kept_on_the_second(client, off):
    off.response = penne(product_quantity=250)
    response = lookup(client, barcode="4006381333931", quantity="3", auto_quantity="500")
    assert field_value(response.text, "quantity") == "3"


def test_scanning_an_unknown_product_clears_the_first_ones_details(client, off):
    page = second_scan(client, off, {"status": 0}).text
    assert field_value(page, "ingredient") == ""
    assert field_value(page, "quantity") == ""
    assert "No product found" in page


def test_scanning_a_product_without_a_pack_size_clears_the_old_quantity(client, off):
    page = second_scan(client, off, {"status": 1, "product": {"product_name": "Fusilli"}}).text
    assert field_value(page, "ingredient") == "Fusilli"
    assert field_value(page, "quantity") == ""


def test_an_unreachable_service_clears_the_first_ones_details(client, off):
    off.error = OSError("offline")
    page = second_scan(client, off, None).text
    assert field_value(page, "ingredient") == ""
    assert field_value(page, "quantity") == ""


def test_a_bad_barcode_keeps_the_form_as_it_was(client, off):
    response = lookup(
        client, barcode="abc", ingredient="Penne", quantity="500", auto_ingredient="Penne"
    )
    assert response.status_code == 400
    assert field_value(response.text, "ingredient") == "Penne"
