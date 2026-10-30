import itertools
import random
from datetime import date, timedelta

import pytest

from emu_the_menu.allergens import Allergen
from emu_the_menu.eaters import Category, Diet, Eater
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.planner import PlanSettings, Slot, Weights, plan
from emu_the_menu.recipe_repository import Recipe, RecipeLine
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import Ingredient
from emu_the_menu.units import BaseUnit

TODAY = date(2026, 11, 2)
D, L, B = MealType.DINNER, MealType.LUNCH, MealType.BREAKFAST

_ids = itertools.count(1)
_ingredients: dict[str, Ingredient] = {}


def ing(name, kcal=100, groups=(), allergens=(), reviewed=True):
    key = (name, kcal, tuple(groups), tuple(allergens), reviewed)
    if key not in _ingredients:
        _ingredients[key] = Ingredient(
            name,
            BaseUnit.GRAM,
            id=next(_ids),
            kcal=kcal,
            food_groups=tuple(groups),
            allergens=tuple(allergens),
            reviewed=reviewed,
        )
    return _ingredients[key]


def recipe(name, ingredients, meals=(D,), servings=1, grams=100, weather=Weather.ANY):
    lines = tuple(RecipeLine(i, grams) for i in ingredients)
    return Recipe(next(_ids), name, servings, tuple(meals), weather, (), lines)


def eater(name="Sam", goal=2000, **kwargs):
    # A MAN's default goal is 2500, so adjust to hit the wanted goal.
    return Eater(next(_ids), name, Category.MAN, default_goal=goal, **kwargs)


def slot_of(meal):
    """A Slot for a main MealType, so most tests can keep saying (L, D)."""
    return meal if isinstance(meal, Slot) else Slot(meal.value)


def make_plan(recipes, eaters=None, *, days=3, meals=(D,), have=None, **kwargs):
    kwargs.setdefault("settings", PlanSettings(shopping_allowance=99, cooldown_days=0))
    return plan(
        start=TODAY,
        days=days,
        slots=[slot_of(m) for m in meals],
        recipes=recipes,
        eaters=eaters if eaters is not None else [eater()],
        stock=have or {},
        seed=kwargs.pop("seed", 1),
        **kwargs,
    )


def names(menu):
    return [m.recipe.name if m.recipe else None for m in menu.meals]


RICE = ing("rice")
BEANS = ing("beans")
PASTA = ing("pasta")

# --- the shape of a menu ----------------------------------------------------------------------


def test_every_day_and_meal_type_gets_a_slot_in_date_order():
    recipes = [recipe("a", [RICE], meals=(L, D)), recipe("b", [BEANS], meals=(L, D))]
    menu = make_plan(recipes, days=2, meals=(L, D))
    assert [(m.day, m.meal_type) for m in menu.meals] == [
        (TODAY, L), (TODAY, D), (TODAY + timedelta(1), L), (TODAY + timedelta(1), D)
    ]  # fmt: skip


def test_only_recipes_for_that_meal_type_are_used():
    recipes = [recipe("porridge", [RICE], meals=(B,)), recipe("stew", [BEANS], meals=(D,))]
    menu = make_plan(recipes, days=1, meals=(B, D))
    assert names(menu) == ["porridge", "stew"]


def test_a_slot_with_no_recipe_says_why():
    menu = make_plan([], days=1)
    assert menu.meals[0].recipe is None
    assert "no recipes" in menu.meals[0].reason.lower()


def test_a_plan_of_no_days_is_empty():
    assert make_plan([recipe("a", [RICE])], days=0).meals == ()


# --- the hard rules ---------------------------------------------------------------------------


def test_a_recipe_that_blocks_any_eater_is_never_used():
    cheese = ing("cheese", groups=[FoodGroup.DAIRY], allergens=[Allergen.MILK])
    recipes = [recipe("cheesy", [cheese]), recipe("plain", [RICE])]
    eaters = [eater("Sam"), eater("Anna", allergens=(Allergen.MILK,))]
    menu = make_plan(recipes, eaters, days=3)
    assert "cheesy" not in names(menu)


