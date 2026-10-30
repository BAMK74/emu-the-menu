import json

import pytest

from emu_the_menu.recipe_import import (
    NoRecipeFoundError,
    extract_recipe,
    match_ingredient,
    parse_ingredient_line,
)
from emu_the_menu.units import known_units

# --- reading one ingredient line --------------------------------------------------------------


@pytest.mark.parametrize(
    "text, name, quantity, unit",
    [
        ("200 g rice", "rice", 200, "g"),
        ("250ml milk", "milk", 250, "ml"),
        ("1 1/2 cups flour", "flour", 1.5, "cup"),
        ("½ tsp salt", "salt", 0.5, "tsp"),
        ("1½ tbsp butter", "butter", 1.5, "tbsp"),
        ("2-3 tablespoons olive oil", "olive oil", 2, "tbsp"),
        ("1 tablespoon of olive oil", "olive oil", 1, "tbsp"),
        ("1,5 kg potatoes, peeled", "potatoes", 1.5, "kg"),
        ("2 large eggs", "eggs", 2, "count"),
        ("3 eggs", "eggs", 3, "count"),
        ("2 cloves garlic, minced", "garlic", 2, "count"),
        ("1 lb chicken thighs", "chicken thighs", 453.6, "g"),
        ("8 oz pasta", "pasta", 226.8, "g"),
        ("1 litre vegetable stock", "vegetable stock", 1, "l"),
        ("1 (400 g) can chopped tomatoes", "chopped tomatoes", 400, "g"),
        ("2 (400 g) cans chickpeas, drained", "chickpeas", 800, "g"),
        ("1 can coconut milk", "coconut milk", 1, "count"),
        ("1 bunch kale", "kale", 1, "bunch"),
        ("1 head of lettuce", "lettuce", 1, "head"),
        ("  200   g   Rice  ", "rice", 200, "g"),
    ],
)
def test_a_readable_line_gives_a_name_quantity_and_unit(text, name, quantity, unit):
    line = parse_ingredient_line(text)
    assert (line.name, line.quantity, line.unit) == (name, quantity, unit)
    assert line.readable
    assert line.unit in known_units()


@pytest.mark.parametrize(
    "text, name",
    [
        ("salt to taste", "salt"),
        ("a pinch of saffron", "saffron"),
        ("Fresh parsley, to serve", "fresh parsley"),
        ("1 handful basil", "basil"),
    ],
)
def test_a_line_without_a_usable_amount_is_left_for_a_person_to_fix(text, name):
    line = parse_ingredient_line(text)
    assert not line.readable
    assert line.quantity is None and line.unit is None
    assert line.raw == text
    assert line.name == name


def test_a_blank_line_is_unreadable():
    assert not parse_ingredient_line("   ").readable


def test_zero_is_not_a_usable_quantity():
    assert not parse_ingredient_line("0 g rice").readable


# --- finding the recipe in a page -------------------------------------------------------------


def page(*blocks, extra=""):
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in blocks
    )
    return f"<html><head><title>Site</title>{scripts}</head><body>{extra}</body></html>"


RECIPE = {
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Tomato  soup",
    "recipeYield": "4 servings",
    "recipeIngredient": ["500 g tomatoes", "1 onion", "salt to taste"],
    "keywords": "Soup, Quick, vegetarian",
    "recipeCategory": ["Dinner"],
    "recipeInstructions": "Cook it. This text must not be kept.",
}


def test_a_recipe_is_read_from_the_page_data():
    imported = extract_recipe(page(RECIPE))
    assert imported.name == "Tomato soup"
    assert imported.servings == 4
    assert imported.lines == ("500 g tomatoes", "1 onion", "salt to taste")
    assert imported.tags == ("soup", "quick", "vegetarian", "dinner")


def test_only_the_structured_facts_are_kept_not_the_instructions():
    imported = extract_recipe(page(RECIPE))
    assert "must not be kept" not in repr(imported)


def test_a_recipe_inside_a_graph_is_found():
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "WebSite"}, RECIPE]}
    assert extract_recipe(page(graph)).name == "Tomato soup"


def test_a_recipe_in_a_list_of_blocks_is_found():
    assert extract_recipe(page([{"@type": "Organization"}, RECIPE])).name == "Tomato soup"


def test_a_type_given_as_a_list_is_found():
    block = {**RECIPE, "@type": ["Recipe", "NewsArticle"]}
    assert extract_recipe(page(block)).name == "Tomato soup"


def test_the_first_unbroken_block_wins_when_another_is_invalid_json():
    html = '<script type="application/ld+json">{not json</script>' + page(RECIPE)
    assert extract_recipe(html).name == "Tomato soup"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("4", 4),
        (4, 4),
        (["6", "6 servings"], 6),
        ("Makes 8 cookies", 8),
        ("a few", None),
        (None, None),
        ("0", None),
        ("500", None),
    ],
)
def test_servings_come_from_the_yield_when_it_is_a_sensible_number(value, expected):
    assert extract_recipe(page({**RECIPE, "recipeYield": value})).servings == expected


def test_ingredient_html_and_extra_spaces_are_cleaned():
    block = {**RECIPE, "recipeIngredient": ["<b>200</b>  g&nbsp;rice", "", "  "]}
    assert extract_recipe(page(block)).lines == ("200 g rice",)


