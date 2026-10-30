import pytest

from emu_the_menu.allergens import Allergen
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.ingredients import calories_in, kcal_basis, parse_kcal, suggest_tags
from emu_the_menu.units import BaseUnit

# --- calories ------------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "label"),
    [
        (BaseUnit.GRAM, "per 100 g"),
        (BaseUnit.MILLILITRE, "per 100 ml"),
        (BaseUnit.COUNT, "per piece"),
    ],
)
def test_calories_are_given_per_100_g_per_100_ml_or_per_piece(base, label):
    assert kcal_basis(base) == label


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", None), ("   ", None), ("539", 539.0), (" 53.5 ", 53.5), ("53,5", 53.5), ("0", 0.0)],
)
def test_parse_kcal_reads_a_number_with_a_dot_or_a_comma_or_nothing(text, expected):
    assert parse_kcal(text, BaseUnit.GRAM) == expected


@pytest.mark.parametrize("text", ["abc", "-5", "1e3", "5 kcal", "1.2.3", "∞"])
def test_parse_kcal_rejects_what_is_not_a_plain_number(text):
    with pytest.raises(ValueError, match="calories"):
        parse_kcal(text, BaseUnit.GRAM)


@pytest.mark.parametrize(
    ("base", "ok", "too_much"),
    [
        (BaseUnit.GRAM, "950", "951"),
        (BaseUnit.MILLILITRE, "950", "951"),
        (BaseUnit.COUNT, "3000", "3001"),
    ],
)
def test_parse_kcal_has_a_ceiling_that_suits_the_unit(base, ok, too_much):
    assert parse_kcal(ok, base) == float(ok)
    with pytest.raises(ValueError, match="calories"):
        parse_kcal(too_much, base)


@pytest.mark.parametrize(
    ("quantity", "base", "kcal", "expected"),
    [
        (250, BaseUnit.GRAM, 539, 1347.5),
        (2000, BaseUnit.MILLILITRE, 42, 840.0),
        (3, BaseUnit.COUNT, 70, 210.0),
        (0, BaseUnit.GRAM, 539, 0.0),
    ],
)
def test_calories_in_an_amount(quantity, base, kcal, expected):
    assert calories_in(quantity, base, kcal) == pytest.approx(expected)


def test_calories_in_an_amount_are_unknown_when_the_ingredient_has_none():
    assert calories_in(250, BaseUnit.GRAM, None) is None


# --- suggestions from the name ------------------------------------------------------------


def tags(name):
    suggestion = suggest_tags(name)
    return set(suggestion.food_groups), set(suggestion.allergens)


@pytest.mark.parametrize(
    ("name", "groups", "allergens"),
    [
        ("chicken breast", {FoodGroup.POULTRY}, set()),
        ("beef mince", {FoodGroup.MEAT}, set()),
        ("Smoked BACON", {FoodGroup.MEAT}, set()),
        ("salmon fillet", {FoodGroup.FISH}, {Allergen.FISH}),
        ("prawns", {FoodGroup.FISH}, {Allergen.CRUSTACEANS}),
        ("mussels", {FoodGroup.FISH}, {Allergen.MOLLUSCS}),
        ("whole milk", {FoodGroup.DAIRY}, {Allergen.MILK}),
        ("cheddar cheese", {FoodGroup.DAIRY}, {Allergen.MILK}),
        ("greek yoghurt", {FoodGroup.DAIRY}, {Allergen.MILK}),
        ("butter", {FoodGroup.DAIRY}, {Allergen.MILK}),
        ("eggs", {FoodGroup.EGGS}, {Allergen.EGGS}),
        ("honey", {FoodGroup.HONEY}, set()),
        ("spaghetti", set(), {Allergen.GLUTEN}),
        ("wholemeal bread", set(), {Allergen.GLUTEN}),
        ("peanuts", set(), {Allergen.PEANUTS}),
        ("almonds", set(), {Allergen.TREE_NUTS}),
        ("tahini", set(), {Allergen.SESAME}),
        ("tofu", set(), {Allergen.SOY}),
        ("celery", set(), {Allergen.CELERY}),
        ("dijon mustard", set(), {Allergen.MUSTARD}),
    ],
)
def test_common_names_suggest_their_food_groups_and_allergens(name, groups, allergens):
    assert tags(name) == (groups, allergens)


@pytest.mark.parametrize(
    ("name", "groups", "allergens"),
    [
        ("coconut milk", set(), set()),  # not dairy
        ("almond milk", set(), {Allergen.TREE_NUTS}),  # a nut, not milk
        ("oat milk", set(), set()),
        ("soy milk", set(), {Allergen.SOY}),
        ("peanut butter", set(), {Allergen.PEANUTS}),  # not dairy
        ("cocoa butter", set(), set()),
        ("eggplant", set(), set()),  # not an egg
    ],
)
def test_plant_milks_nut_butters_and_eggplant_are_not_mistaken_for_dairy_or_eggs(
    name, groups, allergens
):
    assert tags(name) == (groups, allergens)


def test_several_words_can_each_add_a_tag():
    assert tags("prawn and chicken stir fry sauce") == (
        {FoodGroup.POULTRY, FoodGroup.FISH},
        {Allergen.CRUSTACEANS},
    )


def test_plurals_and_capitals_do_not_matter():
    assert tags("PRAWN") == tags("prawns") == tags("Prawns")


def test_whole_words_only():
    assert tags("hamper") == (set(), set())  # not "ham"
    assert tags("sesamex") == (set(), set())


def test_an_unknown_name_suggests_nothing():
    assert tags("xyzzy") == (set(), set())
    assert suggest_tags("").food_groups == ()


def test_suggestions_come_in_the_standard_order():
    suggestion = suggest_tags("honey, milk, chicken and salmon")
    assert suggestion.food_groups == (
        FoodGroup.POULTRY,
        FoodGroup.FISH,
        FoodGroup.DAIRY,
        FoodGroup.HONEY,
    )
    assert suggestion.allergens == (Allergen.FISH, Allergen.MILK)
