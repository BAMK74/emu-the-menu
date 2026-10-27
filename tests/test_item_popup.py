import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.repository import ItemNotFoundError, StockRepository
from emu_the_menu.stock import Location
from emu_the_menu.units import IncompatibleUnitError
from emu_the_menu.web import create_app

TODAY = date(2026, 10, 26)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


@pytest.fixture
def client(db_path):
    app = create_app(db_path, today=lambda: TODAY, fetch_product=lambda code: {"status": 0})
    return TestClient(app, follow_redirects=False)


def add(client, ingredient="flour", quantity="1500", unit="g", location="pantry", expires_on=""):
    return client.post(
        "/items",
        data={
            "ingredient": ingredient,
            "quantity": quantity,
            "unit": unit,
            "location": location,
            "expires_on": expires_on,
        },
    )


def only_item(db_path):
    with StockRepository(db_path) as repo:
        [item] = repo.list_items()
    return item


def save(client, item_id, **fields):
    data = {"quantity": "2", "unit": "kg", "location": "fridge", "expires_on": "2026-11-20"}
    return client.post(f"/items/{item_id}", data={**data, **fields})


# --- the repository ---------------------------------------------------------------------------


@pytest.fixture
def repo(db_path):
    with StockRepository(db_path) as repo:
        yield repo


def test_update_item_changes_quantity_location_and_expiry(repo):
    repo.add("flour", 500, "g", Location.PANTRY)
    [item] = repo.list_items()
    repo.update_item(item.id, 2, "kg", Location.FRIDGE, date(2026, 11, 20))
    [updated] = repo.list_items()
    assert (updated.quantity, updated.location, updated.expires_on) == (
        2000,
        Location.FRIDGE,
        date(2026, 11, 20),
    )
    assert updated.id == item.id


def test_update_item_can_clear_the_expiry(repo):
    repo.add("flour", 500, "g", Location.PANTRY, date(2026, 11, 1))
    [item] = repo.list_items()
    repo.update_item(item.id, 500, "g", Location.PANTRY, None)
    assert repo.list_items()[0].expires_on is None


def test_update_item_rejects_another_unit_family_and_changes_nothing(repo):
    repo.add("milk", 1, "l", Location.FRIDGE)
    [item] = repo.list_items()
    with pytest.raises(IncompatibleUnitError):
        repo.update_item(item.id, 5, "kg", Location.PANTRY, date(2026, 11, 1))
    unchanged = repo.list_items()[0]
    assert (unchanged.quantity, unchanged.location, unchanged.expires_on) == (
        1000,
        Location.FRIDGE,
        None,
    )


def test_update_item_rejects_a_negative_quantity(repo):
    repo.add("flour", 500, "g", Location.PANTRY)
    [item] = repo.list_items()
    with pytest.raises(ValueError, match="negative"):
        repo.update_item(item.id, -1, "g", Location.PANTRY, None)
    assert repo.list_items()[0].quantity == 500


def test_update_item_rejects_an_unknown_location(repo):
    repo.add("flour", 500, "g", Location.PANTRY)
    [item] = repo.list_items()
    with pytest.raises(ValueError):
        repo.update_item(item.id, 500, "g", "garage", None)
    assert repo.list_items()[0].location is Location.PANTRY


def test_update_item_for_a_missing_item_raises(repo):
    with pytest.raises(ItemNotFoundError):
        repo.update_item(999, 1, "g", Location.PANTRY, None)


# --- the cards ----------------------------------------------------------------------------------


def test_each_card_is_a_link_that_opens_the_popup(client, db_path):
    add(client)
    item = only_item(db_path)
    page = client.get("/").text
    assert (
        f'<a class="card-link" href="/items/{item.id}" data-panel="/items/{item.id}/panel">' in page
    )
    assert '<dialog id="item-dialog"' in page
    assert "Click an item to adjust it" in page


def test_the_cards_no_longer_carry_their_own_forms(client, db_path):
    add(client)
    item = only_item(db_path)
    list_html = client.get("/").text.split("<ul>")[1]
    assert f"/items/{item.id}/quantity" not in list_html
    assert f"/items/{item.id}/delete" not in list_html
    assert "<form" not in list_html


# --- the popup content --------------------------------------------------------------------------


def test_the_panel_is_a_fragment_prefilled_with_the_item(client, db_path):
    add(client, "Flour", "1500", "g", "pantry", "2026-11-20")
    item = only_item(db_path)
    response = client.get(f"/items/{item.id}/panel")
    assert response.status_code == 200
    html = response.text
    assert "<html" not in html
    assert "flour" in html.lower()
    assert f'action="/items/{item.id}"' in html
    assert re.search(r'name="quantity"[^>]*value="1.5"', html, re.S)  # 1500 g reads as 1.5 kg
    assert '<option value="kg" selected>kg</option>' in html
    assert '<option value="pantry" selected>' in html
    assert re.search(r'name="expires_on"[^>]*value="2026-11-20"', html, re.S)


def test_the_panel_offers_every_location_and_unit(client, db_path):
    add(client)
    item = only_item(db_path)
    html = client.get(f"/items/{item.id}/panel").text
    for location in ("fridge", "freezer", "pantry"):
        assert f'<option value="{location}"' in html
    for unit in ("g", "kg", "ml", "l", "count", "bunch", "head"):
        assert f'<option value="{unit}"' in html


def test_an_item_with_no_expiry_shows_the_box_ticked_and_the_date_off(client, db_path):
    add(client)
    item = only_item(db_path)
    html = client.get(f"/items/{item.id}/panel").text
    assert re.search(r'<input[^>]*name="no_expiry"[^>]*checked', html)
    assert re.search(r'<input[^>]*name="expires_on"[^>]*disabled', html)


