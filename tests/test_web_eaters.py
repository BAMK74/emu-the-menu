import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.eaters import Category, EaterRepository
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


def add(client, name="Sam", category="man", calorie_goal="", allergies="", dislikes=""):
    return client.post(
        "/eaters",
        data={
            "name": name,
            "category": category,
            "calorie_goal": calorie_goal,
            "allergies": allergies,
            "dislikes": dislikes,
        },
    )


def stored(db_path):
    with EaterRepository(db_path) as repo:
        return repo.list()


def card(client, name):
    """The list entry for one eater."""
    page = client.get("/eaters").text.split("<ul")[1]
    return next(li for li in page.split("<li") if f"<strong>{name}</strong>" in li)


def test_the_page_starts_empty_with_an_add_form(client):
    response = client.get("/eaters")
    assert response.status_code == 200
    assert "No one yet" in response.text
    for field in ("name", "category", "calorie_goal", "allergies", "dislikes"):
        assert f'name="{field}"' in response.text


def test_the_category_choices_are_all_four(client):
    page = client.get("/eaters").text
    for value in ("man", "woman", "teen", "child"):
        assert f'<option value="{value}"' in page


def test_adding_an_eater_redirects_and_lists_them(client, db_path):
    response = add(client, "Sam", "teen", "", "Peanuts, shellfish", "olives")
    assert response.status_code == 303
    assert response.headers["location"] == "/eaters"
    [eater] = stored(db_path)
    assert (eater.name, eater.category, eater.allergies) == (
        "Sam",
        Category.TEEN,
        ("peanuts", "shellfish"),
    )
    entry = card(client, "Sam")
    assert "teen" in entry
    assert "2200 kcal" in entry and "default" in entry
    assert "peanuts, shellfish" in entry
    assert "olives" in entry


def test_a_custom_calorie_goal_is_shown_as_custom(client):
    add(client, "Mia", "child", "1500")
    entry = card(client, "Mia")
    assert "1500 kcal" in entry
    assert "custom" in entry
    assert "default" not in entry.split("<details")[0]


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "   "},
        {"category": "robot"},
        {"calorie_goal": "plenty"},
        {"calorie_goal": "50"},
    ],
)
def test_bad_input_is_rejected_with_a_message_and_nothing_stored(client, db_path, bad):
    response = add(client, **bad)
    assert response.status_code == 400
    assert 'class="error"' in response.text
    assert stored(db_path) == []


def test_a_duplicate_name_is_rejected_and_the_typed_values_are_kept(client, db_path):
    add(client, "Sam")
    response = add(client, "sam", "woman", "", "eggs")
    assert response.status_code == 400
    assert "already" in response.text
    assert 'value="sam"' in response.text
    assert len(stored(db_path)) == 1


def test_the_edit_form_is_prefilled(client):
    add(client, "Sam", "woman", "1900", "eggs, milk", "olives")
    edit = card(client, "Sam").split("<details")[1]
    assert 'value="Sam"' in edit
    assert '<option value="woman" selected>' in edit
    assert 'value="1900"' in edit
    assert 'value="eggs, milk"' in edit
    assert 'value="olives"' in edit


def test_the_edit_form_shows_the_default_goal_as_a_hint_when_not_custom(client):
    add(client, "Sam", "man")
    edit = card(client, "Sam").split("<details")[1]
    assert 'placeholder="2500"' in edit


def test_updating_an_eater(client, db_path):
    add(client, "Sam", "man", "", "peanuts")
    [eater] = stored(db_path)
    response = client.post(
        f"/eaters/{eater.id}",
        data={
            "name": "Samuel",
            "category": "teen",
            "calorie_goal": "2100",
            "allergies": "eggs",
            "dislikes": "",
        },
    )
    assert response.status_code == 303
    [updated] = stored(db_path)
    assert (updated.name, updated.category, updated.calorie_goal, updated.allergies) == (
        "Samuel",
        Category.TEEN,
        2100,
        ("eggs",),
    )


def test_an_invalid_update_changes_nothing(client, db_path):
    add(client, "Sam", "man")
    [eater] = stored(db_path)
    response = client.post(
        f"/eaters/{eater.id}",
        data={
            "name": "Sam",
            "category": "man",
            "calorie_goal": "lots",
            "allergies": "",
            "dislikes": "",
        },
    )
    assert response.status_code == 400
    assert 'class="error"' in response.text
    assert stored(db_path)[0].calorie_goal_override is None


def test_updating_or_deleting_a_missing_eater_is_a_404(client):
    data = {"name": "X", "category": "man", "calorie_goal": "", "allergies": "", "dislikes": ""}
    assert client.post("/eaters/999", data=data).status_code == 404
    assert client.post("/eaters/999/delete").status_code == 404


def test_deleting_an_eater(client, db_path):
    add(client, "Sam")
    add(client, "Mia", "child")
    sam = stored(db_path)[0]
    assert client.post(f"/eaters/{sam.id}/delete").status_code == 303
    assert [e.name for e in stored(db_path)] == ["Mia"]


def test_every_page_links_to_the_others(client):
    for path in ("/", "/eaters", "/settings"):
        page = client.get(path).text
        assert 'href="/eaters"' in page
        assert 'href="/settings"' in page


def test_the_json_export_includes_the_eaters(client):
    add(client, "Sam", "teen", "2300", "peanuts", "olives")
    add(client, "Mia", "child")
    eaters = client.get("/export.json").json()["eaters"]
    assert eaters == [
        {
            "name": "Sam",
            "category": "teen",
            "calorie_goal": 2300,
            "calorie_goal_is_custom": True,
            "allergies": ["peanuts"],
            "dislikes": ["olives"],
        },
        {
            "name": "Mia",
            "category": "child",
            "calorie_goal": 1600,
            "calorie_goal_is_custom": False,
            "allergies": [],
            "dislikes": [],
        },
    ]


def test_the_page_escapes_what_was_typed(client):
    add(client, "<script>alert(1)</script>", "man", "", "<b>nuts</b>")
    page = client.get("/eaters").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert re.search(r"&lt;b&gt;nuts&lt;/b&gt;|&lt;b&gt;nuts", page)