def test_the_reason_says_when_nothing_suits_everyone():
    cheese = ing("cheese", allergens=[Allergen.MILK])
    menu = make_plan([recipe("cheesy", [cheese])], [eater(allergens=(Allergen.MILK,))], days=1)
    assert menu.meals[0].recipe is None
    assert "suits everyone" in menu.meals[0].reason


def test_an_unchecked_ingredient_keeps_a_recipe_from_someone_with_an_allergy():
    mystery = ing("sauce", reviewed=False)
    menu = make_plan([recipe("saucy", [mystery])], [eater(allergies=("kiwi",))], days=1)
    assert menu.meals[0].recipe is None


def test_a_diet_is_respected():
    beef = ing("beef", groups=[FoodGroup.MEAT])
    recipes = [recipe("steak", [beef]), recipe("beans", [BEANS])]
    menu = make_plan(recipes, [eater(diet=Diet.VEGETARIAN)], days=2)
    assert set(names(menu)) <= {"beans", None}
    assert "steak" not in names(menu)


def test_the_menu_is_for_the_eaters_given():
    cheese = ing("cheese", allergens=[Allergen.MILK])
    recipes = [recipe("cheesy", [cheese])]
    allergic, fine = eater("Anna", allergens=(Allergen.MILK,)), eater("Sam")
    assert make_plan(recipes, [fine], days=1).meals[0].recipe is not None
    assert make_plan(recipes, [allergic, fine], days=1).meals[0].recipe is None


# --- cooldown ---------------------------------------------------------------------------------


def test_a_recipe_does_not_repeat_within_its_cooldown():
    recipes = [recipe("a", [RICE]), recipe("b", [BEANS])]
    menu = make_plan(recipes, days=3, settings=PlanSettings(99, cooldown_days=14))
    assert sorted(n for n in names(menu) if n) == ["a", "b"]
    assert names(menu).count(None) == 1


def test_a_recipe_can_return_once_the_cooldown_has_passed():
    menu = make_plan([recipe("a", [RICE])], days=4, settings=PlanSettings(99, cooldown_days=2))
    assert names(menu) == ["a", None, "a", None]


def test_recipes_served_recently_before_the_menu_start_are_still_cooling_down():
    recipes = [recipe("a", [RICE]), recipe("b", [BEANS])]
    served = {recipes[0].id: TODAY - timedelta(days=3)}
    menu = make_plan(
        recipes, days=1, last_served=served, settings=PlanSettings(99, cooldown_days=7)
    )
    assert names(menu) == ["b"]


def test_the_reason_mentions_the_cooldown_when_that_is_what_blocks():
    menu = make_plan([recipe("a", [RICE])], days=2, settings=PlanSettings(99, cooldown_days=14))
    assert "cooldown" in menu.meals[1].reason.lower()


# --- shopping allowance -----------------------------------------------------------------------


def test_recipes_needing_too_many_missing_ingredients_are_skipped():
    big = recipe("big", [ing(f"x{n}") for n in range(5)])
    small = recipe("small", [RICE])
    menu = make_plan(
        [big, small], days=2, settings=PlanSettings(shopping_allowance=2, cooldown_days=14)
    )
    assert names(menu) == ["small", None]


def test_ingredients_in_stock_do_not_count_against_the_allowance():
    items = [ing(f"y{n}") for n in range(5)]
    have = {i.id: None for i in items}  # in stock, no expiry
    menu = make_plan(
        [recipe("big", items)], days=1, have=have,
        settings=PlanSettings(shopping_allowance=0, cooldown_days=0),
    )  # fmt: skip
    assert names(menu) == ["big"]
    assert menu.shopping == ()


def test_the_allowance_is_shared_across_the_whole_menu():
    a = recipe("a", [ing("m1"), ing("m2")])
    b = recipe("b", [ing("m3"), ing("m4")])
    menu = make_plan([a, b], days=2, settings=PlanSettings(shopping_allowance=3, cooldown_days=14))
    assert names(menu).count(None) == 1


