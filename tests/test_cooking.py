import itertools

import pytest

from emu_the_menu.cooking import Use, uses_for
from emu_the_menu.recipe_repository import Recipe, RecipeLine
from emu_the_menu.recipes import Weather
from emu_the_menu.repository import Ingredient
from emu_the_menu.units import BaseUnit

_ids = itertools.count(1)


def ing(name, unit=BaseUnit.GRAM):
    return Ingredient(name, unit, id=next(_ids))


def recipe(*lines, servings=2):
    return Recipe(next(_ids), "r", servings, (), Weather.ANY, (), tuple(lines))


CARROTS, ONIONS, WATER, BROTH = ing("carrots"), ing("onions"), ing("water"), ing("broth")


def test_each_line_uses_its_quantity_scaled_to_the_servings_cooked():
    r = recipe(RecipeLine(CARROTS, 200), RecipeLine(ONIONS, 100))
    uses = uses_for(r, 1.5, {CARROTS.id: 1000, ONIONS.id: 1000})
    assert uses == [Use(CARROTS, 300, 300), Use(ONIONS, 150, 150)]


def test_only_what_is_in_stock_is_taken_and_the_rest_is_short():
    r = recipe(RecipeLine(CARROTS, 200))
    [use] = uses_for(r, 1, {CARROTS.id: 120})
    assert (use.needed, use.taken, use.short) == (200, 120, 80)


def test_an_ingredient_not_in_stock_at_all_is_wholly_short():
    r = recipe(RecipeLine(CARROTS, 200))
    [use] = uses_for(r, 1, {})
    assert (use.taken, use.short) == (0, 200)


def test_of_alternatives_the_first_that_covers_the_amount_is_used():
    r = recipe(RecipeLine(WATER, 500, 1), RecipeLine(BROTH, 500, 1))
    assert uses_for(r, 1, {WATER.id: 100, BROTH.id: 900}) == [Use(BROTH, 500, 500)]
    assert uses_for(r, 1, {WATER.id: 900, BROTH.id: 900}) == [Use(WATER, 500, 500)]


def test_with_no_alternative_covering_it_the_one_with_most_stock_is_used():
    r = recipe(RecipeLine(WATER, 500, 1), RecipeLine(BROTH, 500, 1))
    [use] = uses_for(r, 1, {WATER.id: 100, BROTH.id: 300})
    assert (use.ingredient, use.taken, use.short) == (BROTH, 300, 200)


def test_with_no_alternative_in_stock_the_first_is_the_one_short():
    r = recipe(RecipeLine(WATER, 500, 1), RecipeLine(BROTH, 500, 1))
    [use] = uses_for(r, 1, {})
    assert (use.ingredient, use.taken, use.short) == (WATER, 0, 500)


def test_nothing_is_taken_for_a_zero_scale():
    r = recipe(RecipeLine(CARROTS, 200))
    assert uses_for(r, 0, {CARROTS.id: 100}) == []


@pytest.mark.parametrize("scale", [-1, -0.5])
def test_a_negative_scale_is_rejected(scale):
    with pytest.raises(ValueError):
        uses_for(recipe(RecipeLine(CARROTS, 200)), scale, {})


# --- the shopping list with amounts -----------------------------------------------------------


def meal(recipe_, servings, cooked_on=None):
    from datetime import date

    from emu_the_menu.eaters import Category, Eater
    from emu_the_menu.planner import Meal, Portion, Slot

    eater = Eater(next(_ids), "Sam", Category.MAN)
    return Meal(
        date(2026, 11, 2),
        Slot.DINNER,
        recipe_,
        (Portion(eater, servings, None),),
        cooked_on=cooked_on,
    )


def test_the_amounts_to_buy_are_what_the_meals_need_less_what_is_held():
    from emu_the_menu.cooking import shopping_amounts

    r = recipe(RecipeLine(CARROTS, 200), RecipeLine(ONIONS, 100))
    assert shopping_amounts([meal(r, 2)], {CARROTS.id: 50, ONIONS.id: 1000}) == [(CARROTS, 150)]


def test_stock_is_shared_out_between_meals_in_order():
    from emu_the_menu.cooking import shopping_amounts

    r = recipe(RecipeLine(CARROTS, 200))
    meals = [meal(r, 2), meal(r, 2), meal(r, 1)]  # 200 + 200 + 100 needed, 250 held
    assert shopping_amounts(meals, {CARROTS.id: 250}) == [(CARROTS, 250)]


def test_cooked_meals_need_nothing_more():
    from datetime import date

    from emu_the_menu.cooking import shopping_amounts

    r = recipe(RecipeLine(CARROTS, 200))
    assert shopping_amounts([meal(r, 2, cooked_on=date(2026, 11, 2))], {}) == []


def test_meals_without_a_recipe_need_nothing():
    from emu_the_menu.cooking import shopping_amounts

    assert shopping_amounts([meal(None, 1)], {}) == []


def test_the_list_is_sorted_by_name():
    from emu_the_menu.cooking import shopping_amounts

    r = recipe(RecipeLine(ONIONS, 100), RecipeLine(CARROTS, 100))
    assert [i.name for i, _ in shopping_amounts([meal(r, 2)], {})] == ["carrots", "onions"]


# --- a pantry with amounts and dates ----------------------------------------------------------

from datetime import date, timedelta  # noqa: E402

from emu_the_menu.cooking import Pantry  # noqa: E402

DAY = date(2026, 11, 2)


def test_the_pantry_counts_only_stock_that_has_not_expired_by_the_day():
    pantry = Pantry({1: [(100, DAY - timedelta(days=1)), (50, DAY), (30, None)]})
    assert pantry.available(1, DAY) == 80
    assert pantry.available(1, DAY - timedelta(days=1)) == 180
    assert pantry.available(2, DAY) == 0


def test_taking_uses_the_earliest_expiry_first_and_can_empty_a_lot():
    pantry = Pantry({1: [(100, DAY + timedelta(days=5)), (100, DAY + timedelta(days=1))]})
    pantry.take(1, 120, DAY)
    assert pantry.available(1, DAY + timedelta(days=2)) == 80  # the sooner lot is gone first
    assert pantry.available(1, DAY + timedelta(days=6)) == 0


def test_a_date_or_none_means_an_unlimited_amount_until_then():
    pantry = Pantry({1: DAY + timedelta(days=3), 2: None})
    assert pantry.available(1, DAY) > 10**9 and pantry.available(1, DAY + timedelta(days=4)) == 0
    assert pantry.available(2, DAY + timedelta(days=400)) > 10**9
    pantry.take(2, 5000, DAY)
    assert pantry.available(2, DAY) > 10**9


def test_a_plain_number_is_an_amount_with_no_expiry():
    assert Pantry({1: 250.0}).available(1, DAY) == 250


def test_expiring_means_some_usable_stock_that_runs_out_within_the_days_given():
    pantry = Pantry({1: [(10, DAY + timedelta(days=3))], 2: [(10, DAY + timedelta(days=30))]})
    assert pantry.expiring(1, DAY, within=7) and not pantry.expiring(2, DAY, within=7)


def test_shopping_does_not_count_stock_that_expires_before_the_meal():
    from emu_the_menu.cooking import shopping_amounts

    r = recipe(RecipeLine(CARROTS, 200))
    spoilt = {CARROTS.id: [(1000, DAY - timedelta(days=1))]}
    assert shopping_amounts([meal(r, 2)], spoilt) == [(CARROTS, 200)]
