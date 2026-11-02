"""A line such as "water or vegetable broth" is two ingredients, either of which may be used."""

import itertools
import re
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.allergens import Allergen
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.ingredients import NEEDS_LOOK, judge
from emu_the_menu.names import split_alternatives
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather, analyse
from emu_the_menu.repository import Ingredient, StockRepository
from emu_the_menu.units import BaseUnit
from emu_the_menu.web import create_app

# --- reading "a or b" ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written, parts",
    [
        ("water or vegetable broth", ["water", "vegetable broth"]),
        ("almond butter or peanut butter", ["almond butter", "peanut butter"]),
        ("sherry vinegar or red wine vinegar", ["sherry vinegar", "red wine vinegar"]),
        ("basil or flat-leaf parsley", ["basil", "flat-leaf parsley"]),
        ("basil and/or microgreens", ["basil", "microgreens"]),
        ("milk or cream or water", ["milk", "cream", "water"]),
        ("Water OR vegetable broth", ["Water", "vegetable broth"]),
        # one food described two ways: the ending is shared
        ("brown or green lentils", ["brown lentils", "green lentils"]),
        ("red or yellow potatoes", ["red potatoes", "yellow potatoes"]),
        ("green or red pepper", ["green pepper", "red pepper"]),
        ("cherry or grape tomatoes", ["cherry tomatoes", "grape tomatoes"]),
        ("sunflower or vegetable oil", ["sunflower oil", "vegetable oil"]),
        ("red or white wine vinegar", ["red wine vinegar", "white wine vinegar"]),
        ("whole or skim milk", ["whole milk", "skim milk"]),
        ("red or yellow or green peppers", ["red peppers", "yellow peppers", "green peppers"]),
    ],
)
def test_alternatives_become_separate_names(written, parts):
    assert split_alternatives(written) == parts


@pytest.mark.parametrize(
    "written",
    [
        "oregano",
        "olive oil",
        "chicken stock / broth",
        "cornflour / corn starch",
        "or 2 med. yellow heirloom tomatoes",
        "salt or",
        "",
        "tomatoes or",
    ],
)
def test_a_name_without_real_alternatives_is_left_alone(written):
    assert split_alternatives(written) == ([written.strip()] if written.strip() else [""])


def test_the_same_alternative_twice_counts_once():
    assert split_alternatives("water or water") == ["water"]


# --- what a Recipe works out from alternatives ---------------------------------------------------


_ids = itertools.count(1)


def ing(name, kcal=None, groups=(), allergens=(), reviewed=True):
    return Ingredient(
        name,
        BaseUnit.MILLILITRE,
        id=next(_ids),
        kcal=kcal,
        food_groups=tuple(groups),
        allergens=tuple(allergens),
        reviewed=reviewed,
    )


def test_allergens_of_every_alternative_count():
    water, broth = ing("water", 0), ing("vegetable broth", 5, allergens=[Allergen.CELERY])
    facts = analyse([(water, 400, 1), (broth, 400, 1)], servings=2)
    assert facts.allergens == (Allergen.CELERY,)


def test_an_unchecked_alternative_makes_the_recipe_unchecked():
    water, broth = ing("water", 0), ing("vegetable broth", 5, reviewed=False)
    facts = analyse([(water, 400, 1), (broth, 400, 1)], servings=2)
    assert facts.unchecked == ("vegetable broth",)


def test_calories_count_the_highest_alternative_once_not_both():
    peanut = ing("peanut butter", 600)
    almond = ing("almond butter", 620)
    rice = ing("rice", 100)
    facts = analyse([(peanut, 100, 1), (almond, 100, 1), (rice, 100, None)], servings=1)
    # 100 ml of 600 or 620 per 100 ml, whichever is higher, plus 100 ml of rice
    assert facts.total_calories == 620 + 100


def test_calories_are_unknown_if_any_alternative_lacks_them():
    a, b = ing("a", 100), ing("b", None)
    facts = analyse([(a, 100, 1), (b, 100, 1)], servings=1)
    assert facts.total_calories is None
    assert facts.without_calories == ("b",)


def test_lines_without_a_group_work_as_before():
    assert analyse([(ing("x", 100), 100)], servings=1).total_calories == 100


# --- saving them ---------------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


