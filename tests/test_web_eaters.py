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
    allergens=(),
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
            "allergens": list(allergens),
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
    assert "Teen" in entry
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


# --- default goals per category (changed in Settings) -----------------------------------------


def save_defaults(client, **goals):
    data = {"goal_man": "", "goal_woman": "", "goal_teen": "", "goal_child": ""}
    return client.post("/settings/calorie-goals", data={**data, **goals})


def test_every_page_links_to_settings_and_its_pages_to_eaters(client):
    for path in ("/", "/eaters", "/settings"):
        assert 'href="/settings"' in client.get(path).text
    for path in ("/eaters", "/settings"):  # Eaters is a tab of the Settings section
        assert 'href="/eaters"' in client.get(path).text


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
            "allergens": [],
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
            "allergens": [],
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


# --- allergens ------------------------------------------------------------------------------


def allergen_boxes(form_html):
    """(value, checked) for each allergen checkbox in a form."""
    return [
        (value, bool(checked))
        for value, checked in re.findall(
            r'<input type="checkbox" name="allergens" value="([^"]+)"( checked)?>', form_html
        )
    ]


def test_the_add_form_has_a_checkbox_for_every_eu_allergen_all_unticked(client):
    form = client.get("/eaters").text.split('action="/eaters"')[1].split("</form>")[0]
    boxes = allergen_boxes(form)
    assert [value for value, _ in boxes] == [
        "celery", "crustaceans", "eggs", "fish", "gluten", "lupin", "milk",
        "molluscs", "mustard", "peanuts", "sesame", "soy", "sulphites", "tree-nuts",
    ]  # fmt: skip
    assert not any(checked for _, checked in boxes)
    assert "Other allergies" in form


def test_ticked_allergens_are_saved_and_shown_on_the_card(client, db_path):
    assert add(client, "Mia", "woman", allergens=["milk", "tree-nuts"]).status_code == 303
    assert [a.value for a in stored(db_path)[0].allergens] == ["milk", "tree-nuts"]
    entry = card(client, "Mia")
    assert "Allergies:" in entry
    assert "Milk" in entry and "Tree nuts" in entry


def test_allergens_and_other_allergies_are_listed_together(client):
    add(client, "Mia", "woman", allergies="kiwi", allergens=["peanuts"])
    meta = re.search(r"Allergies: ([^<]*)<", card(client, "Mia")).group(1)
    assert meta == "Peanuts, kiwi"


def test_the_edit_form_ticks_the_persons_allergens(client):
    add(client, "Mia", "woman", allergens=["eggs", "soy"])
    edit = card(client, "Mia").split("<details")[1]
    assert [v for v, checked in allergen_boxes(edit) if checked] == ["eggs", "soy"]


def test_updating_changes_the_allergens(client, db_path):
    add(client, "Mia", "woman", allergens=["eggs"])
    [eater] = stored(db_path)
    response = client.post(
        f"/eaters/{eater.id}",
        data={
            "name": "Mia",
            "category": "woman",
            "calorie_adjustment": "0",
            "allergies": "",
            "dislikes": "",
            "diet": "omnivore",
            "allergens": ["fish", "sesame"],
        },
    )
    assert response.status_code == 303
    assert [a.value for a in stored(db_path)[0].allergens] == ["fish", "sesame"]


def test_an_unknown_allergen_is_rejected_and_nothing_is_stored(client, db_path):
    response = add(client, "Mia", "woman", allergens=["milk", "kiwi"])
    assert response.status_code == 400
    assert 'class="error"' in response.text
    assert "allergen" in response.text.lower()
    assert stored(db_path) == []


def test_ticks_are_kept_in_the_form_after_a_rejected_add(client):
    response = add(client, "", "woman", allergens=["gluten", "soy"])
    assert response.status_code == 400
    form = response.text.split('action="/eaters"')[1].split("</form>")[0]
    assert [v for v, checked in allergen_boxes(form) if checked] == ["gluten", "soy"]


def test_the_json_export_lists_allergens_separately(client):
    add(client, "Mia", "woman", allergies="kiwi", allergens=["milk"])
    [mia] = client.get("/export.json").json()["eaters"]
    assert mia["allergens"] == ["milk"]
    assert mia["allergies"] == ["kiwi"]


def test_category_names_are_capitalised_wherever_they_are_shown(client):
    add(client, "Sam", "teen")
    page = client.get("/eaters").text
    for name in ("Man", "Woman", "Teen", "Child"):
        assert f">{name}</option>" in page  # both the Add and the Edit drop-downs
    assert 'class="tag">Teen</span>' in page
    for lowercase in ("man", "woman", "teen", "child"):
        assert f">{lowercase}</option>" not in page
    # What forms send, and what the export holds, stays lowercase.
    assert '<option value="teen"' in page
    assert client.get("/export.json").json()["eaters"][0]["category"] == "teen"


def test_the_list_fields_say_which_separators_to_use(client):
    add(client, "Sam")
    page = client.get("/eaters").text
    add_form = page.split('action="/eaters"')[1].split("</form>")[0]
    edit_form = card(client, "Sam").split("<details")[1]
    for form in (add_form, edit_form):
        # one note under "Other allergies" and one under "Dislikes"
        assert form.count("Separate with commas or semicolons") == 2
    assert "Separate with commas or semicolons, like olives, mushrooms" in add_form
    assert "Separate with commas or semicolons, like kiwi, buckwheat" in add_form


def test_the_separators_the_note_promises_really_work(client, db_path):
    add(client, "Sam", dislikes="olives; mushrooms, capers", allergies="kiwi;buckwheat, figs")
    [sam] = stored(db_path)
    assert sam.dislikes == ("olives", "mushrooms", "capers")
    assert sam.allergies == ("kiwi", "buckwheat", "figs")


def test_allergens_show_short_names_with_the_examples_as_a_tooltip_and_a_note(client):
    add(client, "Sam")
    page = client.get("/eaters").text
    for form in (
        page.split('action="/eaters"')[1].split("</form>")[0],
        card(client, "Sam").split("<details")[1],
    ):
        boxes = form.split('<fieldset class="allergens">')[1].split("</fieldset>")[0]
        names = re.findall(r"</?input[^>]*>\s*([A-Za-z ]+)</label>", boxes)
        assert names == [
            "Celery", "Crustaceans", "Eggs", "Fish", "Gluten", "Lupin", "Milk",
            "Molluscs", "Mustard", "Peanuts", "Sesame", "Soy", "Sulphites", "Tree nuts",
        ]  # fmt: skip
        # The full wording is still there, as a tooltip on each pill.
        assert 'title="Gluten (wheat, rye, barley, oats)"' in boxes
        assert 'title="Crustaceans (prawns, crab, lobster)"' in boxes
        # And spelled out once in a note, so touch screens can read it too.
        assert "Gluten: wheat, rye, barley, oats" in boxes
        assert "Tree nuts: almonds, hazelnuts, walnuts" in boxes
