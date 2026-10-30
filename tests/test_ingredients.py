import pytest

from emu_the_menu.allergens import Allergen
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.ingredients import (
    NEEDS_LOOK,
    PLAIN,
    calories_in,
    judge,
    kcal_basis,
    parse_kcal,
    suggest_tags,
)
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
        ("oat milk", set(), {Allergen.GLUTEN}),
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


# --- plain-looking ingredients -------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "salt", "black pepper", "freshly ground black pepper", "sea salt and black pepper",
        "garlic", "onion", "red onion", "carrots", "cherry tomatoes", "diced tomatoes",
        "fresh basil leaves", "chopped fresh cilantro", "lemon juice", "olive oil",
        "extra virgin olive oil", "water", "ice water", "white sugar", "brown sugar",
        "red bell pepper", "baby spinach", "frozen peas", "chickpeas", "brown or green lentils",
        "black beans", "cucumber", "sweet potatoes", "dried oregano", "ground cumin", "kale",
    ],
)  # fmt: skip
def test_a_single_unprocessed_plant_food_looks_plain(name):
    assert judge(name).kind == PLAIN


@pytest.mark.parametrize(
    "name",
    [
        "chicken breast", "eggs", "milk", "honey", "butter", "cheddar cheese",
        "soy sauce", "tomato sauce", "vegetable stock", "chicken stock / broth",
        "all-purpose flour", "pasta shells", "rolled oats", "bread", "peanut butter",
        "sesame oil", "sesame seeds", "mustard", "dijon mustard", "worcestershire sauce",
        "taco seasoning", "baking powder", "curry powder", "mayonnaise", "tortilla chips",
        "celery", "walnuts", "tofu", "maple syrup", "hot sauce", "fish sauce", "granola",
        "something unheard of", "",
    ],
)  # fmt: skip
def test_anything_else_needs_a_look(name):
    assert judge(name).kind == NEEDS_LOOK


def test_a_name_that_hints_at_a_tag_says_so():
    reason = judge("chicken breast").reason
    assert "poultry" in reason.lower()
    assert "milk" in judge("whole milk").reason.lower()


def test_a_processed_food_says_hidden_allergens_are_possible():
    for name in ("vegetable stock", "taco seasoning", "hot sauce", "granola"):
        assert "label" in judge(name).reason.lower(), name


def test_an_unrecognised_name_says_so():
    assert "not recognised" in judge("something unheard of").reason.lower()


def test_one_unknown_word_is_enough_to_need_a_look():
    assert judge("garlic and zhoug").kind == NEEDS_LOOK
    assert judge("garlic").kind == PLAIN


def test_a_plain_food_has_no_suggestions_and_a_hint_is_never_plain():
    for name in ("garlic", "salt", "olive oil", "kale"):
        s = suggest_tags(name)
        assert s.food_groups == () and s.allergens == (), name
    assert judge("peanut oil").kind == NEEDS_LOOK


def test_a_bare_oil_could_be_anything_so_it_needs_a_look():
    assert judge("oil").kind == NEEDS_LOOK
    assert "peanut or sesame" in judge("oil").reason
    assert judge("olive oil").kind == PLAIN
    assert judge("vegetable oil").kind == PLAIN


# --- more of what a recipe site calls things ------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "long grain rice", "jasmine rice", "steamed jasmine rice", "uncooked quinoa",
        "strawberries", "raspberries", "dates", "limes", "eating apple", "banana slices",
        "cannellini beans", "kidney beans", "jalapeño", "green chiles", "fresno chiles",
        "iceberg lettuce", "bean sprouts", "choy sum", "bok choys", "collard greens or kale",
        "maris piper potatoes", "lemon wedges", "pumpkin seeds", "roasted sunflower seeds",
        "chia seeds", "flaxseed meal", "pitted kalamata olives", "bundle rainbow chard",
        "or 2 med. yellow heirloom tomatoes", "cooked jasmine rice", "green beans or haricot verts",
    ],
)  # fmt: skip
def test_more_plain_foods_look_plain(name):
    assert judge(name).kind == PLAIN, name


@pytest.mark.parametrize(
    "name, groups, allergens",
    [
        ("all-purpose flour", [], ["gluten"]),
        ("plain flour", [], ["gluten"]),
        ("rolled oats", [], ["gluten"]),
        ("old fashioned oats", [], ["gluten"]),
        ("granola", [], ["gluten"]),
        ("flour tortillas", [], ["gluten"]),
        ("red wine vinegar", [], ["sulphites"]),
        ("dry white wine", [], ["sulphites"]),
        ("balsamic vinegar", [], ["sulphites"]),
        ("worcestershire sauce", ["fish"], ["fish"]),
        ("mayonnaise", ["eggs"], ["eggs"]),
        ("vegetable broth", [], ["celery"]),
        ("bouillon powder", [], ["celery"]),
        ("hummus", [], ["sesame"]),
    ],
)  # fmt: skip
def test_more_typical_allergens_are_suggested(name, groups, allergens):
    hint = suggest_tags(name)
    assert [g.value for g in hint.food_groups] == groups
    assert [a.value for a in hint.allergens] == allergens


@pytest.mark.parametrize(
    "name",
    [
        "cornflour / corn starch", "rice flour", "almond flour", "coconut flour",
        "gluten-free rolled oats", "vegan mayonnaise", "corn tortillas", "tomato sauce",
        "ketchup", "taco seasoning",
    ],
)  # fmt: skip
def test_these_do_not_get_a_gluten_or_egg_suggestion(name):
    hint = suggest_tags(name)
    assert Allergen.GLUTEN not in hint.allergens, name
    assert Allergen.EGGS not in hint.allergens, name


def test_a_flour_that_is_not_wheat_still_gets_its_own_nut_suggestion():
    assert Allergen.TREE_NUTS in suggest_tags("almond flour").allergens


def test_accents_do_not_break_the_words():
    assert judge("jalapeño").kind == PLAIN
    assert judge("crème fraîche").kind == NEEDS_LOOK
    assert Allergen.MILK in suggest_tags("crème fraîche cream").allergens


@pytest.mark.parametrize(
    "name",
    [
        "chickpeas (counted)", "onion (by weight)", "tomatoes (counted)", "cucumber (by volume)",
        "bunch lacinato kale", "flat-leaf parsley", "thai basil leaves", "black beluga lentils",
        "frozen corn kernels", "reserved corn kernels", "corn on the cob", "caraway seeds",
        "smoked paprika", "birds eye or thai chilli", "herbs", "fruit", "caster sugar",
        "baking soda", "baking soda / bi-carb",
    ],
)  # fmt: skip
def test_brackets_and_more_everyday_words_do_not_stop_a_plain_food_looking_plain(name):
    assert judge(name).kind == PLAIN, name


@pytest.mark.parametrize(
    "name",
    ["baking powder", "chili powder", "curry powder", "vegan protein powder", "oyster sauce"],
)
def test_blends_and_powders_still_need_a_look(name):
    assert judge(name).kind == NEEDS_LOOK


@pytest.mark.parametrize(
    "name, allergen",
    [
        ("fettuccine", "gluten"),
        ("dried fettuccine", "gluten"),
        ("linguine", "gluten"),
        ("parmigiano reggiano", "milk"),
        ("halloumi", "milk"),
        ("pecorino", "milk"),
    ],
)
def test_pasta_shapes_and_cheeses_are_recognised(name, allergen):
    assert allergen in [a.value for a in suggest_tags(name).allergens]
