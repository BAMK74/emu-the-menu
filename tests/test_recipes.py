import pytest

from emu_the_menu.allergens import Allergen
from emu_the_menu.eaters import Diet
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.recipes import (
    MealType,
    Weather,
    analyse,
    clean_recipe_name,
    parse_meal_types,
    parse_servings,
    parse_tags,
    parse_weather,
)
from emu_the_menu.repository import Ingredient
from emu_the_menu.units import BaseUnit


def ingredient(
    name="rice",
    unit=BaseUnit.GRAM,
    kcal=360.0,
    groups=(),
    allergens=(),
    reviewed=True,
    ingredient_id=1,
):
    return Ingredient(
        name,
        unit,
        id=ingredient_id,
        kcal=kcal,
        food_groups=tuple(groups),
        allergens=tuple(allergens),
        reviewed=reviewed,
    )


# --- the small fields -------------------------------------------------------------------------


def test_a_name_is_trimmed_and_spaces_are_collapsed():
    assert clean_recipe_name("  Tomato   soup ") == "Tomato soup"


@pytest.mark.parametrize("bad", ["", "   ", "x" * 81])
def test_a_blank_or_very_long_name_is_rejected(bad):
    with pytest.raises(ValueError):
        clean_recipe_name(bad)


@pytest.mark.parametrize("text, expected", [("4", 4), (" 1 ", 1), ("50", 50)])
def test_servings_are_a_whole_number_from_1_to_50(text, expected):
    assert parse_servings(text) == expected


@pytest.mark.parametrize("bad", ["", "0", "51", "-2", "2.5", "two"])
def test_other_servings_are_rejected(bad):
    with pytest.raises(ValueError, match="[Ss]ervings"):
        parse_servings(bad)


def test_meal_types_keep_the_standard_order_and_drop_repeats():
    assert parse_meal_types(["dinner", "breakfast", "dinner"]) == (
        MealType.BREAKFAST,
        MealType.DINNER,
    )


def test_a_recipe_needs_at_least_one_meal_type():
    with pytest.raises(ValueError, match="meal type"):
        parse_meal_types([])


def test_an_unknown_meal_type_is_rejected():
    with pytest.raises(ValueError, match="meal type"):
        parse_meal_types(["brunch"])


@pytest.mark.parametrize(
    "text, expected",
    [("", Weather.ANY), ("any", Weather.ANY), ("warm", Weather.WARM), ("cold", Weather.COLD)],
)
def test_weather_defaults_to_any(text, expected):
    assert parse_weather(text) is expected


def test_unknown_weather_is_rejected():
    with pytest.raises(ValueError, match="[Ww]eather"):
        parse_weather("rainy")


def test_tags_split_on_commas_semicolons_and_lines_and_are_lowercased():
    assert parse_tags("Festive, Quick; quick\nKids") == ("festive", "quick", "kids")


def test_blank_tags_are_fine():
    assert parse_tags("  ") == ()


def test_too_many_or_too_long_tags_are_rejected():
    with pytest.raises(ValueError, match="tags"):
        parse_tags(",".join(f"t{n}" for n in range(11)))
    with pytest.raises(ValueError, match="tags"):
        parse_tags("x" * 31)


# --- calories ---------------------------------------------------------------------------------


def test_calories_per_serving_add_up_the_lines_and_divide_by_servings():
    rice = ingredient("rice", kcal=360)  # per 100 g
    oil = ingredient("oil", BaseUnit.MILLILITRE, kcal=800, ingredient_id=2)
    egg = ingredient("egg", BaseUnit.COUNT, kcal=70, ingredient_id=3)
    facts = analyse([(rice, 200), (oil, 10), (egg, 2)], servings=2)
    assert facts.total_calories == 720 + 80 + 140
    assert facts.calories_per_serving == 470


def test_calories_are_unknown_when_any_ingredient_lacks_them():
    facts = analyse([(ingredient("rice"), 200), (ingredient("salt", kcal=None), 5)], servings=1)
    assert facts.calories_per_serving is None
    assert facts.total_calories is None
    assert facts.without_calories == ("salt",)


# --- food groups, allergens and diets ---------------------------------------------------------


def test_food_groups_and_allergens_are_the_union_in_standard_order():
    a = ingredient("cheese", groups=[FoodGroup.DAIRY], allergens=[Allergen.MILK])
    b = ingredient("pasta", allergens=[Allergen.GLUTEN], ingredient_id=2)
    facts = analyse([(a, 50), (b, 100)], servings=1)
    assert facts.food_groups == (FoodGroup.DAIRY,)
    assert set(facts.allergens) == {Allergen.GLUTEN, Allergen.MILK}
    assert facts.allergens == tuple(x for x in Allergen if x in facts.allergens)


def test_a_vegan_recipe_fits_every_stricter_diet_down_to_vegan():
    facts = analyse([(ingredient("rice"), 100)], servings=1)
    assert facts.diets == (Diet.PESCATARIAN, Diet.VEGETARIAN, Diet.VEGAN)


def test_dairy_makes_a_recipe_vegetarian_but_not_vegan():
    cheese = ingredient("cheese", groups=[FoodGroup.DAIRY])
    assert analyse([(cheese, 50)], servings=1).diets == (Diet.PESCATARIAN, Diet.VEGETARIAN)


def test_fish_makes_a_recipe_pescatarian_only():
    fish = ingredient("salmon", groups=[FoodGroup.FISH])
    assert analyse([(fish, 150)], servings=1).diets == (Diet.PESCATARIAN,)


def test_meat_fits_none_of_the_diets():
    meat = ingredient("beef", groups=[FoodGroup.MEAT])
    assert analyse([(meat, 150)], servings=1).diets == ()


# --- the safety rule: unchecked is never safe -------------------------------------------------


def test_an_unchecked_ingredient_makes_the_recipe_unchecked_with_no_diet_badges():
    mystery = ingredient("sauce", reviewed=False, ingredient_id=2)
    facts = analyse([(ingredient("rice"), 100), (mystery, 20)], servings=1)
    assert facts.unchecked == ("sauce",)
    assert facts.is_checked is False
    assert facts.diets == ()


def test_a_recipe_of_checked_ingredients_is_checked_even_with_no_allergens():
    facts = analyse([(ingredient("rice"), 100)], servings=1)
    assert facts.is_checked is True
    assert facts.allergens == ()


def test_known_tags_of_unchecked_ingredients_are_not_trusted_as_complete():
    # Tags on an unreviewed ingredient can only be empty, but even so the recipe is not "safe".
    facts = analyse([(ingredient("sauce", reviewed=False), 20)], servings=1)
    assert facts.is_checked is False
