import gzip
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


def test_every_page_has_the_settings_overlay_and_link(client):
    for path in ("/", "/eaters"):
        page = client.get(path).text
        assert '<dialog id="settings-dialog"' in page
        assert 'id="settings-link"' in page


def test_the_settings_page_itself_has_no_overlay(client):
    assert "<dialog" not in client.get("/settings").text


def test_the_settings_panel_is_a_fragment_holding_the_form(client):
    response = client.get("/settings/panel", params={"next": "/"})
    assert response.status_code == 200
    assert "<html" not in response.text
    assert 'name="country"' in response.text
    assert '<input type="hidden" name="next" value="/">' in response.text


def test_the_settings_panel_remembers_which_page_to_return_to(client):
    page = client.get("/settings/panel", params={"next": "/eaters"}).text
    assert '<input type="hidden" name="next" value="/eaters">' in page


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example", "/items", ""])
def test_the_settings_panel_ignores_other_return_addresses(client, target):
    assert 'name="next"' not in client.get("/settings/panel", params={"next": target}).text


def test_saving_from_the_overlay_returns_to_the_page_you_were_on(client, db_path):
    response = client.post("/settings", data={"country": "NZ", "next": "/"})
    assert response.status_code == 303
    assert response.headers["location"] == "/?saved=1"
    with StockRepository(db_path) as repo:
        assert repo.household_country() == "NZ"
    assert "Settings saved" in client.get("/?saved=1").text
    assert "Settings saved" not in client.get("/").text

    response = client.post("/settings", data={"country": "AU", "next": "/eaters"})
    assert response.headers["location"] == "/eaters?saved=1"
    assert "Settings saved" in client.get("/eaters?saved=1").text
    assert "Settings saved" not in client.get("/eaters").text


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example", "/items"])
def test_saving_cannot_redirect_anywhere_but_a_known_page(client, target):
    response = client.post("/settings", data={"country": "NZ", "next": target})
    assert response.headers["location"] == "/settings?saved=1"


def test_the_panel_shows_the_saved_country(client):
    client.post("/settings", data={"country": "AU"})
    page = client.get("/settings/panel", params={"next": "/"}).text
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


def list_item_html(client, name):
    """The list entry for one ingredient."""
    page = client.get("/").text.split("<ul>")[1]
    return next(li for li in page.split("<li>") if f"<strong>{name}</strong>" in li)


def test_a_thousand_grams_is_shown_as_a_kilogram(client):
    add(client, "flour", "1000", "g")
    entry = list_item_html(client, "flour")
    assert "1 kg" in entry
    assert "1000 g" not in entry


def test_setting_one_kilogram_shows_one_kilogram(client, db_path):
    add(client, "flour", "500", "g")
    [item] = stored(db_path)
    client.post(f"/items/{item.id}/quantity", data={"quantity": "1", "unit": "kg"})
    assert "1 kg" in list_item_html(client, "flour")
    popup = client.get(f"/items/{item.id}/panel").text  # the adjust form lives in the popup now
    assert '<option value="kg" selected>kg</option>' in popup
    assert re.search(r'name="quantity"[^>]*value="1"', popup, re.S)


def test_millilitres_are_shown_as_litres_and_small_amounts_stay_small(client):
    add(client, "milk", "2500", "ml", "fridge")
    add(client, "flour", "250", "g")
    assert "2.5 l" in list_item_html(client, "milk")
    assert "250 g" in list_item_html(client, "flour")


# --- exports and the backup status --------------------------------------------------------


def test_the_json_export_downloads_everything(client):
    add(client, "flour", "1.5", "kg", "pantry")
    response = client.get("/export.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == 'attachment; filename="emu-2026-10-26.json"'
    data = response.json()
    assert data["exported_on"] == "2026-10-26"
    assert [(i["ingredient"], i["quantity"]) for i in data["stock_items"]] == [("flour", 1500)]


def test_the_database_export_is_a_restorable_backup(client, tmp_path):
    add(client, "flour", "500", "g", "pantry")
    response = client.get("/export.db.gz")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="emu-2026-10-26.db.gz"'
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(response.content))
    with StockRepository(restored) as repo:
        assert [(i.ingredient, i.quantity) for i in repo.list_items()] == [("flour", 500)]


@pytest.mark.parametrize("path", ["/export.json", "/export.db.gz"])
def test_exports_are_never_cached(client, path):
    assert client.get(path).headers["cache-control"] == "no-store"


def test_settings_offer_both_downloads_in_the_panel_and_on_the_page(client):
    panel = client.get("/settings/panel", params={"next": "/"}).text
    for page in (panel, client.get("/settings").text):
        assert 'href="/export.db.gz"' in page
        assert 'href="/export.json"' in page


