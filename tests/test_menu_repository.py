from datetime import date

import pytest

from emu_the_menu.eaters import Category, EaterRepository
from emu_the_menu.menu_repository import MenuNotFoundError, MenuRepository
from emu_the_menu.planner import Meal, Menu, Portion, Slot
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather

START = date(2026, 11, 2)


@pytest.fixture
def repo(db_url):
    with MenuRepository(db_url) as r:
        yield r


@pytest.fixture
def sam(db_url):
    with EaterRepository(db_url) as eaters:
        eaters.add("Sam", Category.MAN)
        return eaters.list()[0]


@pytest.fixture
def soup(db_url):
    with RecipeRepository(db_url) as recipes:
        recipe_id = recipes.add(
            "Soup", 2, (MealType.DINNER,), Weather.ANY, (), [("carrots", 200, "g")]
        )
        return recipes.get(recipe_id)


def one_meal_menu(soup, sam, servings=1.5):
    meal = Meal(START, Slot.DINNER, soup, (Portion(sam, servings, 300.0),))
    return Menu((meal,), ())


def test_a_saved_menu_comes_back_with_its_meals_and_portions(repo, soup, sam):
    menu_id = repo.save("Week one", START, 1, one_meal_menu(soup, sam))
    saved = repo.get(menu_id)
    assert (saved.name, saved.start, saved.days) == ("Week one", START, 1)
    [meal] = saved.meals
    assert (meal.day, meal.slot, meal.recipe_id) == (START, Slot.DINNER, soup.id)
    assert [(p.eater_id, p.servings) for p in meal.portions] == [(sam.id, 1.5)]
    assert meal.cooked_on is None


def test_a_meal_with_no_recipe_keeps_the_reason(repo):
    empty = Meal(START, Slot.LUNCH, None, reason="No recipes are tagged for lunch")
    saved = repo.get(repo.save("Gaps", START, 1, Menu((empty,), ())))
    assert saved.meals[0].recipe_id is None
    assert saved.meals[0].reason == "No recipes are tagged for lunch"


def test_meals_come_back_in_the_order_of_the_day(repo, soup, sam):
    later = Meal(START, Slot.DINNER, soup, (Portion(sam, 1, 100.0),))
    sooner = Meal(START, Slot.BREAKFAST, soup, (Portion(sam, 1, 100.0),))
    next_day = Meal(date(2026, 11, 3), Slot.BREAKFAST, soup, (Portion(sam, 1, 100.0),))
    saved = repo.get(repo.save("Order", START, 2, Menu((next_day, later, sooner), ())))
    assert [(m.day.day, m.slot) for m in saved.meals] == [
        (2, Slot.BREAKFAST),
        (2, Slot.DINNER),
        (3, Slot.BREAKFAST),
    ]


def test_a_blank_name_gets_a_name_from_the_start_date(repo, soup, sam):
    saved = repo.get(repo.save("  ", START, 1, one_meal_menu(soup, sam)))
    assert saved.name == "Menu from 2 Nov 2026"


def test_menus_are_listed_newest_first(repo, soup, sam):
    first = repo.save("First", START, 1, one_meal_menu(soup, sam))
    second = repo.save("Second", START, 1, one_meal_menu(soup, sam))
    assert [m.id for m in repo.menus()] == [second, first]


def test_a_missing_menu_is_none_and_cannot_be_deleted(repo):
    assert repo.get(99) is None
    with pytest.raises(MenuNotFoundError):
        repo.delete(99)


def test_deleting_a_menu_removes_its_meals(repo, soup, sam):
    menu_id = repo.save("Gone", START, 1, one_meal_menu(soup, sam))
    repo.delete(menu_id)
    assert repo.menus() == []
    assert repo.get(menu_id) is None


def test_deleting_a_recipe_leaves_the_meal_without_one(repo, db_url, soup, sam):
    saved = repo.get(repo.save("Keep", START, 1, one_meal_menu(soup, sam)))
    with RecipeRepository(db_url) as recipes:
        recipes.delete(soup.id)
    meal = repo.get(saved.id).meals[0]
    assert meal.recipe_id is None
    assert "deleted" in meal.reason


def test_removing_an_eater_drops_their_portions(repo, db_url, soup, sam):
    saved = repo.get(repo.save("Keep", START, 1, one_meal_menu(soup, sam)))
    with EaterRepository(db_url) as eaters:
        eaters.delete(sam.id)
    assert repo.get(saved.id).meals[0].portions == ()


# --- changing a meal --------------------------------------------------------------------------


@pytest.fixture
def stew(db_url):
    with RecipeRepository(db_url) as recipes:
        recipe_id = recipes.add(
            "Stew", 2, (MealType.DINNER,), Weather.ANY, (), [("beef", 300, "g")]
        )
        return recipes.get(recipe_id)


