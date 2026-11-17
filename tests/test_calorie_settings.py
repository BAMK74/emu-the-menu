import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.eaters import Category, EaterRepository
from emu_the_menu.web import create_app


@pytest.fixture
def client(db_url):
    app = create_app(
        db_url, today=lambda: date(2026, 10, 26), fetch_product=lambda code: {"status": 0}
    )
    return TestClient(app, follow_redirects=False)


def goals_section(html):
    return html.split('<section class="calorie-goals"')[1].split("</section>")[0]


def settings_page(client):
    return goals_section(client.get("/settings").text)


def settings_panel(client, next_page="/eaters"):
    return goals_section(client.get("/settings/panel", params={"next": next_page}).text)


def text_of(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def save(client, **goals):
    data = {"goal_man": "", "goal_woman": "", "goal_teen": "", "goal_child": ""}
    return client.post("/settings/calorie-goals", data={**data, **goals})


def current(html, kind):
    match = re.search(rf'name="goal_{kind}"[^>]*value="([^"]*)"', html, re.S)
    return match.group(1) if match else None


def add_eater(client, name, category="woman", adjustment="0"):
    client.post(
        "/eaters",
        data={"name": name, "category": category, "calorie_adjustment": adjustment},
    )


# --- where it lives -------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["page", "panel"])
def test_the_settings_page_and_popup_both_have_the_calorie_goals(client, where):
    section = settings_page(client) if where == "page" else settings_panel(client)
    assert "Calorie goals by type" in text_of(section)


def test_the_description_and_the_editable_goals_are_grouped_in_one_section(client):
    section = settings_page(client)
    assert "<table" in section  # the description by type
    assert 'action="/settings/calorie-goals"' in section  # the boxes to change them
    assert section.count('class="warning-note"') == 1  # and the disclaimer
    assert section.index("<table") < section.index('action="/settings/calorie-goals"')


def test_the_eaters_page_no_longer_carries_the_goals(client):
    page = client.get("/eaters").text
    assert 'class="calorie-goals"' not in page
    assert "goal-guide" not in page.replace("</style>", "").split("<body>")[1]
    assert "/settings/calorie-goals" not in page
    assert 'name="goal_man"' not in page
    assert "Calorie goals by type" not in page


def test_the_eaters_page_says_where_to_change_the_defaults(client):
    assert "Change the defaults in Settings" in text_of(client.get("/eaters").text)


def test_the_goals_come_between_the_country_and_the_backup(client):
    page = client.get("/settings").text
    assert (
        page.index('name="country"')
        < page.index('class="calorie-goals"')
        < page.index('class="backup"')
    )


# --- the description ------------------------------------------------------------------------


def test_the_section_describes_the_goal_of_each_type(client):
    text = text_of(settings_page(client))
    for kind, goal, who in (
        ("Man", 2500, "adult men"),
        ("Woman", 2000, "adult women"),
        ("Teen", 2200, "teenagers"),
        ("Child", 1600, "children"),
    ):
        row = re.search(rf"{kind} ([^|]*?) {goal}(?: |$)", text)
        assert row, f"{kind} {goal}"
        assert who in row.group(1).lower(), kind


def test_the_description_shows_the_current_default_and_says_when_it_was_changed(client):
    save(client, goal_woman="2100")
    text = text_of(settings_page(client))
    assert re.search(r"Woman .*? 2100 \(built-in 2000\)", text)
    assert "(built-in 2500)" not in text  # unchanged ones carry no note


def test_the_disclaimer_says_what_the_figures_are(client):
    section = settings_page(client)
    text = text_of(section).lower()
    for phrase in (
        "rough starting points",
        "not recommendations for any individual",
        "age, size, activity, health",
        "have not been checked by a dietitian or doctor",
        "talk to a doctor or dietitian",
        "children, teenagers, pregnancy or a medical condition",
    ):
        assert phrase in text, phrase
    assert 'href="/disclaimer"' in section
    assert "Not medical advice" not in section


# --- changing the defaults --------------------------------------------------------------------


def test_the_boxes_are_labelled_with_capitalised_type_names(client):
    text = text_of(settings_page(client))
    for kind in ("Man", "Woman", "Teen", "Child"):
        assert f"{kind} (kcal a day)" in text


def test_the_boxes_start_with_the_current_default_of_each_type(client):
    section = settings_page(client)
    assert [current(section, k) for k in ("man", "woman", "teen", "child")] == [
        "2500",
        "2000",
        "2200",
        "1600",
    ]
    for kind, builtin in (("man", 2500), ("woman", 2000), ("teen", 2200), ("child", 1600)):
        assert re.search(rf'name="goal_{kind}"[^>]*placeholder="{builtin}"', section, re.S)


def test_saving_returns_to_settings_with_a_confirmation(client):
    response = save(client, goal_woman="2100")
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?saved=1"
    assert '<p class="saved" role="status">Saved.</p>' in client.get("/settings?saved=1").text


def test_saving_from_the_popup_returns_to_the_page_you_were_on(client):
    assert save(client, goal_woman="2100", next="/eaters").headers["location"] == "/eaters?saved=1"
    assert save(client, goal_woman="2100", next="/").headers["location"] == "/?saved=1"


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example", "/items", ""])
def test_saving_never_redirects_anywhere_unknown(client, target):
    assert save(client, goal_woman="2100", next=target).headers["location"] == "/settings?saved=1"


def test_the_popup_remembers_which_page_to_return_to(client):
    assert '<input type="hidden" name="next" value="/eaters">' in settings_panel(client, "/eaters")
    assert 'name="next"' not in settings_panel(client, "//evil.example")


def test_a_saved_default_is_shown_again_and_moves_everyone_of_that_type(client):
    add_eater(client, "Ann", "woman", "0")
    add_eater(client, "Beth", "woman", "200")
    add_eater(client, "Carl", "man", "0")
    save(client, goal_woman="2100", goal_man="2600")
    section = settings_page(client)
    assert current(section, "woman") == "2100" and current(section, "man") == "2600"
    assert current(section, "teen") == "2200"
    page = client.get("/eaters").text
    assert "2100 kcal a day (default)" in page
    assert "2300 kcal a day (default 2100, +200)" in page
    assert "2600 kcal a day (default)" in page


def test_a_blank_box_goes_back_to_the_built_in_goal(client):
    save(client, goal_woman="2100")
    save(client, goal_woman="")
    assert current(settings_page(client), "woman") == "2000"


@pytest.mark.parametrize("bad", ["lots", "100", "9000", "-2000", "2000.5"])
def test_a_bad_goal_is_rejected_with_a_message_and_nothing_is_saved(client, bad):
    response = save(client, goal_man="2600", goal_woman=bad)
    assert response.status_code == 400
    assert 'class="error"' in response.text
    assert current(settings_page(client), "man") == "2500"  # the good one was not kept either


def test_a_rejected_save_keeps_what_was_typed(client):
    response = save(client, goal_man="2600", goal_woman="lots")
    section = goals_section(response.text)
    assert current(section, "man") == "2600"
    assert current(section, "woman") == "lots"


def test_the_goals_are_stored_with_the_eaters(client, db_url):
    save(client, goal_teen="2300")
    with EaterRepository(db_url) as repo:
        assert repo.default_goals()[Category.TEEN] == 2300


# --- it is a menu that can be expanded or not -----------------------------------------------------


@pytest.mark.parametrize("where", ["page", "panel"])
def test_the_calorie_goals_are_a_menu_that_starts_closed(client, where):
    section = settings_page(client) if where == "page" else settings_panel(client)
    assert re.search(r"<details>\s*<summary>Calorie goals by type</summary>", section)
    assert not re.search(r"<details[^>]*\bopen\b", section)


def test_everything_in_the_section_sits_inside_the_menu(client):
    section = settings_page(client)
    inside = section.split("<details>")[1].split("</details>")[0]
    for part in ("<table", 'action="/settings/calorie-goals"', 'class="warning-note"'):
        assert part in inside, part


def test_a_rejected_save_opens_the_menu_so_the_error_and_values_are_in_view(client):
    response = save(client, goal_man="2600", goal_woman="lots")
    section = goals_section(response.text)
    assert re.search(r"<details[^>]*\bopen\b", section)


def test_a_successful_save_leaves_the_menu_closed_next_time(client):
    save(client, goal_woman="2100")
    assert not re.search(r"<details[^>]*\bopen\b", settings_page(client))
