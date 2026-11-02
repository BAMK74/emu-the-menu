import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.disclaimer import DISCLAIMER_VERSION
from emu_the_menu.repository import StockRepository
from emu_the_menu.web import create_app

TODAY = date(2026, 10, 26)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


@pytest.fixture
def client(db_path):
    app = create_app(db_path, today=lambda: TODAY, fetch_product=lambda code: {"status": 0})
    return TestClient(app, follow_redirects=False)


def plain(html):
    """The text of a page, with tags removed and spaces collapsed."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def accept(client, **data):
    return client.post("/disclaimer/accept", data={"understood": "on", **data})


# --- storing the acceptance -----------------------------------------------------------------


def test_nothing_is_accepted_at_first(db_path):
    with StockRepository(db_path) as repo:
        assert repo.disclaimer_acceptance() is None


def test_the_version_and_date_accepted_are_recorded(db_path):
    with StockRepository(db_path) as repo:
        repo.accept_disclaimer("2026-10-27", TODAY)
        assert repo.disclaimer_acceptance() == ("2026-10-27", TODAY)


def test_accepting_again_replaces_the_record(db_path):
    with StockRepository(db_path) as repo:
        repo.accept_disclaimer("2026-10-27", date(2026, 10, 1))
        repo.accept_disclaimer("2027-01-01", date(2027, 1, 2))
        assert repo.disclaimer_acceptance() == ("2027-01-01", date(2027, 1, 2))


def test_the_acceptance_survives_reopening_the_database(db_path):
    with StockRepository(db_path) as repo:
        repo.accept_disclaimer("2026-10-27", TODAY)
    with StockRepository(db_path) as repo:
        assert repo.disclaimer_acceptance() == ("2026-10-27", TODAY)


# --- the full text --------------------------------------------------------------------------


def test_the_disclaimer_page_says_what_emu_is_not(client):
    response = client.get("/disclaimer")
    assert response.status_code == 200
    text = plain(response.text).lower()
    for phrase in (
        "it is not a dietitian",
        "doctor",
        "nutritionist",
        "pharmacist",
        "allergy specialist",
        "is not medical, dietary or nutritional advice",
        "does not replace",
        "no professional relationship",
    ):
        assert phrase in text, phrase


def test_the_disclaimer_is_unmistakable_about_allergies(client):
    text = plain(client.get("/disclaimer").text).lower()
    for phrase in (
        "cannot guarantee that any food, recipe or menu is free of an allergen",
        "hidden ingredients",
        "cross-contamination",
        "always read the label of every product",
        "never rely on emu the menu to keep someone safe",
        "112",
        "emergency",
        "emergency medicine",
    ):
        assert phrase in text, phrase


def test_the_disclaimer_covers_calories_diets_food_safety_and_other_sources(client):
    text = plain(client.get("/disclaimer").text).lower()
    for phrase in (
        "not recommendations for any individual",
        "estimates",
        "not a promise that every suggestion fits that diet",
        "not a food safety check",
        "when in doubt, throw it out",
        "open food facts",
        "anyone can edit",
        "you stay responsible",
        "you decide what to buy, cook and serve",
    ):
        assert phrase in text, phrase


def test_the_disclaimer_limits_liability_and_points_to_the_licence(client):
    text = plain(client.get("/disclaimer").text).lower()
    for phrase in (
        "provided as is, without any warranty",
        "to the fullest extent the law allows",
        "not liable",
        "nothing here limits any right or liability that cannot be limited by law",
        "license.md",
    ):
        assert phrase in text, phrase


def test_the_disclaimer_shows_its_version(client):
    assert DISCLAIMER_VERSION in plain(client.get("/disclaimer").text)


# --- the footer and the banner ---------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/eaters", "/settings", "/disclaimer"])
def test_every_page_ends_with_the_short_notice_and_a_link(client, path):
    page = client.get(path).text
    assert 'class="legal"' in page
    assert "not a dietitian, a doctor or an allergy service" in plain(page)
    assert 'href="/disclaimer"' in page


@pytest.mark.parametrize("path", ["/", "/eaters", "/settings"])
def test_a_banner_asks_for_acceptance_on_every_page_until_it_is_given(client, path):
    page = client.get(path).text
    assert 'class="disclaimer-banner"' in page
    assert "cannot guarantee" in plain(page)


def test_the_disclaimer_page_has_the_accept_form_instead_of_the_banner(client):
    page = client.get("/disclaimer").text
    assert 'class="disclaimer-banner"' not in page
    assert 'action="/disclaimer/accept"' in page
    assert re.search(r'<input[^>]*type="checkbox"[^>]*name="understood"', page)


def test_accepting_needs_the_box_ticked(client, db_path):
    response = client.post("/disclaimer/accept", data={})
    assert response.status_code == 400
    assert 'class="error"' in response.text
    assert "tick" in response.text.lower()
    with StockRepository(db_path) as repo:
        assert repo.disclaimer_acceptance() is None


def test_accepting_records_the_version_and_date_and_removes_the_banner(client, db_path):
    response = accept(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/disclaimer?accepted=1"
    with StockRepository(db_path) as repo:
        assert repo.disclaimer_acceptance() == (DISCLAIMER_VERSION, TODAY)
    for path in ("/", "/eaters", "/settings"):
        page = client.get(path).text
        assert 'class="disclaimer-banner"' not in page
        assert 'class="legal"' in page  # the footer stays


def test_the_page_shows_when_it_was_accepted(client):
    accept(client)
    text = plain(client.get("/disclaimer?accepted=1").text)
    assert f"You accepted version {DISCLAIMER_VERSION} on 2026-10-26" in text


def test_a_changed_disclaimer_asks_again(client, db_path):
    with StockRepository(db_path) as repo:
        repo.accept_disclaimer("2000-01-01", date(2000, 1, 2))
    page = client.get("/").text
    assert 'class="disclaimer-banner"' in page
    assert "changed" in plain(client.get("/disclaimer").text).lower()


def test_accepting_can_return_you_to_the_page_you_were_on(client):
    assert accept(client, next="/eaters").headers["location"] == "/eaters"


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example", "/items", ""])
def test_accepting_never_redirects_anywhere_unknown(client, target):
    assert accept(client, next=target).headers["location"] == "/disclaimer?accepted=1"


# --- warnings where the risk is -----------------------------------------------------------------


def test_the_eaters_page_warns_about_allergies_diets_and_calories(client):
    text = plain(client.get("/eaters").text).lower()
    for phrase in (
        "cannot guarantee that a food is free of an allergen",
        "always read the label",
        "not medical or dietary advice",
        "talk to a doctor or dietitian",
    ):
        assert phrase in text, phrase


def test_the_add_form_says_expiry_dates_are_not_a_food_safety_check(client):
    assert "not a food safety check" in plain(client.get("/").text)


def test_the_eaters_warning_sits_between_the_add_form_and_the_list(client):
    add_eater = lambda name: client.post(  # noqa: E731
        "/eaters",
        data={"name": name, "category": "man", "calorie_adjustment": "0", "diet": "omnivore"},
    )
    add_eater("Sam")
    page = client.get("/eaters").text
    warning = page.index("Not medical advice")
    assert page.index('action="/eaters"') < warning  # after the add form
    assert warning < page.index("<ul")  # before the list of people
    assert page.count("Not medical advice") == 1


def test_the_eaters_warning_still_comes_before_the_empty_message(client):
    page = client.get("/eaters").text
    assert page.index('action="/eaters"') < page.index("Not medical advice")
    assert page.index("Not medical advice") < page.index("No one yet")


# --- the disclaimer as a pop-up ----------------------------------------------------------------


def test_the_popup_fragment_has_the_full_text_and_no_page_around_it(client):
    page = client.get("/disclaimer/panel")
    assert page.status_code == 200
    assert "<html" not in page.text and "<header" not in page.text
    assert "It is not a dietitian, a doctor or an allergy service" in page.text
    assert "Always read the label" in page.text or "always read the label" in page.text.lower()


def test_the_popup_offers_the_accept_form_that_stays_in_the_popup(client):
    page = client.get("/disclaimer/panel").text
    assert 'action="/disclaimer/accept"' in page
    assert 'name="popup" value="1"' in page
    assert "data-disclaimer-form" in page
    assert "data-close-dialog" in page


def test_accepting_in_the_popup_answers_with_the_panel_not_a_redirect(client):
    response = client.post("/disclaimer/accept", data={"understood": "on", "popup": "1"})
    assert response.status_code == 200
    assert "You accepted version" in response.text
    assert "<html" not in response.text
    assert 'action="/disclaimer/accept"' not in response.text


def test_accepting_in_the_popup_is_recorded(client):
    client.post("/disclaimer/accept", data={"understood": "on", "popup": "1"})
    assert 'class="disclaimer-banner"' not in client.get("/recipes").text


def test_the_popup_asks_for_the_tick_and_says_so(client):
    response = client.post("/disclaimer/accept", data={"popup": "1"})
    assert response.status_code == 400
    assert "Tick the box" in response.text
    assert "<html" not in response.text
    assert 'class="disclaimer-banner"' in client.get("/recipes").text


def test_accepting_without_the_popup_still_redirects(client):
    response = client.post("/disclaimer/accept", data={"understood": "on", "next": "/eaters"})
    assert response.status_code == 303 and response.headers["location"] == "/eaters"


def test_every_page_carries_the_dialog_the_links_open(client):
    for path in ("/", "/recipes", "/menu", "/ingredients", "/eaters"):
        assert 'id="disclaimer-dialog"' in client.get(path).text, path


def test_the_disclaimer_page_itself_is_unchanged(client):
    page = client.get("/disclaimer").text
    assert "<h1>Disclaimer</h1>" in page
    assert "It is not a dietitian, a doctor or an allergy service" in page
