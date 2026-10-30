import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.repository import StockRepository
from emu_the_menu.web import create_app

BARCODE = "3017620422003"


class FakeOpenFoodFacts:
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
def db_path(tmp_path):
    return tmp_path / "emu.db"


@pytest.fixture
def client(db_path, off):
    app = create_app(db_path, today=lambda: date(2026, 10, 26), fetch_product=off)
    return TestClient(app, follow_redirects=False)


def nutella(kcal=539, name="Nutella"):
    return {
        "status": 1,
        "product": {"product_name": name, "nutriments": {"energy-kcal_100g": kcal}},
    }


def stock(client, name, quantity="500", unit="g"):
    client.post(
        "/items",
        data={"ingredient": name, "quantity": quantity, "unit": unit, "location": "pantry"},
    )


def ingredient(db_path, name):
    with StockRepository(db_path) as repo:
        return next(i for i in repo.ingredients() if i.name == name)


def lookup(client, db_path, name, barcode=BARCODE):
    item = ingredient(db_path, name)
    return client.post(f"/ingredients/{item.id}/lookup", data={"barcode": barcode})


# --- the lookup answers with JSON the popup fills in from ---------------------------------------


def test_a_found_product_gives_its_calories_and_a_message(client, db_path, off):
    stock(client, "hazelnut spread")
    off.response = nutella()
    response = lookup(client, db_path, "hazelnut spread")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["kcal"] == 539
    assert data["message"] == "Nutella: 539 kcal per 100 g. Data from Open Food Facts."


def test_the_message_says_per_100_ml_for_a_liquid(client, db_path, off):
    stock(client, "milk", "1", "l")
    off.response = nutella(42, "Whole milk")
    message = lookup(client, db_path, "milk").json()["message"]
    assert message.startswith("Whole milk: 42 kcal per 100 ml")


def test_a_lookup_never_saves_anything(client, db_path, off):
    stock(client, "hazelnut spread")
    off.response = nutella()
    lookup(client, db_path, "hazelnut spread")
    saved = ingredient(db_path, "hazelnut spread")
    assert (saved.kcal, saved.reviewed) == (None, False)


def test_the_same_barcode_is_fetched_only_once(client, db_path, off):
    stock(client, "hazelnut spread")
    off.response = nutella()
    lookup(client, db_path, "hazelnut spread")
    lookup(client, db_path, "hazelnut spread")
    assert len(off.calls) == 1


def test_spaces_in_the_barcode_are_ignored(client, db_path, off):
    stock(client, "hazelnut spread")
    off.response = nutella()
    assert lookup(client, db_path, "hazelnut spread", " 3017 6204 22003 ").status_code == 200
    assert off.calls == [BARCODE]


def test_a_product_that_lists_no_calories_says_so(client, db_path, off):
    stock(client, "hazelnut spread")
    off.response = {"status": 1, "product": {"product_name": "Mystery"}}
    response = lookup(client, db_path, "hazelnut spread")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False and data.get("kcal") is None
    assert "lists no calories" in data["message"]
    assert "Mystery" in data["message"]


def test_an_unknown_barcode_is_a_404_with_a_message(client, db_path, off):
    stock(client, "hazelnut spread")
    response = lookup(client, db_path, "hazelnut spread")
    assert response.status_code == 404
    assert response.json() == {"ok": False, "message": "No product found for that barcode."}


def test_a_bad_barcode_is_a_400_and_nothing_is_fetched(client, db_path, off):
    stock(client, "hazelnut spread")
    response = lookup(client, db_path, "hazelnut spread", "abc")
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "8 to 14 digits" in response.json()["message"]
    assert off.calls == []


def test_an_unreachable_service_is_a_503_that_says_to_enter_it_by_hand(client, db_path, off):
    stock(client, "hazelnut spread")
    off.error = OSError("offline")
    response = lookup(client, db_path, "hazelnut spread")
    assert response.status_code == 503
    assert "Could not reach Open Food Facts" in response.json()["message"]
    assert "by hand" in response.json()["message"]


def test_a_counted_ingredient_cannot_use_a_barcode_and_nothing_is_fetched(client, db_path, off):
    stock(client, "egg", "6", "count")
    off.response = nutella()
    response = lookup(client, db_path, "egg")
    assert response.status_code == 400
    assert "per piece" in response.json()["message"]
    assert "by hand" in response.json()["message"]
    assert off.calls == []


def test_a_lookup_for_a_missing_ingredient_is_a_404(client):
    assert client.post("/ingredients/999/lookup", data={"barcode": BARCODE}).status_code == 404


# --- the form -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("unit", "name"), [("g", "flour"), ("ml", "oil")])
def test_the_popup_has_a_barcode_box_for_things_measured_by_weight_or_volume(
    client, db_path, unit, name
):
    stock(client, name, "500", unit)
    item = ingredient(db_path, name)
    html = client.get(f"/ingredients/{item.id}/panel").text
    assert f'data-url="/ingredients/{item.id}/lookup"' in html
    assert "data-lookup-calories" in html and "data-barcode" in html
    assert re.search(r"<p[^>]*data-lookup-note[^>]*hidden", html)


def test_the_popup_of_a_counted_ingredient_explains_why_there_is_no_barcode_box(client, db_path):
    stock(client, "egg", "6", "count")
    html = client.get(f"/ingredients/{ingredient(db_path, 'egg').id}/panel").text
    assert "data-lookup-calories" not in html
    assert "per piece" in html and "by hand" in html


def test_one_shared_script_handles_the_look_up_button_and_the_enter_key(client):
    for path in ("/", "/ingredients", "/eaters"):
        page = client.get(path).text
        assert page.count('closest("[data-lookup-calories]")') == 1, path
        assert page.count("[data-barcode]") >= 1, path


def test_the_script_fills_in_only_the_calories_box(client):
    page = client.get("/ingredients").text
    script = page.split('closest("[data-lookup-calories]")')[1].split("</script>")[0]
    assert 'name="kcal"' in script
    assert "Looking up" in script
    assert ".submit(" not in script and "requestSubmit" not in script
