"""The look on the pages: the season and festive colours a page is dressed in, and the Settings
choice that pins a season or turns festive colours off."""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.repository import StockRepository
from emu_the_menu.web import create_app


def off(code):
    return {"status": 0}


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


def client_on(db_path, day):
    app = create_app(db_path, today=lambda: day, fetch_product=off)
    return TestClient(app, follow_redirects=False)


def html_tag(page):
    return re.search(r"<html[^>]*>", page).group(0)


def test_pages_carry_the_season_of_the_day(db_path):
    tag = html_tag(client_on(db_path, date(2026, 7, 4)).get("/").text)
    assert 'data-season="summer"' in tag and 'data-festive=""' in tag


def test_pages_carry_the_festive_overlay_in_its_window(db_path):
    tag = html_tag(client_on(db_path, date(2026, 10, 28)).get("/recipes").text)
    assert 'data-season="autumn"' in tag and 'data-festive="halloween"' in tag


def test_a_southern_household_gets_the_opposite_season(db_path):
    with StockRepository(db_path) as repo:
        repo.set_household_country("AU")
    tag = html_tag(client_on(db_path, date(2026, 7, 4)).get("/").text)
    assert 'data-season="winter"' in tag


def test_settings_offers_the_look_choice(db_path):
    page = client_on(db_path, date(2026, 7, 4)).get("/settings").text
    assert re.search(r'<select name="look_season">.*?value="auto" selected', page, re.S)
    assert re.search(r'name="look_festive"[^>]*value="on"[^>]*checked', page)


def test_pinning_a_season_dresses_every_page_in_it(db_path):
    client = client_on(db_path, date(2026, 7, 4))
    response = client.post("/settings", data={"look_season": "winter", "look_festive": "on"})
    assert response.status_code == 303
    assert 'data-season="winter"' in html_tag(client.get("/menu").text)
    with StockRepository(db_path) as repo:
        assert (repo.look_choice().season, repo.look_choice().festive) == ("winter", True)
    page = client.get("/settings").text
    assert re.search(r'value="winter" selected', page)


def test_festive_colours_can_be_turned_off(db_path):
    client = client_on(db_path, date(2026, 12, 20))
    assert 'data-festive="christmas"' in html_tag(client.get("/").text)
    client.post("/settings", data={"look_season": "auto", "look_festive": "off"})
    tag = html_tag(client.get("/").text)
    assert 'data-festive=""' in tag and 'data-season="winter"' in tag


def test_a_bad_look_choice_is_refused_and_nothing_else_is_saved(db_path):
    client = client_on(db_path, date(2026, 7, 4))
    response = client.post(
        "/settings", data={"look_season": "monsoon", "look_festive": "on", "country": "FR"}
    )
    assert response.status_code == 400 and "Choose a season" in response.text
    with StockRepository(db_path) as repo:
        assert repo.household_country() is None and repo.look_choice().season == "auto"


def test_the_settings_panel_in_the_overlay_has_the_choice_too(db_path):
    page = client_on(db_path, date(2026, 7, 4)).get("/settings/panel").text
    assert 'name="look_season"' in page and 'name="look_festive"' in page
