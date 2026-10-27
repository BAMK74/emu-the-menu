import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.eaters import CALORIE_ADJUSTMENTS, Category, EaterRepository
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


def add(
    client,
    name="Sam",
    category="man",
    calorie_adjustment="0",
    allergies="",
    dislikes="",
    diet="omnivore",
):
    return client.post(
        "/eaters",
        data={
            "name": name,
            "category": category,
            "calorie_adjustment": calorie_adjustment,
            "allergies": allergies,
            "dislikes": dislikes,
            "diet": diet,
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
    for field in ("name", "category", "calorie_adjustment", "allergies", "dislikes"):
        assert f'name="{field}"' in response.text


def test_the_category_choices_are_all_four(client):
    page = client.get("/eaters").text
    for value in ("man", "woman", "teen", "child"):
        assert f'<option value="{value}"' in page


def test_the_calorie_dropdown_offers_every_adjustment_with_default_selected(client):
    page = client.get("/eaters").text
    form = page.split('action="/eaters"')[1].split("</form>")[0]
    select = form.split('name="calorie_adjustment"')[1].split("</select>")[0]
    values = [int(v) for v in re.findall(r'<option value="(-?\d+)"', select)]
    assert values == list(CALORIE_ADJUSTMENTS)
    assert '<option value="0" selected>Default</option>' in select
    assert '<option value="200">+200 kcal</option>' in select
    assert '<option value="-300">-300 kcal</option>' in select


def test_adding_an_eater_redirects_and_lists_them(client, db_path):
    response = add(client, "Sam", "teen", "0", "Peanuts, shellfish", "olives")
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
    assert "2200 kcal a day (default)" in entry
    assert "peanuts, shellfish" in entry
    assert "olives" in entry


def test_an_adjusted_goal_shows_the_default_and_the_difference(client):
    add(client, "Mia", "child", "-100")
    add(client, "Ann", "woman", "300")
    assert "1500 kcal a day (default 1600, -100)" in card(client, "Mia")
    assert "2300 kcal a day (default 2000, +300)" in card(client, "Ann")


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "   "},
        {"category": "robot"},
        {"calorie_adjustment": "plenty"},
        {"calorie_adjustment": "150"},
        {"calorie_adjustment": "5000"},
    ],
)
def test_bad_input_is_rejected_with_a_message_and_nothing_stored(client, db_path, bad):
    response = add(client, **bad)
    assert response.status_code == 400
    assert 'class="error"' in response.text
    assert stored(db_path) == []


def test_a_duplicate_name_is_rejected_and_the_typed_values_are_kept(client, db_path):
    add(client, "Sam")
    response = add(client, "sam", "woman", "0", "eggs")
    assert response.status_code == 400
    assert "already" in response.text
    assert 'value="sam"' in response.text
    assert len(stored(db_path)) == 1


def test_the_edit_form_is_prefilled(client):
    add(client, "Sam", "woman", "200", "eggs, milk", "olives")
    edit = card(client, "Sam").split("<details")[1]
    assert 'value="Sam"' in edit
    assert '<option value="woman" selected>' in edit
    assert '<option value="200" selected>+200 kcal</option>' in edit
    assert 'value="eggs, milk"' in edit
    assert 'value="olives"' in edit


def test_updating_an_eater(client, db_path):
    add(client, "Sam", "man", "0", "peanuts")
    [eater] = stored(db_path)
    response = client.post(
        f"/eaters/{eater.id}",
        data={
            "name": "Samuel",
            "category": "teen",
            "calorie_adjustment": "-100",
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
            "calorie_adjustment": "123",
            "allergies": "",
            "dislikes": "",
        },
    )
    assert response.status_code == 400
    assert 'class="error"' in response.text
    assert stored(db_path)[0].calorie_adjustment == 0


def test_updating_or_deleting_a_missing_eater_is_a_404(client):
    data = {
        "name": "X",
        "category": "man",
        "calorie_adjustment": "0",
        "allergies": "",
        "dislikes": "",
    }
    assert client.post("/eaters/999", data=data).status_code == 404
    assert client.post("/eaters/999/delete").status_code == 404


def test_deleting_an_eater(client, db_path):
    add(client, "Sam")
    add(client, "Mia", "child")
    sam = stored(db_path)[0]
    assert client.post(f"/eaters/{sam.id}/delete").status_code == 303
    assert [e.name for e in stored(db_path)] == ["Mia"]


# --- default goals per category -------------------------------------------------------------


def defaults_form(page):
    return page.split('action="/eaters/defaults"')[1].split("</form>")[0]


def test_the_page_shows_a_default_goal_for_each_category(client):
    form = defaults_form(client.get("/eaters").text)
    for category, goal in (("man", 2500), ("woman", 2000), ("teen", 2200), ("child", 1600)):
        assert re.search(rf'name="goal_{category}"[^>]*value="{goal}"', form, re.S)


def save_defaults(client, **goals):
    data = {"goal_man": "", "goal_woman": "", "goal_teen": "", "goal_child": ""}
    return client.post("/eaters/defaults", data={**data, **goals})


def test_saving_default_goals_changes_everyone_in_that_category(client, db_path):
    add(client, "Ann", "woman", "0")
    add(client, "Beth", "woman", "200")
    response = save_defaults(client, goal_woman="2100", goal_man="2600")
    assert response.status_code == 303
    assert response.headers["location"] == "/eaters"
    assert "2100 kcal a day (default)" in card(client, "Ann")
    assert "2300 kcal a day (default 2100, +200)" in card(client, "Beth")
    form = defaults_form(client.get("/eaters").text)
    assert re.search(r'name="goal_man"[^>]*value="2600"', form, re.S)
    assert re.search(r'name="goal_teen"[^>]*value="2200"', form, re.S)


