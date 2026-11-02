import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.eaters import Category, EaterRepository
from emu_the_menu.menu_repository import MenuRepository
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import StockRepository
from emu_the_menu.web import create_app

TODAY = date(2026, 11, 2)
QUERY = "go=1&start=2026-11-02&days=2&meal_types=dinner&eater={eater}&seed=0"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


@pytest.fixture
def client(db_path):
    app = create_app(db_path, today=lambda: TODAY, fetch_product=lambda code: {"status": 0})
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def kitchen(db_path):
    """One eater and one reviewed dinner recipe."""
    with EaterRepository(db_path) as eaters:
        eater_id = eaters.add("Sam", Category.MAN).id
    with RecipeRepository(db_path) as recipes:
        recipes.add("Carrot soup", 2, (MealType.DINNER,), Weather.ANY, (), [("carrots", 200, "g")])
    with StockRepository(db_path) as stock:
        carrots = next(i for i in stock.ingredients() if i.name == "carrots")
        stock.set_ingredient_details(carrots.id, 40, (), ())
    return eater_id


def text_of(html):
    html = re.sub(r"<(style|script)\b.*?</\1>", "", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def save(client, kitchen, name="Week one", **extra):
    fields = dict(
        go="1", start="2026-11-02", days="2", meal_types="dinner", eater=str(kitchen), seed="0"
    )
    return client.post("/menu/save", data={**fields, "name": name, **extra})


def test_the_menu_page_offers_to_save_the_plan(client, kitchen):
    html = client.get("/menu?" + QUERY.format(eater=kitchen)).text
    assert 'action="/menu/save"' in html
    assert "Save this menu" in html


def test_there_is_no_save_form_when_nobody_is_set_up_to_eat(client):
    assert "/menu/save" not in client.get("/menu").text


def test_saving_stores_the_plan_and_opens_it(client, db_path, kitchen):
    response = save(client, kitchen)
    assert response.status_code == 303
    with MenuRepository(db_path) as menus:
        [saved] = menus.menus()
    assert response.headers["location"] == f"/menus/{saved.id}"
    assert saved.name == "Week one" and len(saved.meals) == 2


def test_the_saved_menu_page_shows_the_meals_and_portions(client, kitchen):
    page = client.get(save(client, kitchen).headers["location"])
    assert page.status_code == 200
    text = text_of(page.text)
    assert "Week one" in text and "Mon 2 Nov" in text and "Tue 3 Nov" in text
    assert "Carrot soup" in text and "Sam" in text and "kcal" in text


def test_the_saved_menu_does_not_change_when_recipes_are_added(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    with RecipeRepository(db_path) as recipes:
        recipes.add("Pea soup", 2, (MealType.DINNER,), Weather.ANY, (), [("peas", 100, "g")])
    text = text_of(client.get(location).text)
    assert "Carrot soup" in text and "Pea soup" not in text


def test_the_list_shows_saved_menus_and_links_to_them(client, kitchen):
    location = save(client, kitchen, name="Week one").headers["location"]
    html = client.get("/menus").text
    assert f'href="{location}"' in html and "Week one" in text_of(html)


def test_the_list_says_when_there_are_none(client):
    assert "No saved menus" in text_of(client.get("/menus").text)


def test_a_menu_can_be_deleted(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    response = client.post(location + "/delete")
    assert response.status_code == 303 and response.headers["location"] == "/menus"
    with MenuRepository(db_path) as menus:
        assert menus.menus() == []
    assert client.get(location).status_code == 404


def test_an_unknown_menu_is_a_404(client):
    assert client.get("/menus/99").status_code == 404
    assert client.post("/menus/99/delete").status_code == 404


def test_saving_with_bad_settings_saves_nothing(client, db_path, kitchen):
    response = save(client, kitchen, days="0")
    assert response.status_code == 400
    with MenuRepository(db_path) as menus:
        assert menus.menus() == []


def test_the_saved_menu_page_keeps_the_allergy_warning(client, kitchen):
    page = client.get(save(client, kitchen).headers["location"])
    assert "read the label" in text_of(page.text).lower()


def test_the_header_links_to_saved_menus_from_the_menu_page(client, kitchen):
    assert 'href="/menus"' in client.get("/menu").text


# --- changing one meal ------------------------------------------------------------------------


def first_meal(db_path, menu_location):
    menu_id = int(menu_location.rsplit("/", 1)[1])
    with MenuRepository(db_path) as menus:
        return menu_id, menus.get(menu_id).meals[0]


def add_dinner(db_path, name, ingredient, allergens=()):
    with RecipeRepository(db_path) as recipes:
        recipe_id = recipes.add(
            name, 2, (MealType.DINNER,), Weather.ANY, (), [(ingredient, 100, "g")]
        )
    with StockRepository(db_path) as stock:
        found = next(i for i in stock.ingredients() if i.name == ingredient)
        stock.set_ingredient_details(found.id, 100, (), allergens)
    return recipe_id


def test_each_meal_links_to_changing_it(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    menu_id, meal = first_meal(db_path, location)
    assert f'href="/menus/{menu_id}/meals/{meal.id}"' in client.get(location).text


def test_the_change_page_lists_the_recipes_that_suit_everyone(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    add_dinner(db_path, "Pea soup", "peas")
    menu_id, meal = first_meal(db_path, location)
    text = text_of(client.get(f"/menus/{menu_id}/meals/{meal.id}").text)
    assert "Pea soup" in text and "Carrot soup" in text


def test_the_change_page_leaves_out_recipes_with_an_allergen_for_someone_eating(
    client, db_path, kitchen
):
    from emu_the_menu.allergens import Allergen

    location = save(client, kitchen).headers["location"]
    add_dinner(db_path, "Peanut stew", "peanuts", allergens=(Allergen.PEANUTS,))
    with EaterRepository(db_path) as eaters:
        sam = eaters.list()[0]
        eaters.update(sam.id, sam.name, sam.category, allergens=(Allergen.PEANUTS,))
    menu_id, meal = first_meal(db_path, location)
    text = text_of(client.get(f"/menus/{menu_id}/meals/{meal.id}").text)
    assert "Peanut stew" not in text


def test_choosing_a_recipe_swaps_it_and_recalculates_portions(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    pea = add_dinner(db_path, "Pea soup", "peas")
    menu_id, meal = first_meal(db_path, location)
    response = client.post(f"/menus/{menu_id}/meals/{meal.id}", data={"recipe": str(pea)})
    assert response.status_code == 303 and response.headers["location"] == location
    with MenuRepository(db_path) as menus:
        changed = menus.get(menu_id).meals[0]
    assert changed.recipe_id == pea
    assert changed.portions[0].servings != meal.portions[0].servings  # 100 vs 40 kcal a serving
    assert "Pea soup" in text_of(client.get(location).text)


def test_a_recipe_that_does_not_suit_cannot_be_forced_in(client, db_path, kitchen):
    from emu_the_menu.allergens import Allergen

    location = save(client, kitchen).headers["location"]
    nuts = add_dinner(db_path, "Peanut stew", "peanuts", allergens=(Allergen.PEANUTS,))
    with EaterRepository(db_path) as eaters:
        sam = eaters.list()[0]
        eaters.update(sam.id, sam.name, sam.category, allergens=(Allergen.PEANUTS,))
    menu_id, meal = first_meal(db_path, location)
    response = client.post(f"/menus/{menu_id}/meals/{meal.id}", data={"recipe": str(nuts)})
    assert response.status_code == 400
    with MenuRepository(db_path) as menus:
        assert menus.get(menu_id).meals[0].recipe_id == meal.recipe_id


def test_unknown_menus_and_meals_are_404(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    menu_id, meal = first_meal(db_path, location)
    assert client.get(f"/menus/99/meals/{meal.id}").status_code == 404
    assert client.get(f"/menus/{menu_id}/meals/9999").status_code == 404
    assert client.post(f"/menus/{menu_id}/meals/9999", data={"recipe": "1"}).status_code == 404


# --- cooked and Cooldown ----------------------------------------------------------------------


def post_to(client, menu_id, meal_id, action, **data):
    return client.post(f"/menus/{menu_id}/meals/{meal_id}/{action}", data=data)


def test_each_meal_with_a_recipe_offers_to_be_marked_cooked(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    menu_id, meal = first_meal(db_path, location)
    html = client.get(location).text
    assert f'action="/menus/{menu_id}/meals/{meal.id}/cooked"' in html
    assert "Mark cooked" in html


def test_marking_cooked_records_today_and_shows_it(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    menu_id, meal = first_meal(db_path, location)
    response = post_to(client, menu_id, meal.id, "cooked")
    assert response.status_code == 303 and response.headers["location"] == location
    with MenuRepository(db_path) as menus:
        assert menus.get(menu_id).meals[0].cooked_on == TODAY
    html = client.get(location).text
    assert "Cooked Mon 2 Nov" in text_of(html)
    assert f'action="/menus/{menu_id}/meals/{meal.id}/not-cooked"' in html
    assert f'href="/menus/{menu_id}/meals/{meal.id}"' not in html


def test_a_meal_can_be_marked_not_cooked_again(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    menu_id, meal = first_meal(db_path, location)
    post_to(client, menu_id, meal.id, "cooked")
    post_to(client, menu_id, meal.id, "not-cooked")
    with MenuRepository(db_path) as menus:
        assert menus.get(menu_id).meals[0].cooked_on is None


def test_a_cooked_meal_cannot_be_swapped(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    pea = add_dinner(db_path, "Pea soup", "peas")
    menu_id, meal = first_meal(db_path, location)
    post_to(client, menu_id, meal.id, "cooked")
    response = client.post(f"/menus/{menu_id}/meals/{meal.id}", data={"recipe": str(pea)})
    assert response.status_code == 400
    with MenuRepository(db_path) as menus:
        assert menus.get(menu_id).meals[0].recipe_id == meal.recipe_id


def test_cooking_unknown_meals_is_a_404(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    menu_id, _ = first_meal(db_path, location)
    assert post_to(client, menu_id, 9999, "cooked").status_code == 404
    assert post_to(client, 99, 1, "not-cooked").status_code == 404


def next_menu(kitchen, start):
    return f"go=1&start={start}&days=1&meal_types=dinner&eater={kitchen}&cooldown=14"


def test_a_cooked_recipe_is_in_cooldown_for_the_next_menu(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    menu_id, meal = first_meal(db_path, location)
    post_to(client, menu_id, meal.id, "cooked")
    assert "Cooldown: every" in text_of(
        client.get("/menu?" + next_menu(kitchen, "2026-11-05")).text
    )
    assert "Cooldown: every" not in text_of(
        client.get("/menu?" + next_menu(kitchen, "2026-11-20")).text
    )


def test_a_recipe_that_was_only_planned_is_not_in_cooldown(client, kitchen):
    save(client, kitchen)
    text = text_of(client.get("/menu?" + next_menu(kitchen, "2026-11-05")).text)
    assert "Cooldown: every" not in text and "Carrot soup" in text


def test_saving_a_menu_also_respects_the_cooldown(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    menu_id, meal = first_meal(db_path, location)
    post_to(client, menu_id, meal.id, "cooked")
    again = save(client, kitchen, start="2026-11-05", days="1", cooldown="14")
    with MenuRepository(db_path) as menus:
        newest = menus.get(int(again.headers["location"].rsplit("/", 1)[1]))
    assert newest.meals[0].recipe_id is None


# --- cooking uses up stock --------------------------------------------------------------------


def stock_carrots(db_path, grams):
    from emu_the_menu.stock import Location

    with StockRepository(db_path) as stock:
        stock.add("carrots", grams, "g", Location.PANTRY)


def carrots_left(db_path):
    with StockRepository(db_path) as stock:
        return sum(i.quantity for i in stock.list_items() if i.ingredient == "carrots")


def cooked_menu(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    menu_id, meal = first_meal(db_path, location)
    need = 200 * sum(p.servings for p in meal.portions) / 2  # a 2-serving recipe of 200 g
    return location, menu_id, meal, need


def test_cooking_takes_the_scaled_amount_from_stock(client, db_path, kitchen):
    location, menu_id, meal, need = cooked_menu(client, db_path, kitchen)
    stock_carrots(db_path, need + 1000)
    post_to(client, menu_id, meal.id, "cooked")
    assert carrots_left(db_path) == pytest.approx(1000)
    assert "short" not in text_of(client.get(location).text).lower().replace("shopping", "")


def test_cooking_with_too_little_stock_uses_it_up_and_says_what_was_short(client, db_path, kitchen):
    location, menu_id, meal, need = cooked_menu(client, db_path, kitchen)
    stock_carrots(db_path, 1000)
    response = post_to(client, menu_id, meal.id, "cooked")
    assert response.status_code == 303
    assert carrots_left(db_path) == 0
    text = text_of(client.get(location).text)
    assert "Cooked" in text and "Short in stock: carrots" in text


def test_cooking_with_nothing_in_stock_still_works(client, db_path, kitchen):
    location, menu_id, meal, _ = cooked_menu(client, db_path, kitchen)
    post_to(client, menu_id, meal.id, "cooked")
    assert "Short in stock: carrots" in text_of(client.get(location).text)
    with MenuRepository(db_path) as menus:
        assert menus.get(menu_id).meals[0].cooked_on == TODAY


def test_pressing_cooked_twice_takes_stock_only_once(client, db_path, kitchen):
    _, menu_id, meal, need = cooked_menu(client, db_path, kitchen)
    stock_carrots(db_path, need * 3)
    post_to(client, menu_id, meal.id, "cooked")
    post_to(client, menu_id, meal.id, "cooked")
    assert carrots_left(db_path) == pytest.approx(need * 2)


def test_not_cooked_puts_the_stock_back_where_it_came_from(client, db_path, kitchen):
    from datetime import timedelta

    from emu_the_menu.stock import Location

    location, menu_id, meal, need = cooked_menu(client, db_path, kitchen)
    soon = TODAY + timedelta(days=3)
    with StockRepository(db_path) as stock:
        stock.add("carrots", need * 2, "g", Location.FRIDGE, soon)
    post_to(client, menu_id, meal.id, "cooked")
    assert carrots_left(db_path) == pytest.approx(need)
    post_to(client, menu_id, meal.id, "not-cooked")
    with StockRepository(db_path) as stock:
        [item] = stock.list_items()
    assert (item.quantity, item.location, item.expires_on) == (
        pytest.approx(need * 2),
        Location.FRIDGE,
        soon,
    )


def test_not_cooked_twice_puts_back_only_once(client, db_path, kitchen):
    _, menu_id, meal, need = cooked_menu(client, db_path, kitchen)
    stock_carrots(db_path, need * 2)
    post_to(client, menu_id, meal.id, "cooked")
    post_to(client, menu_id, meal.id, "not-cooked")
    post_to(client, menu_id, meal.id, "not-cooked")
    assert carrots_left(db_path) == pytest.approx(need * 2)


def test_not_cooked_puts_back_only_what_was_really_taken(client, db_path, kitchen):
    _, menu_id, meal, need = cooked_menu(client, db_path, kitchen)
    stock_carrots(db_path, 1000)  # less than the meal needs
    post_to(client, menu_id, meal.id, "cooked")
    assert carrots_left(db_path) == 0
    post_to(client, menu_id, meal.id, "not-cooked")
    assert carrots_left(db_path) == pytest.approx(1000)


def test_cooking_again_after_not_cooked_takes_stock_again(client, db_path, kitchen):
    _, menu_id, meal, need = cooked_menu(client, db_path, kitchen)
    stock_carrots(db_path, need * 2)
    post_to(client, menu_id, meal.id, "cooked")
    post_to(client, menu_id, meal.id, "not-cooked")
    post_to(client, menu_id, meal.id, "cooked")
    assert carrots_left(db_path) == pytest.approx(need)


def test_the_not_cooked_button_says_it_puts_the_stock_back(client, db_path, kitchen):
    location, menu_id, meal, need = cooked_menu(client, db_path, kitchen)
    post_to(client, menu_id, meal.id, "cooked")
    html = client.get(location).text
    assert "puts the ingredients back in Stock" in html
    assert "does not put the ingredients back" not in html


# --- the shopping list with amounts -----------------------------------------------------------


def amount_text(grams):
    from emu_the_menu.units import BaseUnit, display_quantity

    amount, unit = display_quantity(grams, BaseUnit.GRAM)
    return f"carrots {amount:g} {unit}"


def test_the_saved_menu_shopping_list_has_amounts_less_what_is_held(client, db_path, kitchen):
    location, menu_id, meal, need = cooked_menu(client, db_path, kitchen)
    stock_carrots(db_path, 1000)
    text = text_of(client.get(location).text)
    assert amount_text(need - 1000) in text
    assert "Amounts are not totalled yet" not in text


def test_the_shopping_list_drops_a_meal_once_it_is_cooked(client, db_path, kitchen):
    location, menu_id, meal, need = cooked_menu(client, db_path, kitchen)
    assert amount_text(need) in text_of(client.get(location).text)
    post_to(client, menu_id, meal.id, "cooked")
    # the second day is a Cooldown gap, so nothing else is left to buy
    text = text_of(client.get(location).text)
    assert "carrots" not in text.split("Shopping list")[1] and "Nothing to buy" in text


def test_the_menu_page_shopping_list_has_amounts_too(client, db_path, kitchen):
    text = text_of(client.get("/menu?" + QUERY.format(eater=kitchen)).text)
    assert "carrots" in text and "Amounts are not totalled yet" not in text
    assert " kg" in text


# --- amounts and expiry in the planning and cooking pages -------------------------------------


def test_cooking_leaves_expired_stock_alone_and_counts_it_as_short(client, db_path, kitchen):
    from datetime import timedelta

    from emu_the_menu.stock import Location

    location, menu_id, meal, need = cooked_menu(client, db_path, kitchen)
    with StockRepository(db_path) as stock:
        stock.add("carrots", need * 2, "g", Location.PANTRY, TODAY - timedelta(days=1))
    post_to(client, menu_id, meal.id, "cooked")
    assert carrots_left(db_path) == pytest.approx(need * 2)
    assert "Short in stock: carrots" in text_of(client.get(location).text)


def test_the_menu_page_does_not_count_a_little_stock_as_enough(client, db_path, kitchen):
    stock_carrots(db_path, 100)
    query = QUERY.format(eater=kitchen) + "&allowance=0"
    assert "Shopping Allowance" in text_of(client.get("/menu?" + query).text)


def test_the_menu_page_accepts_plenty_of_stock_under_a_zero_allowance(client, db_path, kitchen):
    stock_carrots(db_path, 100_000)
    query = QUERY.format(eater=kitchen) + "&allowance=0"
    assert "Carrot soup" in text_of(client.get("/menu?" + query).text)


# --- reshuffling one meal ---------------------------------------------------------------------


def test_each_uncooked_meal_offers_to_pick_another_recipe(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    menu_id, meal = first_meal(db_path, location)
    html = client.get(location).text
    assert f'action="/menus/{menu_id}/meals/{meal.id}/reshuffle"' in html
    assert "Pick another" in html
    post_to(client, menu_id, meal.id, "cooked")
    assert f"/menus/{menu_id}/meals/{meal.id}/reshuffle" not in client.get(location).text


def test_reshuffling_swaps_in_a_different_suitable_recipe(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    pea = add_dinner(db_path, "Pea soup", "peas")
    menu_id, meal = first_meal(db_path, location)
    response = post_to(client, menu_id, meal.id, "reshuffle")
    assert response.status_code == 303 and response.headers["location"] == location
    with MenuRepository(db_path) as menus:
        changed = menus.get(menu_id).meals[0]
    assert changed.recipe_id == pea
    assert changed.portions and changed.portions[0].servings > 0


def test_reshuffling_never_picks_a_recipe_that_does_not_suit(client, db_path, kitchen):
    from emu_the_menu.allergens import Allergen

    location = save(client, kitchen).headers["location"]
    add_dinner(db_path, "Peanut stew", "peanuts", allergens=(Allergen.PEANUTS,))
    with EaterRepository(db_path) as eaters:
        sam = eaters.list()[0]
        eaters.update(sam.id, sam.name, sam.category, allergens=(Allergen.PEANUTS,))
    menu_id, meal = first_meal(db_path, location)
    response = post_to(client, menu_id, meal.id, "reshuffle")
    assert response.status_code == 400
    assert "No other recipe" in text_of(response.text)
    with MenuRepository(db_path) as menus:
        assert menus.get(menu_id).meals[0].recipe_id == meal.recipe_id


def test_reshuffling_with_no_other_recipe_says_so_and_changes_nothing(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    menu_id, meal = first_meal(db_path, location)
    response = post_to(client, menu_id, meal.id, "reshuffle")
    assert response.status_code == 400 and "No other recipe" in text_of(response.text)
    with MenuRepository(db_path) as menus:
        assert menus.get(menu_id).meals[0].recipe_id == meal.recipe_id


def test_reshuffling_fills_a_gap_in_the_menu(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    add_dinner(db_path, "Pea soup", "peas")
    menu_id, _ = first_meal(db_path, location)
    with MenuRepository(db_path) as menus:
        gap = menus.get(menu_id).meals[1]
    assert gap.recipe_id is None
    response = post_to(client, menu_id, gap.id, "reshuffle")
    assert response.status_code == 303
    with MenuRepository(db_path) as menus:
        assert menus.get(menu_id).meals[1].recipe_id is not None


def test_a_cooked_meal_cannot_be_reshuffled(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    add_dinner(db_path, "Pea soup", "peas")
    menu_id, meal = first_meal(db_path, location)
    post_to(client, menu_id, meal.id, "cooked")
    assert post_to(client, menu_id, meal.id, "reshuffle").status_code == 400
    with MenuRepository(db_path) as menus:
        assert menus.get(menu_id).meals[0].recipe_id == meal.recipe_id


def test_reshuffling_unknown_meals_is_a_404(client, db_path, kitchen):
    location = save(client, kitchen).headers["location"]
    menu_id, _ = first_meal(db_path, location)
    assert post_to(client, menu_id, 9999, "reshuffle").status_code == 404
    assert post_to(client, 99, 1, "reshuffle").status_code == 404


def test_a_gap_meal_still_knows_who_is_at_the_table(client, db_path, kitchen):
    from emu_the_menu.allergens import Allergen

    with EaterRepository(db_path) as eaters:
        sam = eaters.list()[0]
        eaters.update(sam.id, sam.name, sam.category, allergens=(Allergen.PEANUTS,))
    location = save(client, kitchen).headers["location"]
    add_dinner(db_path, "Peanut stew", "peanuts", allergens=(Allergen.PEANUTS,))
    menu_id, _ = first_meal(db_path, location)
    with MenuRepository(db_path) as menus:
        gap = menus.get(menu_id).meals[1]
    assert gap.recipe_id is None
    page = client.get(f"/menus/{menu_id}/meals/{gap.id}")
    assert page.status_code == 200
    text = text_of(page.text)
    assert "Carrot soup" in text and "Peanut stew" not in text
    forced = client.post(f"/menus/{menu_id}/meals/{gap.id}", data={"recipe": "2"})
    assert forced.status_code == 400