def test_a_meal_can_be_given_another_recipe_and_portions(repo, soup, stew, sam):
    saved = repo.get(repo.save("Swap", START, 1, one_meal_menu(soup, sam)))
    meal = saved.meals[0]
    repo.set_meal(saved.id, meal.id, stew.id, [(sam.id, 2.0)])
    changed = repo.get(saved.id).meals[0]
    assert changed.recipe_id == stew.id
    assert [(p.eater_id, p.servings) for p in changed.portions] == [(sam.id, 2.0)]
    assert changed.reason == ""


def test_a_meal_that_had_no_recipe_can_be_given_one(repo, soup, sam):
    empty = Meal(START, Slot.DINNER, None, reason="No recipe suits everyone at the table")
    saved = repo.get(repo.save("Gap", START, 1, Menu((empty,), ())))
    repo.set_meal(saved.id, saved.meals[0].id, soup.id, [(sam.id, 1.0)])
    assert repo.get(saved.id).meals[0].recipe_id == soup.id


def test_a_meal_of_another_menu_cannot_be_changed(repo, soup, stew, sam):
    one = repo.get(repo.save("One", START, 1, one_meal_menu(soup, sam)))
    two = repo.get(repo.save("Two", START, 1, one_meal_menu(soup, sam)))
    with pytest.raises(MenuNotFoundError):
        repo.set_meal(one.id, two.meals[0].id, stew.id, [(sam.id, 1.0)])
    assert repo.get(two.id).meals[0].recipe_id == soup.id


def test_a_failed_change_leaves_the_meal_as_it_was(repo, soup, sam):
    saved = repo.get(repo.save("Keep", START, 1, one_meal_menu(soup, sam)))
    with pytest.raises(Exception):  # noqa: B017 - a recipe id that does not exist
        repo.set_meal(saved.id, saved.meals[0].id, 999, [(sam.id, 2.0)])
    kept = repo.get(saved.id).meals[0]
    assert kept.recipe_id == soup.id
    assert [(p.eater_id, p.servings) for p in kept.portions] == [(sam.id, 1.5)]


# --- cooking ----------------------------------------------------------------------------------

COOKED = date(2026, 11, 3)


def saved_meal(repo, soup, sam):
    saved = repo.get(repo.save("Cook", START, 1, one_meal_menu(soup, sam)))
    return saved.id, saved.meals[0].id


def test_a_meal_can_be_marked_cooked_and_unmarked(repo, soup, sam):
    menu_id, meal_id = saved_meal(repo, soup, sam)
    repo.set_cooked(menu_id, meal_id, COOKED)
    assert repo.get(menu_id).meals[0].cooked_on == COOKED
    repo.set_cooked(menu_id, meal_id, None)
    assert repo.get(menu_id).meals[0].cooked_on is None


def test_a_meal_with_no_recipe_cannot_be_cooked(repo):
    empty = Meal(START, Slot.LUNCH, None, reason="none")
    saved = repo.get(repo.save("Gap", START, 1, Menu((empty,), ())))
    with pytest.raises(ValueError, match="recipe"):
        repo.set_cooked(saved.id, saved.meals[0].id, COOKED)


def test_a_meal_of_another_menu_cannot_be_marked_cooked(repo, soup, sam):
    menu_id, _ = saved_meal(repo, soup, sam)
    _, other_meal = saved_meal(repo, soup, sam)
    with pytest.raises(MenuNotFoundError):
        repo.set_cooked(menu_id, other_meal, COOKED)


def test_a_cooked_meal_cannot_be_given_another_recipe(repo, soup, stew, sam):
    menu_id, meal_id = saved_meal(repo, soup, sam)
    repo.set_cooked(menu_id, meal_id, COOKED)
    with pytest.raises(ValueError, match="cooked"):
        repo.set_meal(menu_id, meal_id, stew.id, [(sam.id, 1.0)])
    assert repo.get(menu_id).meals[0].recipe_id == soup.id


def test_last_served_is_the_latest_cooked_date_of_each_recipe(repo, soup, sam):
    first_menu, first_meal = saved_meal(repo, soup, sam)
    second_menu, second_meal = saved_meal(repo, soup, sam)
    saved_meal(repo, soup, sam)  # never cooked
    repo.set_cooked(first_menu, first_meal, date(2026, 11, 1))
    repo.set_cooked(second_menu, second_meal, COOKED)
    assert repo.last_served() == {soup.id: COOKED}


def test_nothing_cooked_means_nothing_served(repo, soup, sam):
    saved_meal(repo, soup, sam)
    assert repo.last_served() == {}