def test_tags_are_trimmed_to_what_a_recipe_accepts():
    block = {**RECIPE, "keywords": ",".join(f"tag{n}" for n in range(15)) + ", " + "x" * 40}
    tags = extract_recipe(page(block)).tags
    assert len(tags) == 10 and all(len(t) <= 30 for t in tags)


def test_a_missing_name_is_blank_for_the_person_to_fill_in():
    block = {k: v for k, v in RECIPE.items() if k != "name"}
    assert extract_recipe(page(block)).name == ""


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>No data here</body></html>",
        page({"@type": "Article", "name": "News"}),
        page({**RECIPE, "recipeIngredient": []}),
        "",
    ],
)
def test_a_page_without_a_recipe_with_ingredients_is_reported(html):
    with pytest.raises(NoRecipeFoundError):
        extract_recipe(html)


# --- matching a written name to an ingredient already known ------------------------------------


@pytest.mark.parametrize(
    "written, known, expected",
    [
        ("tomatoes", ["tomato", "rice"], "tomato"),
        ("tomato", ["tomatoes"], "tomatoes"),
        ("eggs", ["egg"], "egg"),
        ("Rice", ["rice"], "rice"),
        ("potatoes", ["sweet potatoes"], "potatoes"),
        ("flour", ["rice"], "flour"),
        ("", ["rice"], ""),
    ],
)
def test_a_written_name_is_matched_to_a_known_ingredient_ignoring_plurals(written, known, expected):
    assert match_ingredient(written, known) == expected


def test_amounts_keep_their_decimals_when_no_conversion_is_needed():
    assert parse_ingredient_line("0.25 teaspoon salt").quantity == 0.25
    assert parse_ingredient_line("1.25 cups milk").quantity == 1.25


def test_a_plus_clause_is_not_part_of_the_name():
    line = parse_ingredient_line("1 tbsp sunflower oil plus a little extra for frying")
    assert line.name == "sunflower oil"


# --- lines as real recipe sites write them ------------------------------------------------------


@pytest.mark.parametrize(
    "text, name, quantity, unit",
    [
        ("250g / 10 oz dried fettuccine", "dried fettuccine", 250, "g"),
        ("125 ml / 1/2 cup heavy cream", "heavy cream", 125, "ml"),
        ("1 15-ounce can chickpeas, drained", "chickpeas", 425.2, "g"),
        ("1 15oz. can black beans", "black beans", 425.2, "g"),
        ("15 oz. can black beans", "black beans", 425.2, "g"),
        ("1 x 400g tin of cannellini beans", "cannellini beans", 400, "g"),
        ("2 x 400g tins of chopped tomatoes", "chopped tomatoes", 800, "g"),
        ("8 6-inch corn tortillas", "corn tortillas", 8, "count"),
        ("2 heaped tablespoons natural yoghurt", "natural yoghurt", 2, "tbsp"),
        ("1 heaping tsp dijon mustard", "dijon mustard", 1, "tsp"),
        ("1 lightly packed cup chopped baby spinach", "chopped baby spinach", 1, "cup"),
        ("4 ears corn", "corn", 4, "count"),
        ("2 stems choy sum", "choy sum", 2, "count"),
        ("3 garlic cloves, minced", "garlic", 3, "count"),
        ("2 cloves of garlic", "garlic", 2, "count"),
        ("1 medium-to-large clove garlic", "garlic", 1, "count"),
        ("2 tbsp oil ((or any neutral oil))", "oil", 2, "tbsp"),
        ("1 cup cheddar cheese ($1.25)", "cheddar cheese", 1, "cup"),
        ("1 tbsp sugar )", "sugar", 1, "tbsp"),
        ("2 tbsp soy sauce*", "soy sauce", 2, "tbsp"),
        ("optional: 1 tablespoon paprika", "paprika", 1, "tbsp"),
        ("3 tbsp cornflour / corn starch + 2 tbsp water", "cornflour / corn starch", 3, "tbsp"),
        ("1 cup unsweetened almond milk (or milk of choice)", "unsweetened almond milk", 1, "cup"),
    ],
)
def test_lines_as_real_sites_write_them_are_read(text, name, quantity, unit):
    line = parse_ingredient_line(text)
    assert line.name == name
    assert line.quantity == pytest.approx(quantity, abs=0.1)
    assert line.unit == unit


def test_a_heading_prefix_is_dropped_but_a_line_without_an_amount_stays_unreadable():
    line = parse_ingredient_line("Optional garnish: crumbled feta cheese")
    assert not line.readable and line.name == "crumbled feta cheese"


def test_no_name_ever_has_stray_brackets_stars_or_prices():
    for text in ["1 cup rice )", "1 cup rice ((cooked))", "1 cup rice*", "1 cup rice ($0.40)"]:
        name = parse_ingredient_line(text).name
        assert not set(name) & set("()*$"), (text, name)


def test_a_very_long_junk_name_is_cut_short():
    text = "1 cup " + "something very long " * 10
    assert len(parse_ingredient_line(text).name) <= 60


def test_a_word_before_the_number_does_not_hide_the_amount():
    line = parse_ingredient_line("Scant ½ teaspoon fine-grain sea salt")
    assert (line.name, line.quantity, line.unit) == ("fine-grain sea salt", 0.5, "tsp")
    line = parse_ingredient_line("about 2 cups milk")
    assert (line.name, line.quantity, line.unit) == ("milk", 2, "cup")