def save(db_path, lines, name="Soup"):
    with RecipeRepository(db_path) as repo:
        return repo.add(name, 2, [MealType.DINNER], Weather.ANY, (), lines)


def lines_of(db_path, recipe_id=1):
    with RecipeRepository(db_path) as repo:
        return [
            (line.ingredient.name, line.quantity, line.alt_group)
            for line in repo.get(recipe_id).lines
        ]


def test_an_alternative_line_becomes_one_ingredient_per_alternative_with_the_same_quantity(db_path):
    save(db_path, [("rice", 100, "g"), ("water or vegetable broth", 400, "ml")])
    found = lines_of(db_path)
    assert found[0] == ("rice", 100, None)
    assert [(n, q) for n, q, _ in found[1:]] == [("water", 400), ("vegetable broth", 400)]
    assert found[1][2] is not None and found[1][2] == found[2][2]


def test_each_alternative_is_its_own_ingredient_that_nobody_has_checked(db_path):
    save(db_path, [("water or vegetable broth", 400, "ml")])
    with StockRepository(db_path) as stock:
        found = {i.name: i for i in stock.ingredients()}
    assert set(found) == {"water", "vegetable broth"}
    assert not found["water"].reviewed and not found["vegetable broth"].reviewed


def test_no_ingredient_is_ever_named_with_an_or(db_path):
    save(
        db_path,
        [
            ("almond butter or peanut butter", 100, "g"),
            ("brown or green lentils", 200, "g"),
            ("sunflower or vegetable oil", 15, "ml"),
        ],
    )
    with StockRepository(db_path) as stock:
        names = [i.name for i in stock.ingredients()]
    assert not [n for n in names if re.search(r"\bor\b", n)]
    assert {"almond butter", "peanut butter", "brown lentils", "green lentils"} <= set(names)


def test_two_alternative_lines_get_their_own_groups(db_path):
    save(db_path, [("water or broth", 1, "l"), ("brown or green lentils", 200, "g")])
    groups = {g for _, _, g in lines_of(db_path)}
    assert len(groups) == 2 and None not in groups


def test_an_alternative_that_is_also_listed_on_its_own_is_one_line(db_path):
    save(db_path, [("water or vegetable broth", 400, "ml"), ("vegetable broth", 100, "ml")])
    found = {n: (q, g) for n, q, g in lines_of(db_path)}
    assert found["vegetable broth"] == (500, None)
    assert found["water"][1] is None  # nothing left to be an alternative to


def test_updating_replaces_alternatives(db_path):
    recipe_id = save(db_path, [("water or vegetable broth", 400, "ml")])
    with RecipeRepository(db_path) as repo:
        repo.update(
            recipe_id, "Soup", 2, [MealType.DINNER], Weather.ANY, (), [("stock", 400, "ml")]
        )
    assert [n for n, _, _ in lines_of(db_path)] == ["stock"]


def test_the_export_says_which_lines_are_alternatives(db_path):
    save(db_path, [("water or vegetable broth", 400, "ml")])
    with RecipeRepository(db_path) as repo:
        lines = repo.export_data()[0]["lines"]
    assert lines[0]["alternative_group"] == lines[1]["alternative_group"] is not None


def test_an_older_database_gains_the_column(db_path):
    save(db_path, [("rice", 100, "g")])
    db = sqlite3.connect(db_path)
    db.executescript(
        "CREATE TABLE recipe_lines_old AS SELECT recipe_id, ingredient_id, quantity, position"
        " FROM recipe_lines; DROP TABLE recipe_lines;"
        "CREATE TABLE recipe_lines (recipe_id INTEGER NOT NULL REFERENCES recipes(id)"
        " ON DELETE CASCADE, ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),"
        " quantity REAL NOT NULL CHECK (quantity > 0), position INTEGER NOT NULL,"
        " PRIMARY KEY (recipe_id, ingredient_id));"
        "INSERT INTO recipe_lines SELECT * FROM recipe_lines_old; DROP TABLE recipe_lines_old;"
    )
    db.commit()
    db.close()
    assert lines_of(db_path) == [("rice", 100, None)]


# --- what the review does with a name that lists alternatives ------------------------------------


@pytest.mark.parametrize(
    "name",
    ["water or vegetable broth", "brown or green lentils", "basil and/or microgreens"],
)
def test_a_name_listing_alternatives_is_never_plain_and_says_why(name):
    verdict = judge(name)
    assert verdict.kind == NEEDS_LOOK
    assert "alternatives" in verdict.reason