def test_a_note_about_what_was_short_is_kept_with_the_cooked_meal(repo, soup, sam):
    menu_id, meal_id = saved_meal(repo, soup, sam)
    repo.set_cooked(menu_id, meal_id, COOKED, short="carrots 100 g")
    assert repo.get(menu_id).meals[0].short == "carrots 100 g"
    repo.set_cooked(menu_id, meal_id, None)
    assert repo.get(menu_id).meals[0].short == ""


def test_what_cooking_took_from_stock_is_kept_and_returned(repo, soup, sam):
    from emu_the_menu.stock import Location, TakenLot

    menu_id, meal_id = saved_meal(repo, soup, sam)
    lots = [
        TakenLot("carrots", 200, "g", Location.FRIDGE, date(2026, 11, 9)),
        TakenLot("carrots", 50, "g", Location.PANTRY, None),
    ]
    repo.set_cooked(menu_id, meal_id, COOKED, took=lots)
    assert repo.taken(menu_id, meal_id) == lots


def test_marking_not_cooked_forgets_what_was_taken(repo, soup, sam):
    from emu_the_menu.stock import Location, TakenLot

    menu_id, meal_id = saved_meal(repo, soup, sam)
    repo.set_cooked(
        menu_id, meal_id, COOKED, took=[TakenLot("carrots", 5, "g", Location.PANTRY, None)]
    )
    repo.set_cooked(menu_id, meal_id, None)
    assert repo.taken(menu_id, meal_id) == []


def test_a_meal_cooked_without_a_record_has_nothing_to_give_back(repo, soup, sam):
    menu_id, meal_id = saved_meal(repo, soup, sam)
    repo.set_cooked(menu_id, meal_id, COOKED)
    assert repo.taken(menu_id, meal_id) == []


def test_what_was_taken_belongs_to_a_meal_of_that_menu(repo, soup, sam):
    menu_id, _ = saved_meal(repo, soup, sam)
    _, other_meal = saved_meal(repo, soup, sam)
    with pytest.raises(MenuNotFoundError):
        repo.taken(menu_id, other_meal)


def test_a_gap_keeps_the_people_at_its_table_with_no_servings(repo, sam):
    empty = Meal(START, Slot.LUNCH, None, reason="none")
    saved = repo.get(repo.save("Gap", START, 1, Menu((empty,), ()), table=[sam]))
    assert [(p.eater_id, p.servings) for p in saved.meals[0].portions] == [(sam.id, 0.0)]


# --- accepting a menu -------------------------------------------------------------------------


def test_a_new_menu_is_not_accepted(repo, soup, sam):
    saved = repo.get(repo.save("New", START, 1, one_meal_menu(soup, sam)))
    assert saved.accepted_on is None and saved.accepted_order is None


def test_a_menu_can_be_accepted_and_unaccepted(repo, soup, sam):
    menu_id = repo.save("Take", START, 1, one_meal_menu(soup, sam))
    repo.accept(menu_id, COOKED)
    accepted = repo.get(menu_id)
    assert accepted.accepted_on == COOKED and accepted.accepted_order == 1
    repo.unaccept(menu_id)
    again = repo.get(menu_id)
    assert again.accepted_on is None and again.accepted_order is None


def test_menus_are_numbered_in_the_order_they_were_accepted(repo, soup, sam):
    first = repo.save("A", START, 1, one_meal_menu(soup, sam))
    second = repo.save("B", START, 1, one_meal_menu(soup, sam))
    repo.accept(second, COOKED)
    repo.accept(first, COOKED)
    assert [m.id for m in repo.accepted_menus()] == [second, first]
    assert [repo.get(i).accepted_order for i in (second, first)] == [1, 2]


def test_accepting_again_keeps_the_place_in_line(repo, soup, sam):
    first = repo.save("A", START, 1, one_meal_menu(soup, sam))
    second = repo.save("B", START, 1, one_meal_menu(soup, sam))
    repo.accept(first, COOKED)
    repo.accept(second, COOKED)
    repo.accept(first, date(2026, 11, 9))
    assert repo.get(first).accepted_order == 1
    assert repo.get(first).accepted_on == COOKED


def test_a_menu_accepted_after_a_gap_goes_to_the_back(repo, soup, sam):
    first = repo.save("A", START, 1, one_meal_menu(soup, sam))
    second = repo.save("B", START, 1, one_meal_menu(soup, sam))
    repo.accept(first, COOKED)
    repo.unaccept(first)
    repo.accept(second, COOKED)
    repo.accept(first, COOKED)
    assert [m.id for m in repo.accepted_menus()] == [second, first]


def test_accepting_an_unknown_menu_is_reported(repo):
    with pytest.raises(MenuNotFoundError):
        repo.accept(99, COOKED)
    with pytest.raises(MenuNotFoundError):
        repo.unaccept(99)
