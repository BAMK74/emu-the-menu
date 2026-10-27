import pytest

from emu_the_menu.eaters import (
    CALORIE_ADJUSTMENTS,
    DEFAULT_CALORIE_GOALS,
    Category,
    Diet,
    Eater,
    FoodGroup,
    clean_name,
    parse_calorie_adjustment,
    parse_calorie_goal,
    parse_diet,
    parse_terms,
)


def test_every_category_has_a_built_in_default_calorie_goal():
    assert DEFAULT_CALORIE_GOALS == {
        Category.MAN: 2500,
        Category.WOMAN: 2000,
        Category.TEEN: 2200,
        Category.CHILD: 1600,
    }
    assert set(DEFAULT_CALORIE_GOALS) == set(Category)


def test_the_adjustment_choices_are_small_steps_that_grow_with_distance():
    assert 0 in CALORIE_ADJUSTMENTS
    assert list(CALORIE_ADJUSTMENTS) == sorted(CALORIE_ADJUSTMENTS)
    assert list(CALORIE_ADJUSTMENTS) == [-a for a in reversed(CALORIE_ADJUSTMENTS)]
    assert all(a % 50 == 0 for a in CALORIE_ADJUSTMENTS)
    steps = [b - a for a, b in zip(CALORIE_ADJUSTMENTS, CALORIE_ADJUSTMENTS[1:], strict=False)]
    assert max(steps) <= 250
    # Up to 500 either way it moves in steps of 100.
    near = [a for a in CALORIE_ADJUSTMENTS if abs(a) <= 500]
    assert near == [-500, -400, -300, -200, -100, 0, 100, 200, 300, 400, 500]


def test_an_eater_uses_the_category_default_plus_their_adjustment():
    assert Eater(1, "Sam", Category.WOMAN).calorie_goal == 2000
    assert Eater(1, "Sam", Category.WOMAN, calorie_adjustment=200).calorie_goal == 2200
    assert Eater(1, "Sam", Category.WOMAN, calorie_adjustment=-300).calorie_goal == 1700


def test_a_changed_category_default_carries_through_to_the_goal():
    eater = Eater(1, "Sam", Category.WOMAN, calorie_adjustment=100, default_goal=2100)
    assert eater.default_calorie_goal == 2100
    assert eater.calorie_goal == 2200


def test_only_a_non_zero_adjustment_counts_as_custom():
    assert Eater(1, "Sam", Category.MAN).has_custom_goal is False
    assert Eater(1, "Sam", Category.MAN, calorie_adjustment=-100).has_custom_goal is True


def test_category_values_are_what_the_forms_send():
    assert [c.value for c in Category] == ["man", "woman", "teen", "child"]
    assert Category("teen") is Category.TEEN


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("peanuts, shellfish", ("peanuts", "shellfish")),
        ("Peanuts;Shellfish\nMilk", ("peanuts", "shellfish", "milk")),
        ("  tree   nuts , eggs ", ("tree nuts", "eggs")),
        ("peanuts, PEANUTS, peanuts", ("peanuts",)),
        ("a,, ,b,", ("a", "b")),
        ("", ()),
        ("   ", ()),
    ],
)
def test_parse_terms_cleans_a_typed_list(text, expected):
    assert parse_terms(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"), [("", None), ("  ", None), ("1800", 1800), (" 2500 ", 2500)]
)
def test_parse_calorie_goal_reads_a_number_or_nothing(text, expected):
    assert parse_calorie_goal(text) == expected


@pytest.mark.parametrize("text", ["abc", "1800.5", "1,800", "-2000", "499", "6001", "0"])
def test_parse_calorie_goal_rejects_nonsense(text):
    with pytest.raises(ValueError, match="calorie"):
        parse_calorie_goal(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("0", 0),
        ("200", 200),
        ("+200", 200),
        ("-200", -200),
        (" 750 ", 750),
        ("-1000", -1000),
    ],
)
def test_parse_calorie_adjustment_reads_one_of_the_choices(text, expected):
    assert parse_calorie_adjustment(text) == expected


@pytest.mark.parametrize("text", ["150", "abc", "2000", "-50", "1.5", "+-100"])
def test_parse_calorie_adjustment_rejects_anything_not_in_the_list(text):
    with pytest.raises(ValueError, match="adjustment"):
        parse_calorie_adjustment(text)


def test_clean_name_trims_and_collapses_spaces():
    assert clean_name("  Mary   Ann ") == "Mary Ann"


@pytest.mark.parametrize("name", ["", "   ", "x" * 41])
def test_clean_name_rejects_blank_or_very_long_names(name):
    with pytest.raises(ValueError, match="name"):
        clean_name(name)


# --- diets ----------------------------------------------------------------------------------


def test_the_diets_are_the_ones_the_forms_send():
    assert [d.value for d in Diet] == [
        "omnivore",
        "low-meat",
        "pescatarian",
        "vegetarian",
        "vegan",
    ]


def test_every_diet_has_a_label_and_a_description():
    for diet in Diet:
        assert diet.label and diet.description
    assert Diet.OMNIVORE.label == "Eats everything"
    assert Diet.PESCATARIAN.label == "Pescatarian"


@pytest.mark.parametrize(
    ("diet", "excluded"),
    [
        (Diet.OMNIVORE, set()),
        (Diet.LOW_MEAT, set()),  # a preference to limit meat, not a ban
        (Diet.PESCATARIAN, {FoodGroup.MEAT, FoodGroup.POULTRY}),
        (Diet.VEGETARIAN, {FoodGroup.MEAT, FoodGroup.POULTRY, FoodGroup.FISH}),
        (
            Diet.VEGAN,
            {
                FoodGroup.MEAT,
                FoodGroup.POULTRY,
                FoodGroup.FISH,
                FoodGroup.DAIRY,
                FoodGroup.EGGS,
                FoodGroup.HONEY,
            },
        ),
    ],
)
def test_strict_diets_exclude_whole_food_groups(diet, excluded):
    assert diet.excluded_groups == frozenset(excluded)


def test_each_stricter_diet_excludes_everything_the_looser_one_does():
    chain = [Diet.OMNIVORE, Diet.PESCATARIAN, Diet.VEGETARIAN, Diet.VEGAN]
    for looser, stricter in zip(chain, chain[1:], strict=False):
        assert looser.excluded_groups < stricter.excluded_groups


def test_only_low_meat_is_a_soft_limit_on_meat():
    assert [d for d in Diet if d.limits_meat] == [Diet.LOW_MEAT]
    assert FoodGroup.MEAT not in Diet.LOW_MEAT.excluded_groups


def test_an_eater_defaults_to_eating_everything():
    assert Eater(1, "Sam", Category.MAN).diet is Diet.OMNIVORE


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", Diet.OMNIVORE),
        ("  ", Diet.OMNIVORE),
        ("vegan", Diet.VEGAN),
        (" Low-Meat ", Diet.LOW_MEAT),
        ("PESCATARIAN", Diet.PESCATARIAN),
    ],
)
def test_parse_diet_reads_a_form_value(text, expected):
    assert parse_diet(text) is expected


@pytest.mark.parametrize("text", ["carnivore", "no meat", "low meat", "veg"])
def test_parse_diet_rejects_anything_unknown(text):
    with pytest.raises(ValueError, match="diet"):
        parse_diet(text)