def test_a_blank_default_goal_goes_back_to_the_built_in_one(client):
    save_defaults(client, goal_woman="2100")
    save_defaults(client, goal_woman="")
    assert re.search(
        r'name="goal_woman"[^>]*value="2000"', defaults_form(client.get("/eaters").text), re.S
    )


@pytest.mark.parametrize("bad", ["lots", "100", "9000", "-2000", "2000.5"])
def test_a_bad_default_goal_is_rejected_and_nothing_is_saved(client, bad):
    response = save_defaults(client, goal_man="2600", goal_woman=bad)
    assert response.status_code == 400
    assert 'class="error"' in response.text
    assert re.search(
        r'name="goal_man"[^>]*value="2500"', defaults_form(client.get("/eaters").text), re.S
    )


def test_a_rejected_default_form_keeps_what_was_typed(client):
    response = save_defaults(client, goal_man="2600", goal_woman="lots")
    form = defaults_form(response.text)
    assert re.search(r'name="goal_man"[^>]*value="2600"', form, re.S)
    assert re.search(r'name="goal_woman"[^>]*value="lots"', form, re.S)


def test_every_page_links_to_the_others(client):
    for path in ("/", "/eaters", "/settings"):
        page = client.get(path).text
        assert 'href="/eaters"' in page
        assert 'href="/settings"' in page


def test_the_json_export_includes_the_eaters_and_the_defaults(client):
    save_defaults(client, goal_teen="2300")
    add(client, "Sam", "teen", "100", "peanuts", "olives")
    add(client, "Mia", "child")
    data = client.get("/export.json").json()
    assert data["default_calorie_goals"] == {
        "man": 2500,
        "woman": 2000,
        "teen": 2300,
        "child": 1600,
    }
    assert data["eaters"] == [
        {
            "name": "Sam",
            "category": "teen",
            "calorie_goal": 2400,
            "calorie_adjustment": 100,
            "calorie_goal_is_custom": True,
            "allergies": ["peanuts"],
            "dislikes": ["olives"],
            "diet": "omnivore",
        },
        {
            "name": "Mia",
            "category": "child",
            "calorie_goal": 1600,
            "calorie_adjustment": 0,
            "calorie_goal_is_custom": False,
            "allergies": [],
            "dislikes": [],
            "diet": "omnivore",
        },
    ]


def test_the_page_escapes_what_was_typed(client):
    add(client, "<script>alert(1)</script>", "man", "0", "<b>nuts</b>")
    page = client.get("/eaters").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert re.search(r"&lt;b&gt;nuts&lt;/b&gt;|&lt;b&gt;nuts", page)


# --- diets ----------------------------------------------------------------------------------


def diet_options(form_html):
    select = form_html.split('name="diet"')[1].split("</select>")[0]
    return re.findall(r'<option value="([^"]+)"( selected)?>([^<]*)</option>', select)


def test_the_add_form_offers_every_diet_with_eating_everything_selected(client):
    form = client.get("/eaters").text.split('action="/eaters"')[1].split("</form>")[0]
    options = diet_options(form)
    assert [value for value, _, _ in options] == [
        "omnivore",
        "low-meat",
        "pescatarian",
        "vegetarian",
        "vegan",
    ]
    assert [(value, label) for value, selected, label in options if selected] == [
        ("omnivore", "Eats everything")
    ]
    assert "Pescatarian" in [label for _, _, label in options]


def test_a_diet_is_saved_and_shown_on_the_card(client, db_path):
    assert add(client, "Mia", "woman", diet="pescatarian").status_code == 303
    assert stored(db_path)[0].diet.value == "pescatarian"
    assert "Diet: Pescatarian" in card(client, "Mia")


def test_eating_everything_adds_nothing_to_the_card(client):
    add(client, "Sam")
    assert "Diet:" not in card(client, "Sam")


def test_an_unknown_diet_is_rejected_and_nothing_is_stored(client, db_path):
    response = add(client, "Mia", diet="carnivore")
    assert response.status_code == 400
    assert 'class="error"' in response.text
    assert "diet" in response.text.lower()
    assert stored(db_path) == []


def test_the_edit_form_preselects_the_diet_and_can_change_it(client, db_path):
    add(client, "Mia", "woman", diet="vegetarian")
    edit = card(client, "Mia").split("<details")[1]
    assert [value for value, selected, _ in diet_options(edit) if selected] == ["vegetarian"]
    [eater] = stored(db_path)
    response = client.post(
        f"/eaters/{eater.id}",
        data={
            "name": "Mia",
            "category": "woman",
            "calorie_adjustment": "0",
            "allergies": "",
            "dislikes": "",
            "diet": "vegan",
        },
    )
    assert response.status_code == 303
    assert "Diet: Vegan" in card(client, "Mia")


def test_the_diet_is_kept_in_the_form_after_a_rejected_add(client):
    response = add(client, "", "woman", diet="vegan")
    assert response.status_code == 400
    form = response.text.split('action="/eaters"')[1].split("</form>")[0]
    assert [value for value, selected, _ in diet_options(form) if selected] == ["vegan"]


def test_the_json_export_includes_each_persons_diet(client):
    add(client, "Mia", "woman", diet="low-meat")
    add(client, "Sam")
    diets = {e["name"]: e["diet"] for e in client.get("/export.json").json()["eaters"]}
    assert diets == {"Mia": "low-meat", "Sam": "omnivore"}


def test_the_page_explains_what_each_diet_means(client):
    page = client.get("/eaters").text
    for text in (
        "no meat or fish",
        "fish, but no meat",
        "no animal products",
        "meat only now and then",
    ):
        assert text in page
