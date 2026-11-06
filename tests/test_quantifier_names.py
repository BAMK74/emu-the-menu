"""A word that only says how much ("a drizzle of", "some") never makes a different ingredient."""

import pytest

from emu_the_menu.names import clean_name, name_key
from emu_the_menu.recipe_import import parse_ingredient_line


@pytest.mark.parametrize(
    "written",
    [
        "a drizzle of olive oil",
        "drizzle of olive oil",
        "A drizzle of extra virgin olive oil",
        "some olive oil",
        "a splash of olive oil",
        "a little olive oil",
        "a glug of olive oil",
        "a handful of olive oil",
        "a good drizzle of olive oil",
        "a generous drizzle of olive oil",
        "olive oil",
    ],
)
def test_how_much_does_not_change_which_ingredient(written):
    assert name_key(written) == name_key("olive oil")
    assert name_key(clean_name(written)) == name_key(written)
    assert clean_name(written) == "olive oil"


@pytest.mark.parametrize(
    ("written", "plain"),
    [
        ("a pinch of salt", "salt"),
        ("a handful of fresh basil", "basil"),
        ("a few sprigs of thyme", "thyme"),
        ("a bunch of parsley", "parsley"),
        ("a knob of butter", "butter"),
        ("a dollop of yoghurt", "yoghurt"),
        ("a squeeze of lemon juice", "lemon juice"),
        ("a couple of eggs", "eggs"),
        ("a tomato", "tomato"),
        ("an onion", "onion"),
    ],
)
def test_other_quantity_phrases_and_articles_are_dropped(written, plain):
    assert name_key(written) == name_key(plain)
    assert clean_name(written) == plain


def test_a_food_that_starts_with_a_quantity_word_is_kept():
    assert name_key("little gem lettuce") != name_key("gem lettuce")
    assert name_key("a little gem lettuce") == name_key("little gem lettuce")
    assert clean_name("a little gem lettuce") == "little gem lettuce"
    assert name_key("pinch pot") == "pinch pot"  # no "of": it is a name, not a measure


def test_a_name_that_is_only_a_quantity_is_left_alone():
    assert clean_name("some") == "some"
    assert name_key("some") == "some"
    assert name_key("a") == "a"


def test_imported_lines_drop_the_quantity_words_from_the_name():
    for text, name in [
        ("a drizzle of olive oil", "olive oil"),
        ("a glug of olive oil, to serve", "olive oil"),
        ("olive oil, for drizzling", "olive oil"),
        ("olive oil for frying", "olive oil"),
        ("some olive oil", "olive oil"),
        ("a handful of fresh basil leaves", "fresh basil leaves"),
    ]:
        line = parse_ingredient_line(text)
        assert line.name == name
        assert line.quantity is None or text[0].isdigit()  # nothing to measure without a number


@pytest.mark.parametrize(
    ("written", "plain"),
    [
        ("zest of 1 lemon", "lemon"),
        ("the zest of a lime", "lime"),
        ("finely grated zest of 2 oranges", "oranges"),
        ("zest and juice of 1 lemon", "lemon"),
        ("juice of half a lemon", "lemon"),
        ("rind of 1 orange", "orange"),
        ("peel of an orange", "orange"),
    ],
)
def test_zest_or_juice_of_a_fruit_is_the_fruit(written, plain):
    assert name_key(written) == name_key(plain)
    assert clean_name(written) == plain


def test_a_measured_zest_or_juice_stays_its_own_ingredient():
    # "1 tsp lemon zest" is measured in spoons, a lemon is counted: they cannot be one ingredient
    assert name_key("lemon zest") != name_key("lemon")
    assert name_key("lemon juice") != name_key("lemon")
    assert parse_ingredient_line("1 tsp lemon zest").name == "lemon zest"


@pytest.mark.parametrize(
    ("written", "plain"),
    [
        ("a handful basil leaves", "basil leaves"),
        ("a large handful of spinach", "spinach"),
        ("a few grinds of black pepper", "black pepper"),
        ("a dusting of flour", "flour"),
        ("a sprinkling of sesame seeds", "sesame seeds"),
        ("a trickle of honey", "honey"),
        ("a good pinch of salt", "salt"),
        ("a clove of garlic", "garlic"),
        ("a head of broccoli", "broccoli"),
        ("a stick of celery", "celery"),
        ("a can of tomatoes", "tomatoes"),
        ("a jar of pesto", "pesto"),
        ("a slice of bread", "bread"),
        ("a piece of ginger", "ginger"),
        ("a wedge of lemon", "lemon"),
        ("a block of tofu", "tofu"),
        ("a tub of yoghurt", "yoghurt"),
        ("a spoonful of honey", "honey"),
        ("a loaf of bread", "bread"),
        ("a bit of butter", "butter"),
        ("a touch of cream", "cream"),
    ],
)
def test_more_amount_phrases_are_dropped(written, plain):
    assert name_key(written) == name_key(plain)
    assert clean_name(written) == plain


@pytest.mark.parametrize(
    ("written", "plain"),
    [
        ("peeled garlic", "garlic"),
        ("deseeded red pepper", "red pepper"),
        ("trimmed green beans", "green beans"),
        ("boneless skinless chicken breast", "chicken breast"),
        ("torn basil", "basil"),
        ("beaten egg", "egg"),
        ("melted butter", "butter"),
        ("softened butter", "butter"),
        ("halved cherry tomatoes", "cherry tomatoes"),
        ("good quality olive oil", "olive oil"),
        ("washed spinach", "spinach"),
    ],
)
def test_more_describing_words_do_not_change_the_ingredient(written, plain):
    assert name_key(written) == name_key(plain)
    assert clean_name(written) == plain


def test_words_that_make_a_different_food_are_kept():
    assert name_key("frozen peas") != name_key("peas")
    assert name_key("cooked rice") != name_key("rice")
    assert name_key("gluten free flour") != name_key("flour")
    assert name_key("soft brown sugar") != name_key("sugar")
    assert name_key("cream of tartar") != name_key("cream")


def test_imported_lines_read_the_new_phrases_too():
    for text, name in [
        ("zest of 1 lemon", "lemon"),
        ("grated zest and juice of 1 lemon", "lemon"),
        ("a handful basil", "basil"),
        ("a drizzle of honey", "honey"),
        ("2 drizzles of honey", "honey"),
        ("3 glugs of olive oil", "olive oil"),
        ("a dollop of yoghurt", "yoghurt"),
        ("a few grinds of black pepper", "black pepper"),
        ("a head of broccoli", "broccoli"),
    ]:
        line = parse_ingredient_line(text)
        assert line.name == name, text
        assert line.quantity is None, text


@pytest.mark.parametrize(
    ("written", "plain"),
    [
        ("good grind of black pepper", "black pepper"),
        ("large handful of basil", "basil"),
        ("small bunch of kale", "kale"),
        ("big pinch of salt", "salt"),
        ("generous glug of olive oil", "olive oil"),
        ("pitted and quartered kalamata olives", "kalamata olives"),
        ("pitted dates", "dates"),
        ("melted coconut oil", "coconut oil"),
        ("skinless higher-welfare chicken breasts", "chicken breasts"),
    ],
)
def test_an_adjective_before_the_amount_and_more_describing_words_are_dropped(written, plain):
    assert clean_name(written) == plain
    assert name_key(clean_name(written)) == name_key(written)
