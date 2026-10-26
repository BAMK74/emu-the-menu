import pytest
from fastapi.testclient import TestClient

from emu_the_menu.repository import StockRepository
from emu_the_menu.web import create_app


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path), follow_redirects=False)


def add(client, ingredient="flour", quantity="500", unit="g", location="pantry", expires_on=""):
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


def stored(db_path):
    with StockRepository(db_path) as repo:
        return repo.list_items()


def test_empty_list_page_renders_the_add_form(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="ingredient"' in response.text


def test_adding_an_item_redirects_and_shows_it(client, db_path):
    response = add(client, "Flour", "1.5", "kg", "pantry", "2026-11-01")
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    [item] = stored(db_path)
    assert (item.ingredient, item.quantity) == ("flour", 1500)
    page = client.get("/").text
    assert "flour" in page
    assert "2026-11-01" in page


def test_list_is_ordered_by_expiry(client):
    add(client, "zucchini", expires_on="2026-12-01")
    add(client, "yoghurt")
    add(client, "apple", expires_on="2026-11-01")
    page = client.get("/").text
    assert page.index("apple") < page.index("zucchini") < page.index("yoghurt")


@pytest.mark.parametrize(
    "bad",
    [
        {"unit": "furlong"},
        {"quantity": "-3"},
        {"expires_on": "next tuesday"},
        {"location": "garage"},
        {"ingredient": "   "},
    ],
)
def test_bad_input_is_rejected_with_a_message_and_nothing_stored(client, db_path, bad):
    response = add(client, **bad)
    assert response.status_code == 400
    assert "error" in response.text
    assert stored(db_path) == []


def test_unit_family_clash_is_reported(client, db_path):
    add(client, "milk", "1", "l", "fridge")
    response = add(client, "milk", "100", "g", "fridge")
    assert response.status_code == 400
    assert len(stored(db_path)) == 1


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