def test_an_ingredient_missing_for_two_recipes_is_counted_once():
    shared = ing("shared")
    a, b = recipe("a", [shared, RICE]), recipe("b", [shared, BEANS])
    menu = make_plan([a, b], days=2, settings=PlanSettings(shopping_allowance=3, cooldown_days=14))
    assert None not in names(menu)
    assert sorted(i.name for i in menu.shopping) == ["beans", "rice", "shared"]


def test_stock_that_expires_before_the_meal_does_not_count_as_in_stock():
    old = ing("old milk")
    have = {old.id: TODAY - timedelta(days=1)}
    menu = make_plan([recipe("a", [old])], days=1, have=have)
    assert [i.name for i in menu.shopping] == ["old milk"]


def test_the_reason_mentions_the_allowance_when_that_is_what_blocks():
    big = recipe("big", [ing(f"z{n}") for n in range(4)])
    menu = make_plan([big], days=1, settings=PlanSettings(shopping_allowance=1, cooldown_days=0))
    assert "shopping" in menu.meals[0].reason.lower()


# --- ranking ----------------------------------------------------------------------------------


def test_a_recipe_using_stock_beats_one_needing_shopping():
    stocked, unstocked = ing("stocked"), ing("unstocked")
    recipes = [recipe("shop", [unstocked]), recipe("use", [stocked])]
    menu = make_plan(recipes, days=1, have={stocked.id: None})
    assert names(menu) == ["use"]


def test_a_recipe_using_stock_that_expires_soon_beats_one_using_fresh_stock():
    soon, later = ing("spinach"), ing("tins")
    have = {soon.id: TODAY + timedelta(days=2), later.id: TODAY + timedelta(days=200)}
    recipes = [recipe("tins", [later]), recipe("spinach", [soon])]
    menu = make_plan(recipes, days=1, have=have)
    assert names(menu) == ["spinach"]


def test_the_recipe_closest_to_the_calorie_target_wins():
    # Sam's goal is 2000 kcal and dinner is the only meal, so the target is 2000.
    light = recipe("light", [ing("lettuce", 20)])
    right = recipe("right", [ing("feast", 200)], grams=1000)  # 2000 kcal
    heavy = recipe("heavy", [ing("lard", 900)], grams=1000)  # 9000 kcal
    menu = make_plan([light, right, heavy], days=1)
    assert names(menu) == ["right"]


def test_a_recipe_matching_the_weather_beats_one_for_the_other_weather():
    soup = recipe("soup", [RICE], weather=Weather.COLD)
    salad = recipe("salad", [BEANS], weather=Weather.WARM)
    menu = make_plan([soup, salad], days=1, weather={TODAY: Weather.COLD})
    assert names(menu) == ["soup"]
    menu = make_plan([soup, salad], days=1, weather={TODAY: Weather.WARM})
    assert names(menu) == ["salad"]


def test_a_dislike_makes_an_otherwise_equal_recipe_lose():
    mushrooms = ing("mushrooms")
    recipes = [recipe("mushroomy", [mushrooms]), recipe("plain", [RICE])]
    menu = make_plan(recipes, [eater(dislikes=("mushroom",))], days=1)
    assert names(menu) == ["plain"]


def test_weights_can_be_changed():
    stocked, unstocked = ing("stocked2"), ing("unstocked2", kcal=100)
    recipes = [recipe("use", [stocked], grams=10), recipe("fit", [unstocked], grams=2000)]
    # "fit" has 2000 kcal for dinner, the target; "use" is stocked but tiny.
    have = {stocked.id: None}
    calories = Weights(calorie_fit=10, in_stock=0.1, expiring=0, weather=0, soft=0)
    assert names(make_plan(recipes, days=1, have=have, settings=PlanSettings(99, 0, calories))) == [
        "fit"
    ]
    stock = Weights(calorie_fit=0.1, in_stock=10, expiring=0, weather=0, soft=0)
    assert names(make_plan(recipes, days=1, have=have, settings=PlanSettings(99, 0, stock))) == [
        "use"
    ]


