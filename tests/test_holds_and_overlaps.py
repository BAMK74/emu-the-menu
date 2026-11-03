"""Telling the person when accepted Menus hold Stock or cover the same days."""

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.eaters import Category, EaterRepository
from emu_the_menu.menu_repository import MenuRepository, SavedMenu
from emu_the_menu.planner import Meal, Menu, Portion, Slot
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import StockRepository
from emu_the_menu.reserve import holdings, overlapping
from emu_the_menu.stock import Location
from emu_the_menu.web import create_app

TODAY = date(2026, 11, 2)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


@pytest.fixture
def client(db_path):
    app = create_app(db_path, today=lambda: TODAY, fetch_product=lambda code: {"status": 0})
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def world(db_path):
    """One eater, a recipe of 200 g carrots for 2 servings, and 1 kg of carrots in stock."""
    with EaterRepository(db_path) as eaters:
        sam = eaters.add("Sam", Category.MAN)
    with RecipeRepository(db_path) as recipes:
        recipe = recipes.get(
            recipes.add(
                "Carrot soup", 2, (MealType.DINNER,), Weather.ANY, (), [("carrots", 200, "g")]
            )
        )
    with StockRepository(db_path) as stock:
        stock.add("carrots", 1000, "g", Location.PANTRY)
        carrots = stock.ingredients()[0]
        stock.set_ingredient_details(carrots.id, 40, (), ())
    return sam, recipe


def save(db_path, world, name, start=TODAY, days=1, servings=2.0):
    sam, recipe = world
    meals = tuple(
        Meal(start + timedelta(days=d), Slot.DINNER, recipe, (Portion(sam, servings, None),))
        for d in range(days)
    )
    with MenuRepository(db_path) as menus:
        return menus.save(name, start, days, Menu(meals, ()))


def accept(db_path, menu_id):
    with MenuRepository(db_path) as menus:
        menus.accept(menu_id, TODAY)


def text_of(html):
    html = re.sub(r"<(style|script)\b.*?</\1>", "", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


QUERY = "/menu?go=1&start=2026-11-02&days=1&meal_types=dinner&eater=1&cooldown=0"


# ---- stock held ----


def test_nothing_is_said_when_nothing_is_held(client, world):
    assert "Stock is being held" not in client.get(QUERY).text


def test_the_menu_page_says_what_accepted_menus_hold(client, db_path, world):
    accept(db_path, save(db_path, world, "Week one", days=2))  # 2 meals x 200 g
    text = text_of(client.get(QUERY).text)
    assert "Stock is being held" in text and "An accepted menu is holding stock" in text
    assert "Week one" in text and "carrots 400 g" in text


def test_a_menu_that_is_only_saved_holds_nothing(client, db_path, world):
    save(db_path, world, "Draft")
    assert "Stock is being held" not in client.get(QUERY).text


def test_un_accepting_lets_go_of_the_note(client, db_path, world):
    menu_id = save(db_path, world, "Week one")
    accept(db_path, menu_id)
    client.post(f"/menus/{menu_id}/unaccept")
    assert "Stock is being held" not in client.get(QUERY).text


def test_several_menus_are_listed_in_turn_with_their_own_amounts(client, db_path, world):
    accept(db_path, save(db_path, world, "First", days=1))
    accept(db_path, save(db_path, world, "Second", start=TODAY + timedelta(days=5), days=2))
    text = text_of(client.get(QUERY).text)
    assert "Accepted menus are holding stock" in text
    assert "First (Mon 2 Nov, 1 day): carrots 200 g" in text
    assert "Second (Sat 7 Nov, 2 days): carrots 400 g" in text


def test_a_saved_menu_page_names_the_stock_held_by_menus_before_it(client, db_path, world):
    first = save(db_path, world, "First")
    second = save(db_path, world, "Second")
    accept(db_path, first)
    accept(db_path, second)
    assert "Stock is being held" not in client.get(f"/menus/{first}").text
    text = text_of(client.get(f"/menus/{second}").text)
    assert (
        "so this menu does not count on it" in text and "First" in text and "carrots 200 g" in text
    )


def test_a_cooked_meal_no_longer_holds_stock(db_path, world):
    menu_id = save(db_path, world, "Week one")
    accept(db_path, menu_id)
    with MenuRepository(db_path) as menus:
        meal = menus.get(menu_id).meals[0]
        menus.set_cooked(menu_id, meal.id, TODAY)
    assert holdings(db_path, TODAY) == []


def test_holdings_name_each_ingredient_once_per_menu(db_path, world):
    accept(db_path, save(db_path, world, "Week one", days=3))
    [(menu, items)] = holdings(db_path, TODAY)
    assert menu.name == "Week one" and [(i.name, q) for i, q in items] == [("carrots", 600)]


# ---- overlapping menus ----


def menu(menu_id, start, days):
    return SavedMenu(menu_id, f"M{menu_id}", start, days, ())


def test_overlapping_finds_menus_that_share_a_day():
    a = menu(1, date(2026, 11, 2), 7)  # 2 to 8 Nov
    touching = menu(2, date(2026, 11, 8), 3)
    after = menu(3, date(2026, 11, 9), 3)
    inside = menu(4, date(2026, 11, 4), 1)
    before = menu(5, date(2026, 10, 30), 3)  # 30 Oct to 1 Nov
    assert overlapping(a, [a, touching, after, inside, before], TODAY) == [touching, inside]


def test_overlapping_ignores_menus_that_are_over():
    old_a = menu(1, date(2026, 10, 20), 5)
    old_b = menu(2, date(2026, 10, 22), 5)
    assert overlapping(old_a, [old_a, old_b], TODAY) == []
    live = menu(3, date(2026, 10, 30), 5)  # ends 3 Nov
    assert overlapping(live, [old_b, live], TODAY) == []  # old_b ended 26 Oct
    assert overlapping(live, [live, menu(4, date(2026, 11, 3), 2)], TODAY) != []


def test_the_saved_page_warns_when_two_accepted_menus_overlap(client, db_path, world):
    first = save(db_path, world, "Week one", days=3)
    second = save(db_path, world, "Midweek", start=TODAY + timedelta(days=1), days=2)
    accept(db_path, first)
    assert "Overlapping menus" not in client.get(f"/menus/{first}").text
    accept(db_path, second)
    for this, other in ((first, "Midweek"), (second, "Week one")):
        text = text_of(client.get(f"/menus/{this}").text)
        assert "Overlapping menus" in text and other in text and "Un-accept one" in text


def test_a_menu_that_is_not_accepted_gets_no_overlap_warning(client, db_path, world):
    first = save(db_path, world, "Week one", days=3)
    second = save(db_path, world, "Draft", days=3)
    accept(db_path, first)
    assert "Overlapping menus" not in client.get(f"/menus/{second}").text


def test_the_list_flags_overlaps_and_accepting_through_the_page_shows_them(client, db_path, world):
    first = save(db_path, world, "Week one", days=3)
    second = save(db_path, world, "Midweek", start=TODAY + timedelta(days=1), days=2)
    client.post(f"/menus/{first}/accept")
    response = client.post(f"/menus/{second}/accept")
    assert response.status_code == 303
    listing = text_of(client.get("/menus").text)
    assert "Overlaps with accepted Week one" in listing
    assert "Overlaps with accepted Midweek" in listing