# --- the pages -----------------------------------------------------------------------------------


@pytest.fixture
def client(db_path):
    app = create_app(
        db_path, today=lambda: date(2026, 11, 2), fetch_product=lambda code: {"status": 0}
    )
    return TestClient(app, follow_redirects=False)


def text_of(html):
    html = re.sub(r"<(style|script)\b.*?</\1>", "", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def review(db_path, name, kcal=None, groups=(), allergens=()):
    with StockRepository(db_path) as stock:
        found = next(i for i in stock.ingredients() if i.name == name)
        stock.set_ingredient_details(found.id, kcal, groups, allergens)


def test_the_edit_page_shows_alternatives_in_one_row(client, db_path):
    save(db_path, [("rice", 100, "g"), ("water or vegetable broth", 400, "ml")])
    page = client.get("/recipes/1").text
    assert 'value="water or vegetable broth"' in page
    assert len(re.findall(r'name="ingredient"[^>]*value="rice"', page)) == 1
    assert 'value="400"' in page


def test_saving_the_edit_page_again_keeps_the_alternatives(client, db_path):
    save(db_path, [("water or vegetable broth", 400, "ml")])
    client.post(
        "/recipes/1",
        data={
            "name": "Soup", "servings": "2", "meal_types": ["dinner"], "weather": "any",
            "ingredient": ["water or vegetable broth"], "quantity": ["400"], "unit": ["ml"],
        },
    )  # fmt: skip
    names = [n for n, _, _ in lines_of(db_path)]
    assert names == ["water", "vegetable broth"]


def test_a_recipe_card_counts_the_allergens_of_every_alternative(client, db_path):
    save(db_path, [("water or vegetable broth", 400, "ml")])
    review(db_path, "water", 0)
    review(db_path, "vegetable broth", 5, allergens=[Allergen.CELERY])
    html = client.get("/recipes").text
    assert "Celery" in text_of(html)
    assert 'chip-soon">Not checked' not in html


def test_a_recipe_stays_not_checked_while_any_alternative_is_unchecked(client, db_path):
    save(db_path, [("water or vegetable broth", 400, "ml")])
    review(db_path, "water", 0)
    html = client.get("/recipes").text
    assert 'chip-soon">Not checked' in html and "vegetable broth" in text_of(html)


def test_the_import_of_such_a_line_keeps_both(client, db_path):
    import json

    recipe = {
        "@type": "Recipe",
        "name": "Broth soup",
        "recipeYield": "2",
        "recipeIngredient": ["2 cups water or vegetable broth", "1 onion"],
    }
    html = f'<script type="application/ld+json">{json.dumps(recipe)}</script>'
    app = create_app(
        db_path, today=lambda: date(2026, 11, 2), fetch_recipe_page=lambda url: html,
        fetch_product=lambda code: {"status": 0},
    )  # fmt: skip
    page = TestClient(app).post("/recipes/import", data={"url": "https://example.com/x"}).text
    assert 'value="water or vegetable broth"' in page


# --- ingredients that were saved before this, as one name ---------------------------------------


def old_style(db_path, name="water or vegetable broth"):
    """A recipe whose ingredient is one name that lists alternatives, as older versions saved it."""
    save(db_path, [("rice", 100, "g")])
    db = sqlite3.connect(db_path)
    db.execute("INSERT INTO ingredients (name, base_unit) VALUES (?, 'ml')", (name,))
    combined = db.execute("SELECT id FROM ingredients WHERE name = ?", (name,)).fetchone()[0]
    db.execute(
        "INSERT INTO recipe_lines (recipe_id, ingredient_id, quantity, position)"
        " VALUES (1, ?, 400, 5)",
        (combined,),
    )
    db.commit()
    db.close()
    return combined


def test_the_plan_lists_the_ingredients_that_will_be_split(db_path):
    old_style(db_path)
    with StockRepository(db_path, auto_split=False) as stock:
        [(ingredient, parts)] = stock.split_plan()
    assert ingredient.name == "water or vegetable broth"
    assert parts == ["water", "vegetable broth"]


def test_tidying_splits_old_alternatives_and_keeps_the_quantity(db_path):
    old_style(db_path)
    with StockRepository(db_path, auto_split=False) as stock:
        assert stock.tidy() >= 1
        assert not [i for i in stock.ingredients() if " or " in i.name]
    found = lines_of(db_path)
    by_name = {n: (q, g) for n, q, g in found}
    assert by_name["water"][0] == by_name["vegetable broth"][0] == 400
    assert by_name["water"][1] == by_name["vegetable broth"][1] is not None
    assert by_name["rice"] == (100, None)


def test_the_parts_of_a_split_start_unchecked_even_if_the_whole_was_checked(db_path):
    combined = old_style(db_path)
    with StockRepository(db_path, auto_split=False) as stock:
        stock.set_ingredient_details(combined, 5, (FoodGroup.DAIRY,), (Allergen.MILK,))
        stock.tidy()
        parts = {i.name: i for i in stock.ingredients() if i.name in ("water", "vegetable broth")}
    assert not parts["water"].reviewed and not parts["vegetable broth"].reviewed
    assert parts["water"].allergens == () and parts["vegetable broth"].allergens == ()


def test_a_part_that_was_already_checked_stays_checked(db_path):
    old_style(db_path)
    save(db_path, [("water", 1, "ml")], name="Other")
    with StockRepository(db_path, auto_split=False) as stock:
        water = next(i for i in stock.ingredients() if i.name == "water")
        stock.set_ingredient_details(water.id, 0, (), ())
        stock.tidy()
        assert next(i for i in stock.ingredients() if i.name == "water").reviewed


def test_stock_held_under_the_old_name_keeps_it_from_being_deleted(db_path):
    combined = old_style(db_path)
    db = sqlite3.connect(db_path)
    db.execute(
        "INSERT INTO stock_items (ingredient_id, quantity, location) VALUES (?, 500, 'pantry')",
        (combined,),
    )
    db.commit()
    db.close()
    with StockRepository(db_path, auto_split=False) as stock:
        stock.tidy()
        assert [i.name for i in stock.ingredients() if " or " in i.name] == [
            "water or vegetable broth"
        ]
        assert len(stock.list_items()) == 1


def test_tidying_again_changes_nothing(db_path):
    old_style(db_path)
    with StockRepository(db_path, auto_split=False) as stock:
        stock.tidy()
        assert stock.tidy() == 0
        assert stock.split_plan() == []


def test_opening_the_app_splits_old_alternatives_by_itself(client, db_path):
    old_style(db_path)
    page = client.get("/ingredients").text
    assert "water or vegetable broth" not in page
    names = text_of(page)
    assert "water" in names and "vegetable broth" in names


def test_the_either_or_lives_in_the_recipe_only(client, db_path):
    old_style(db_path)
    client.get("/ingredients")  # opens the database, which splits what is stored
    with StockRepository(db_path) as stock:
        assert not [i for i in stock.ingredients() if " or " in i.name]
    page = client.get("/recipes/1").text
    assert 'value="water or vegetable broth"' in page


def test_the_review_never_groups_a_name_that_lists_alternatives(client, db_path):
    combined = old_style(db_path)
    db = sqlite3.connect(db_path)
    db.execute(
        "INSERT INTO stock_items (ingredient_id, quantity, location) VALUES (?, 5, 'pantry')",
        (combined,),
    )
    db.execute("INSERT INTO ingredients (name, base_unit) VALUES ('stock or broth', 'ml')")
    db.commit()
    db.close()
    page = client.get("/ingredients/review").text
    assert not re.search(r'name="grp" value="\d+" checked> [^<]* or [^<]*<', page)
    assert "water or vegetable broth" in page


def test_stock_cannot_be_added_as_a_choice(client, db_path):
    response = client.post(
        "/items",
        data={
            "ingredient": "water or vegetable broth",
            "quantity": "1",
            "unit": "l",
            "location": "pantry",
        },
    )
    assert response.status_code == 400
    assert "more than one ingredient" in text_of(response.text)
    with StockRepository(db_path) as stock:
        assert stock.list_items() == []


# --- planning with alternatives ------------------------------------------------------------------


def plan_for(db_path, stock):
    from emu_the_menu.eaters import Category, Eater
    from emu_the_menu.planner import PlanSettings, Slot, plan

    with RecipeRepository(db_path) as repo:
        recipes = repo.recipes()
    return plan(
        date(2026, 11, 2), 1, [Slot.DINNER], recipes, [Eater(1, "Sam", Category.MAN)], stock,
        settings=PlanSettings(shopping_allowance=9, cooldown_days=0),
    )  # fmt: skip


def ingredient_id(db_path, name):
    with StockRepository(db_path) as stock:
        return next(i.id for i in stock.ingredients() if i.name == name)


def test_a_group_of_alternatives_is_met_by_any_one_in_stock(db_path):
    save(db_path, [("water or vegetable broth", 400, "ml")])
    menu = plan_for(db_path, {ingredient_id(db_path, "vegetable broth"): None})
    assert menu.shopping == ()


def test_a_group_with_none_in_stock_needs_one_purchase_not_both(db_path):
    save(db_path, [("water or vegetable broth", 400, "ml")])
    menu = plan_for(db_path, {})
    assert [i.name for i in menu.shopping] == ["water"]


@pytest.mark.parametrize(
    "written, parts",
    [
        (
            "all-natural salted peanut or almond butter",
            ["all-natural salted peanut butter", "almond butter"],
        ),
        (
            "roasted lightly salted or unsalted pumpkin seeds",
            ["roasted lightly salted pumpkin seeds", "unsalted pumpkin seeds"],
        ),
        ("peanut or almond butter", ["peanut butter", "almond butter"]),
        ("brown or white onion", ["brown onion", "white onion"]),
    ],
)
def test_a_longer_left_side_still_shares_the_ending(written, parts):
    assert split_alternatives(written) == parts


def test_splitting_works_on_a_database_made_before_alternatives_existed(db_path):
    old_style(db_path)
    db = sqlite3.connect(db_path)
    db.executescript(
        "CREATE TABLE lines_old AS SELECT recipe_id, ingredient_id, quantity, position"
        " FROM recipe_lines; DROP TABLE recipe_lines;"
        "CREATE TABLE recipe_lines (recipe_id INTEGER NOT NULL REFERENCES recipes(id)"
        " ON DELETE CASCADE, ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),"
        " quantity REAL NOT NULL CHECK (quantity > 0), position INTEGER NOT NULL,"
        " PRIMARY KEY (recipe_id, ingredient_id));"
        "INSERT INTO recipe_lines SELECT * FROM lines_old; DROP TABLE lines_old;"
    )
    db.commit()
    db.close()
    with StockRepository(
        db_path, auto_split=False
    ) as stock:  # no recipe repository has opened it yet
        assert stock.tidy() >= 1


def test_an_alternative_whose_name_is_used_in_another_kind_of_unit_is_kept_apart(db_path):
    save(db_path, [("basil", 10, "g")], name="Pesto")
    old_style(db_path, name="basil or flat-leaf parsley")  # stored by volume
    with StockRepository(db_path, auto_split=False) as stock:
        assert stock.tidy() >= 1
        names = {i.name for i in stock.ingredients()}
    assert {"basil", "basil (by volume)", "flat-leaf parsley"} <= names
    assert not [n for n in names if " or " in n]


def test_one_tidy_is_enough_even_after_splitting(db_path):
    old_style(db_path, name="almond butter or peanut butter")
    save(db_path, [("fresh basil or parsley", 10, "g")], name="Herby")
    with StockRepository(db_path, auto_split=False) as stock:
        stock.tidy()
        assert stock.tidy() == 0
        assert stock.tidy_plan() == [] and stock.split_plan() == []


def test_several_old_alternatives_that_share_parts_all_get_split_in_one_go(db_path):
    save(db_path, [("basil", 10, "g")], name="Pesto")
    old_style(db_path, name="chopped fresh basil or flat-leaf parsley")
    db = sqlite3.connect(db_path)
    db.execute("INSERT INTO ingredients (name, base_unit) VALUES ('fresh parsley or basil', 'ml')")
    combined = db.execute(
        "SELECT id FROM ingredients WHERE name = 'fresh parsley or basil'"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO recipe_lines (recipe_id, ingredient_id, quantity, position, alt_group)"
        " VALUES (1, ?, 5, 7, NULL)",
        (combined,),
    )
    db.commit()
    db.close()
    with StockRepository(db_path) as stock:
        left = [i.name for i in stock.ingredients() if " or " in i.name]
    assert left == []