def test_the_same_seed_gives_the_same_menu_and_a_different_seed_may_not():
    recipes = [recipe(f"r{n}", [ing(f"i{n}")]) for n in range(8)]
    one = names(make_plan(recipes, days=4, seed=7, settings=PlanSettings(99, 14)))
    again = names(make_plan(recipes, days=4, seed=7, settings=PlanSettings(99, 14)))
    assert one == again
    others = {
        tuple(names(make_plan(recipes, days=4, seed=s, settings=PlanSettings(99, 14))))
        for s in range(10)
    }
    assert len(others) > 1


# --- portions ---------------------------------------------------------------------------------


def test_portions_are_sized_to_each_eaters_calorie_goal():
    # 1 serving = 800 kcal. Dinner is the only meal, so the target is the whole goal.
    dish = recipe("dish", [ing("stuff", 80)], grams=1000)
    menu = make_plan([dish], [eater("Sam", 2000), eater("Lea", 1000)], days=1)
    sam, lea = menu.meals[0].portions
    assert (sam.eater.name, lea.eater.name) == ("Sam", "Lea")
    assert sam.servings == pytest.approx(2.5)
    assert sam.kcal == pytest.approx(2000)
    assert lea.servings == pytest.approx(1.25)


def test_the_day_is_split_between_the_chosen_meal_types():
    dish = recipe("dish", [ing("stuff2", 100)], meals=(L, D), grams=100)  # 100 kcal a serving
    menu = make_plan(
        [dish], [eater("Sam", 2000)], days=1, meals=(L, D), settings=PlanSettings(99, 0)
    )
    lunch, dinner = menu.meals
    # shares are lunch 30 and dinner 35, scaled to add up to the whole day
    assert lunch.portions[0].kcal == pytest.approx(2000 * 30 / 65, rel=0.01)
    assert dinner.portions[0].kcal == pytest.approx(2000 * 35 / 65, rel=0.01)


def test_unknown_calories_give_one_serving_each_and_no_calorie_figure():
    unknown = ing("mystery", kcal=None)
    menu = make_plan([recipe("m", [unknown])], [eater()], days=1)
    portion = menu.meals[0].portions[0]
    assert portion.servings == 1 and portion.kcal is None


def test_servings_are_rounded_to_quarters():
    dish = recipe("dish", [ing("stuff3", 90)], grams=1000)  # 900 kcal
    portion = make_plan([dish], [eater("Sam", 2000)], days=1).meals[0].portions[0]
    assert portion.servings == 2.25  # 2000 / 900 = 2.22
    assert portion.kcal == pytest.approx(2.25 * 900)


def test_an_empty_slot_has_no_portions():
    assert make_plan([], days=1).meals[0].portions == ()


# --- the shopping list ------------------------------------------------------------------------


def test_the_shopping_list_has_the_missing_ingredients_of_the_chosen_recipes_only():
    have = ing("have")
    a = recipe("a", [have, ing("need1")])
    menu = make_plan([a], days=1, have={have.id: None})
    assert [i.name for i in menu.shopping] == ["need1"]


def test_random_seed_object_is_not_shared_between_plans():
    recipes = [recipe(f"q{n}", [ing(f"qi{n}")]) for n in range(5)]
    random.seed(0)
    first = names(make_plan(recipes, days=3, seed=3, settings=PlanSettings(99, 14)))
    random.seed(1)
    second = names(make_plan(recipes, days=3, seed=3, settings=PlanSettings(99, 14)))
    assert first == second


def test_a_meal_says_how_many_servings_to_cook_in_total_and_how_that_scales_the_recipe():
    dish = recipe("dish", [ing("stuff4", 80)], servings=2, grams=1000)  # 400 kcal a serving
    menu = make_plan([dish], [eater("Sam", 2000), eater("Lea", 1000)], days=1)
    meal = menu.meals[0]
    assert meal.total_servings == 5 + 2.5
    assert meal.scale == pytest.approx(3.75)


def test_an_empty_meal_has_nothing_to_cook():
    meal = make_plan([], days=1).meals[0]
    assert meal.total_servings == 0 and meal.scale == 0


# --- snacks -----------------------------------------------------------------------------------

ALL_SLOTS = list(Slot)
SNACK = MealType.SNACK


