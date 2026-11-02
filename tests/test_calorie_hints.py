import pytest

from emu_the_menu.calorie_hints import suggest_kcal
from emu_the_menu.ingredients import check_kcal
from emu_the_menu.units import BaseUnit

G, ML, COUNT = BaseUnit.GRAM, BaseUnit.MILLILITRE, BaseUnit.COUNT


def test_a_common_food_gets_a_rough_figure_for_its_unit():
    assert suggest_kcal("carrots", G) == 41
    assert suggest_kcal("olive oil", ML) < suggest_kcal("olive oil", G)  # oil floats
    assert suggest_kcal("egg", COUNT) == 72


def test_plurals_and_describing_words_do_not_matter():
    assert suggest_kcal("Carrot", G) == suggest_kcal("carrots", G)
    assert suggest_kcal("freshly cracked black pepper", G) == suggest_kcal("black pepper", G)
    assert suggest_kcal("extra virgin olive oil", ML) == suggest_kcal("olive oil", ML)
    assert suggest_kcal("sea salt", G) == 0


def test_a_name_that_only_contains_a_known_food_gets_nothing():
    # "peanut butter" is not "butter", and a sauce is not its main ingredient
    assert suggest_kcal("peanut butter", G) != suggest_kcal("butter", G)
    assert suggest_kcal("tomato ketchup", G) is None
    assert suggest_kcal("carrot cake", G) is None
    assert suggest_kcal("chicken and rice casserole", G) is None


def test_there_is_no_figure_for_a_unit_the_food_is_not_measured_in():
    assert suggest_kcal("egg", G) == 143
    assert suggest_kcal("carrots", COUNT) == 25
    assert suggest_kcal("black pepper", COUNT) is None
    assert suggest_kcal("olive oil", COUNT) is None
    assert suggest_kcal("rice", ML) is None


def test_unknown_names_get_nothing():
    assert suggest_kcal("xanthan gum", G) is None
    assert suggest_kcal("", G) is None


def test_every_suggestion_is_a_figure_the_app_would_accept():
    from emu_the_menu.calorie_hints import HINTS

    for name, (per_g, per_ml, per_piece) in HINTS.items():
        for value, unit in ((per_g, G), (per_ml, ML), (per_piece, COUNT)):
            if value is not None:
                assert check_kcal(value, unit) == value, name


def test_a_name_is_listed_only_once():
    from emu_the_menu.calorie_hints import _TABLE
    from emu_the_menu.names import name_key

    keys = [name_key(line.split("|")[0].strip()) for line in _TABLE.strip().splitlines()]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("name", ["water", "salt"])
def test_zero_calorie_foods_are_a_zero_not_none(name):
    assert suggest_kcal(name, ML if name == "water" else G) == 0
