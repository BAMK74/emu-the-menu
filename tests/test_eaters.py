import pytest

from emu_the_menu.eaters import (
    DEFAULT_CALORIE_GOALS,
    Category,
    Eater,
    clean_name,
    parse_calorie_goal,
    parse_terms,
)


def test_every_category_has_a_default_calorie_goal():
    assert DEFAULT_CALORIE_GOALS == {
        Category.MAN: 2500,
        Category.WOMAN: 2000,
        Category.TEEN: 2200,
        Category.CHILD: 1600,
    }
    assert set(DEFAULT_CALORIE_GOALS) == set(Category)


def test_an_eater_uses_the_category_default_unless_overridden():
    assert Eater(1, "Sam", Category.WOMAN).calorie_goal == 2000
    assert Eater(1, "Sam", Category.WOMAN).has_custom_goal is False
    custom = Eater(1, "Sam", Category.WOMAN, calorie_goal_override=1800)
    assert custom.calorie_goal == 1800
    assert custom.has_custom_goal is True


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


def test_clean_name_trims_and_collapses_spaces():
    assert clean_name("  Mary   Ann ") == "Mary Ann"


@pytest.mark.parametrize("name", ["", "   ", "x" * 41])
def test_clean_name_rejects_blank_or_very_long_names(name):
    with pytest.raises(ValueError, match="name"):
        clean_name(name)