def test_a_day_has_six_possible_slots_in_the_order_they_are_eaten():
    assert [s.value for s in Slot] == [
        "breakfast", "morning-snack", "lunch", "afternoon-snack", "dinner", "evening-snack"
    ]  # fmt: skip
    assert [s.label for s in Slot] == [
        "Breakfast", "Morning snack", "Lunch", "Afternoon snack", "Dinner", "Evening snack"
    ]  # fmt: skip


def test_the_snack_slots_use_snack_recipes_and_main_slots_do_not():
    assert Slot.MORNING_SNACK.meal_type is SNACK
    assert Slot.AFTERNOON_SNACK.meal_type is SNACK
    assert Slot.EVENING_SNACK.meal_type is SNACK
    assert Slot.LUNCH.meal_type is L


def test_slots_are_planned_in_day_order_whatever_order_they_are_given():
    recipes = [recipe(f"s{n}", [ing(f"sn{n}")], meals=(SNACK, B, L, D)) for n in range(8)]
    menu = make_plan(
        recipes,
        days=1,
        meals=[Slot.EVENING_SNACK, Slot.BREAKFAST, Slot.MORNING_SNACK],
        settings=PlanSettings(99, 0),
    )
    assert [m.slot for m in menu.meals] == [Slot.BREAKFAST, Slot.MORNING_SNACK, Slot.EVENING_SNACK]


def test_snack_recipes_fill_snack_slots_and_never_main_meals():
    apple = recipe("apple", [ing("apple")], meals=(SNACK,))
    stew = recipe("stew", [ing("stew stuff")], meals=(D,))
    menu = make_plan(
        [apple, stew], days=1, meals=[Slot.DINNER, Slot.EVENING_SNACK], settings=PlanSettings(99, 0)
    )
    assert [(m.slot, m.recipe.name) for m in menu.meals] == [
        (Slot.DINNER, "stew"), (Slot.EVENING_SNACK, "apple")
    ]  # fmt: skip


def test_three_snacks_in_a_day_are_three_different_recipes_when_there_is_a_cooldown():
    snacks = [recipe(f"snack{n}", [ing(f"snackstuff{n}")], meals=(SNACK,)) for n in range(3)]
    slots = [Slot.MORNING_SNACK, Slot.AFTERNOON_SNACK, Slot.EVENING_SNACK]
    menu = make_plan(snacks, days=1, meals=slots, settings=PlanSettings(99, cooldown_days=7))
    assert sorted(m.recipe.name for m in menu.meals) == ["snack0", "snack1", "snack2"]


def test_with_one_snack_recipe_and_a_cooldown_only_the_first_snack_slot_gets_it():
    only = recipe("only snack", [ing("lone snack")], meals=(SNACK,))
    slots = [Slot.MORNING_SNACK, Slot.AFTERNOON_SNACK]
    menu = make_plan([only], days=1, meals=slots, settings=PlanSettings(99, cooldown_days=7))
    assert [m.recipe.name if m.recipe else None for m in menu.meals] == ["only snack", None]
    assert "cooldown" in menu.meals[1].reason.lower()


def test_a_snack_slot_is_a_smaller_share_of_the_day_than_a_main_meal():
    dish = recipe("both", [ing("both stuff", 100)], meals=(L, SNACK), grams=100)  # 100 kcal
    menu = make_plan(
        [dish],
        [eater("Sam", 2000)],
        days=1,
        meals=[Slot.LUNCH, Slot.AFTERNOON_SNACK],
        settings=PlanSettings(99, 0),
    )
    lunch, snack = menu.meals
    assert snack.portions[0].kcal < lunch.portions[0].kcal / 2


def test_all_six_slots_share_out_exactly_the_whole_day():
    dish = recipe(
        "everything", [ing("all stuff", 100)], meals=(B, L, D, SNACK), grams=10
    )  # 10 kcal
    menu = make_plan(
        [dish], [eater("Sam", 2000)], days=1, meals=ALL_SLOTS, settings=PlanSettings(99, 0)
    )
    total = sum(m.portions[0].kcal for m in menu.meals)
    assert total == pytest.approx(2000, rel=0.02)
