import itertools

import pytest

from emu_the_menu.allergens import Allergen
from emu_the_menu.eaters import Category, Diet, Eater
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.recipe_repository import Recipe, RecipeLine
from emu_the_menu.recipes import MealType, Weather, analyse
from emu_the_menu.repository import Ingredient
from emu_the_menu.suitability import assess
from emu_the_menu.units import BaseUnit

_ids = itertools.count(1)


def ing(name, groups=(), allergens=(), reviewed=True):
    return Ingredient(
        name,
        BaseUnit.GRAM,
        id=next(_ids),
        kcal=100,
        food_groups=tuple(groups),
        allergens=tuple(allergens),
        reviewed=reviewed,
    )


def recipe(*ingredients):
    lines = tuple(RecipeLine(i, 100) for i in ingredients)
    return Recipe(1, "Dish", 2, (MealType.DINNER,), Weather.ANY, (), lines)


def check(recipe, **eater):
    facts = analyse([(line.ingredient, line.quantity) for line in recipe.lines], recipe.servings)
    return assess(recipe, facts, Eater(1, "Sam", Category.MAN, **eater))


RICE = ing("rice")
BEEF = ing("beef", [FoodGroup.MEAT])
CHEESE = ing("cheese", [FoodGroup.DAIRY], [Allergen.MILK])
SAUCE = ing("mystery sauce", reviewed=False)

# --- an eater with no restrictions -------------------------------------------------------------


def test_an_unrestricted_eater_can_have_anything_checked():
    result = check(recipe(RICE, BEEF, CHEESE))
    assert result.ok and result.blocked == () and result.cautions == ()


def test_an_unrestricted_eater_gets_a_caution_for_unchecked_ingredients_not_a_block():
    result = check(recipe(RICE, SAUCE))
    assert result.ok
    assert any("mystery sauce" in c and "not checked" in c.lower() for c in result.cautions)


# --- ticked allergens ---------------------------------------------------------------------------


def test_a_ticked_allergen_in_the_recipe_blocks_it_and_names_the_allergen():
    result = check(recipe(RICE, CHEESE), allergens=(Allergen.MILK,))
    assert not result.ok
    assert any("milk" in reason.lower() for reason in result.blocked)


def test_an_allergen_the_recipe_lacks_does_not_block():
    assert check(recipe(RICE, BEEF), allergens=(Allergen.MILK,)).ok


def test_one_of_several_allergens_is_enough_to_block():
    result = check(recipe(CHEESE), allergens=(Allergen.PEANUTS, Allergen.MILK))
    assert not result.ok


def test_an_unchecked_ingredient_blocks_the_recipe_for_someone_with_an_allergen():
    result = check(recipe(RICE, SAUCE), allergens=(Allergen.MILK,))
    assert not result.ok
    assert any(
        "mystery sauce" in reason and "not checked" in reason.lower() for reason in result.blocked
    )


def test_an_unchecked_ingredient_blocks_the_recipe_for_someone_with_another_allergy_too():
    assert not check(recipe(RICE, SAUCE), allergies=("kiwi",)).ok


# --- free-text allergies ------------------------------------------------------------------------


def test_an_allergy_word_in_an_ingredient_name_blocks_the_recipe():
    result = check(recipe(RICE, ing("kiwi fruit")), allergies=("kiwi",))
    assert not result.ok
    assert any("kiwi" in reason for reason in result.blocked)


@pytest.mark.parametrize(
    "term, name",
    [
        ("kiwi", "kiwis"),
        ("peanut", "peanut butter"),
        ("almonds", "almond flour"),
        ("Shrimp", "dried shrimp"),
    ],
)
def test_allergy_matching_ignores_plurals_and_case_and_matches_whole_words(term, name):
    assert not check(recipe(ing(name)), allergies=(term.lower(),)).ok


def test_an_allergy_word_that_only_appears_inside_another_word_does_not_block():
    assert check(recipe(ing("pineapple")), allergies=("pine",)).ok


def test_a_free_text_allergy_with_no_match_still_asks_for_the_labels_to_be_read():
    result = check(recipe(RICE), allergies=("kiwi",))
    assert result.ok
    assert any("kiwi" in c and "label" in c for c in result.cautions)


# --- diets --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "diet, blocked_by",
    [
        (Diet.PESCATARIAN, ing("chicken", [FoodGroup.POULTRY])),
        (Diet.VEGETARIAN, ing("tuna", [FoodGroup.FISH])),
        (Diet.VEGAN, ing("honey", [FoodGroup.HONEY])),
        (Diet.VEGAN, ing("feta", [FoodGroup.DAIRY])),
        (Diet.VEGAN, ing("egg", [FoodGroup.EGGS])),
    ],
)
def test_a_diet_blocks_the_food_groups_it_excludes(diet, blocked_by):
    result = check(recipe(RICE, blocked_by), diet=diet)
    assert not result.ok
    assert any(blocked_by.name in reason for reason in result.blocked)


@pytest.mark.parametrize("diet", [Diet.PESCATARIAN, Diet.VEGETARIAN, Diet.VEGAN])
def test_a_plant_recipe_suits_every_diet(diet):
    assert check(recipe(RICE), diet=diet).ok


def test_an_unchecked_ingredient_blocks_a_recipe_for_a_diet_that_bans_food_groups():
    result = check(recipe(RICE, SAUCE), diet=Diet.VEGETARIAN)
    assert not result.ok
    assert any("mystery sauce" in reason for reason in result.blocked)


def test_a_known_banned_group_is_the_reason_even_when_something_is_unchecked_too():
    result = check(recipe(BEEF, SAUCE), diet=Diet.VEGETARIAN)
    assert any("meat" in reason.lower() for reason in result.blocked)


def test_low_meat_never_blocks_but_notes_meat_as_a_soft_point():
    result = check(recipe(RICE, BEEF), diet=Diet.LOW_MEAT)
    assert result.ok
    assert any("meat" in s.lower() for s in result.soft)


def test_low_meat_has_no_note_for_a_meatless_recipe():
    assert check(recipe(RICE), diet=Diet.LOW_MEAT).soft == ()


# --- dislikes are soft --------------------------------------------------------------------------


def test_a_dislike_never_blocks_but_is_noted():
    result = check(recipe(RICE, ing("mushrooms")), dislikes=("mushroom",))
    assert result.ok
    assert any("mushroom" in s for s in result.soft)


def test_no_dislike_note_when_none_match():
    assert check(recipe(RICE), dislikes=("mushroom",)).soft == ()


# --- the reasons say who and what ---------------------------------------------------------------


def test_every_reason_is_plain_text_a_person_can_read():
    result = check(recipe(CHEESE), allergens=(Allergen.MILK,), diet=Diet.VEGAN)
    assert result.blocked
    assert all(isinstance(reason, str) and reason == reason.strip() for reason in result.blocked)
    assert len(set(result.blocked)) == len(result.blocked)