def test_an_item_with_an_expiry_shows_the_box_unticked(client, db_path):
    add(client, expires_on="2026-11-20")
    item = only_item(db_path)
    html = client.get(f"/items/{item.id}/panel").text
    assert not re.search(r'<input[^>]*name="no_expiry"[^>]*checked', html)
    assert not re.search(r'<input[^>]*name="expires_on"[^>]*disabled', html)


def test_the_panel_has_a_delete_button_that_asks_first(client, db_path):
    add(client, "Flour")
    item = only_item(db_path)
    html = client.get(f"/items/{item.id}/panel").text
    assert f'action="/items/{item.id}/delete" data-confirm="Remove flour from stock?"' in html


def test_the_panel_has_a_cancel_button_that_closes_the_popup(client, db_path):
    add(client)
    item = only_item(db_path)
    assert "data-close-dialog" in client.get(f"/items/{item.id}/panel").text


def test_the_full_page_holds_the_same_form_for_use_without_scripting(client, db_path):
    add(client, "Flour", "1500", "g", "pantry", "2026-11-20")
    item = only_item(db_path)
    response = client.get(f"/items/{item.id}")
    assert response.status_code == 200
    assert "<html" in response.text
    assert f'action="/items/{item.id}"' in response.text
    assert re.search(r'name="quantity"[^>]*value="1.5"', response.text, re.S)


def test_a_missing_item_is_a_404_for_the_panel_and_the_page(client):
    assert client.get("/items/999/panel").status_code == 404
    assert client.get("/items/999").status_code == 404


# --- saving -------------------------------------------------------------------------------------


def test_saving_changes_everything_and_returns_to_the_stock_page(client, db_path):
    add(client, "Flour", "500", "g", "pantry")
    item = only_item(db_path)
    response = save(client, item.id)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    updated = only_item(db_path)
    assert (updated.quantity, updated.location, updated.expires_on) == (
        2000,
        Location.FRIDGE,
        date(2026, 11, 20),
    )


def test_ticking_no_expiry_clears_the_date(client, db_path):
    add(client, expires_on="2026-11-01")
    item = only_item(db_path)
    assert save(client, item.id, no_expiry="on").status_code == 303
    assert only_item(db_path).expires_on is None


def test_a_blank_date_means_no_expiry(client, db_path):
    add(client, expires_on="2026-11-01")
    item = only_item(db_path)
    assert save(client, item.id, expires_on="").status_code == 303
    assert only_item(db_path).expires_on is None


@pytest.mark.parametrize(
    "bad",
    [
        {"quantity": "-3"},
        {"quantity": "lots"},
        {"quantity": ""},
        {"unit": "furlong"},
        {"location": "garage"},
        {"expires_on": "next tuesday"},
    ],
)
def test_bad_input_is_rejected_with_a_message_and_nothing_changes(client, db_path, bad):
    add(client, "Flour", "500", "g", "pantry")
    item = only_item(db_path)
    response = save(client, item.id, **bad)
    assert response.status_code == 400
    assert 'class="error"' in response.text
    unchanged = only_item(db_path)
    assert (unchanged.quantity, unchanged.location, unchanged.expires_on) == (
        500,
        Location.PANTRY,
        None,
    )


def test_a_rejected_save_keeps_what_was_typed_on_the_full_page(client, db_path):
    add(client, "Flour", "500", "g", "pantry")
    item = only_item(db_path)
    response = save(client, item.id, quantity="-3")
    assert "<html" in response.text
    assert re.search(r'name="quantity"[^>]*value="-3"', response.text, re.S)


def test_another_unit_family_is_a_warning_with_a_one_tap_fix(client, db_path):
    add(client, "chicken", "500", "ml", "fridge")
    item = only_item(db_path)
    response = save(client, item.id, quantity="2", unit="kg", location="freezer")
    assert response.status_code == 409
    assert 'class="warning"' in response.text
    assert "chicken is stored in ml, but you entered kg" in response.text
    assert "Store chicken in kg instead" in response.text
    assert only_item(db_path).base_unit.value == "ml"  # nothing changed yet
    fix = save(client, item.id, quantity="2", unit="kg", location="freezer", switch_unit="on")
    assert fix.status_code == 303
    fixed = only_item(db_path)
    assert (fixed.base_unit.value, fixed.quantity, fixed.location) == ("g", 2000, Location.FREEZER)


def test_saving_a_missing_item_is_a_404(client):
    assert save(client, 999).status_code == 404


def test_the_old_set_quantity_route_still_works(client, db_path):
    add(client, "Flour", "500", "g", "pantry")
    item = only_item(db_path)
    response = client.post(f"/items/{item.id}/quantity", data={"quantity": "2", "unit": "kg"})
    assert response.status_code == 303
    assert only_item(db_path).quantity == 2000


# --- the confirm prompt is shared ---------------------------------------------------------------


def test_one_shared_handler_asks_before_anything_marked_data_confirm_is_sent(client):
    for path in ("/", "/eaters"):
        page = client.get(path).text
        assert page.count('closest("form[data-confirm]")') == 1, path
        assert 'querySelectorAll("form[data-confirm]")' not in page, path


def test_the_click_hint_is_in_italics(client):
    add(client)
    assert '<em class="hint">Click an item to adjust it.</em>' in client.get("/").text


def test_there_is_no_hint_when_there_is_nothing_to_click(client):
    assert '<em class="hint">' not in client.get("/").text