@pytest.fixture
def backup_dir(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    return folder


@pytest.fixture
def backup_client(db_path, off, backup_dir):
    app = create_app(
        db_path, today=lambda: date(2026, 10, 26), fetch_product=off, backup_dir=backup_dir
    )
    return TestClient(app, follow_redirects=False)


def backup_status(client):
    page = client.get("/settings").text
    match = re.search(r'<p class="(backup-status[^"]*)"[^>]*>(.*?)</p>', page, re.S)
    return match.group(1), " ".join(match.group(2).split())


def test_without_a_backup_folder_the_page_says_backups_are_not_set_up(client):
    css, text = backup_status(client)
    assert "stale" not in css
    assert "not set up" in text


def test_a_folder_with_no_backups_is_a_warning(backup_client):
    css, text = backup_status(backup_client)
    assert "stale" in css
    assert "No nightly backup yet" in text


@pytest.mark.parametrize(
    ("backup_day", "text", "stale"),
    [
        (date(2026, 10, 26), "Last nightly backup: today", False),
        (date(2026, 10, 25), "Last nightly backup: yesterday", False),
        (date(2026, 10, 24), "Last nightly backup: 2 days ago", False),
        (date(2026, 10, 22), "Last nightly backup was 4 days ago. Check the Pi.", True),
    ],
)
def test_the_last_backup_is_shown_and_an_old_one_is_flagged(
    backup_client, backup_dir, backup_day, text, stale
):
    (backup_dir / f"emu-{backup_day.isoformat()}.db.gz").write_bytes(b"x")
    css, shown = backup_status(backup_client)
    assert text in shown
    assert ("stale" in css) is stale


def test_the_backup_status_is_also_in_the_panel(backup_client, backup_dir):
    (backup_dir / "emu-2026-10-26.db.gz").write_bytes(b"x")
    panel = backup_client.get("/settings/panel", params={"next": "/"}).text
    assert "Last nightly backup: today" in " ".join(panel.split())


# --- layout: header, overview and expiry chips -----------------------------------------------


@pytest.mark.parametrize(
    ("path", "current"),
    [("/", "Stock"), ("/eaters", "Eaters"), ("/settings", "Settings")],
)
def test_the_header_marks_the_page_you_are_on(client, path, current):
    page = client.get(path).text
    assert '<header class="topbar">' in page
    assert 'class="brand"' in page and "Emu the Menu" in page
    marked = re.findall(r'<a[^>]*aria-current="page"[^>]*>([^<]*)</a>', page)
    assert marked == [current]
    for link in ('href="/"', 'href="/eaters"', 'href="/settings"'):
        assert link in page


def test_the_disclaimer_page_marks_none_of_the_main_links(client):
    assert not re.findall(r'<a[^>]*aria-current="page"', client.get("/disclaimer").text)


@pytest.mark.parametrize(
    ("path", "layout"),
    [("/", "wide"), ("/eaters", "wide"), ("/disclaimer", "reading")],
)
def test_pages_choose_a_wide_or_a_reading_layout(client, path, layout):
    assert f'<main class="{layout}">' in client.get(path).text


def chips(client, name):
    page = client.get("/").text.split("<ul>")[1]
    entry = next(li for li in page.split("<li>") if f"<strong>{name}</strong>" in li)
    return [
        (css.split()[-1], " ".join(text.split()))
        for css, text in re.findall(r'<span class="(chip[^"]*)">([^<]*)</span>', entry)
    ]


def test_expiry_chips_say_how_soon_something_goes_off(client):
    # Today is 2026-10-26 in these tests.
    for name, day in [
        ("gone", "2026-10-25"),
        ("today", "2026-10-26"),
        ("tomorrow", "2026-10-27"),
        ("three", "2026-10-29"),
        ("later", "2026-11-20"),
    ]:
        add(client, name, expires_on=day)
    add(client, "never")
    assert ("chip-expired", "Expired 25/10/2026") in chips(client, "gone")
    assert ("chip-soon", "Expires today") in chips(client, "today")
    assert ("chip-soon", "Expires tomorrow") in chips(client, "tomorrow")
    assert ("chip-soon", "Expires in 3 days") in chips(client, "three")
    assert ("chip-ok", "Expires 20/11/2026") in chips(client, "later")
    assert ("chip-none", "No expiry") in chips(client, "never")


def test_each_item_shows_where_it_is_kept(client):
    add(client, "milk", "1", "l", "fridge")
    assert ("chip", "Fridge") in chips(client, "milk")


def overview(client):
    match = re.search(r'<p class="summary">(.*?)</p>', client.get("/").text, re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split()) if match else None


def test_the_overview_counts_items_expiring_soon_and_expired(client):
    add(client, "gone", expires_on="2026-10-25")
    add(client, "today", expires_on="2026-10-26")
    add(client, "three", expires_on="2026-10-29")
    add(client, "four", expires_on="2026-10-30")
    add(client, "never")
    assert overview(client) == "5 items 2 expiring within 3 days 1 expired"


def test_the_overview_leaves_out_zero_counts_and_uses_the_singular(client):
    add(client, "flour")
    assert overview(client) == "1 item"


def test_an_empty_stock_has_no_overview(client):
    assert overview(client) is None
