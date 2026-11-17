"""The same ingredient written two ways ("extra virgin" and "extra-virgin") is one ingredient."""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.allergens import Allergen
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.names import canonical_name, clean_name, name_key
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import StockRepository
from emu_the_menu.stock import Location
from emu_the_menu.units import IncompatibleUnitError
from emu_the_menu.web import create_app


def add_recipe(db_url, name, lines):
    with RecipeRepository(db_url) as repo:
        return repo.add(name, 2, [MealType.DINNER], Weather.ANY, (), lines)


def names(db_url):
    with StockRepository(db_url) as stock:
        return [i.name for i in stock.ingredients()]


# --- comparing names ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a, b",
    [
        ("extra virgin olive oil", "extra-virgin olive oil"),
        ("Extra-Virgin  Olive Oil", "extra virgin olive oil"),
        ("tomato", "tomatoes"),
        ("all-purpose flour", "all purpose flour"),
        ("egg", "Eggs"),
        ("salt", "sea salt"),
        ("salt", "sea-salt"),
        ("salt", "Kosher salt"),
        ("salt", "fine sea salt"),
        ("salt", "table salt"),
        ("salt", "salt and pepper"),
        ("salt", "Salt & Pepper"),
        ("salt", "pepper and salt"),
        ("salt", "sea salt and black pepper"),
        ("salt", "salt, pepper"),
        ("olive oil", "extra virgin olive oil"),
        ("basil", "fresh basil"),
        ("tomato", "chopped tomatoes"),
        ("black pepper", "freshly ground black pepper"),
        ("cinnamon", "ground cinnamon"),
        ("cumin", "ground cumin"),
    ],
)
def test_names_that_differ_only_by_hyphen_case_spacing_or_plural_share_a_key(a, b):
    assert name_key(a) == name_key(b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("olive oil", "sesame oil"),
        ("brown sugar", "white sugar"),
        ("chickpeas", "chickpeas (counted)"),
        ("black pepper", "salt"),
        ("black pepper", "green pepper"),
        ("black pepper", "salt and garlic"),
        ("dried basil", "basil"),
        ("ground beef", "beef stock"),
        ("ground beef", "beef"),
        ("ground turkey", "turkey"),
    ],
)
def test_different_foods_do_not_share_a_key(a, b):
    assert name_key(a) != name_key(b)


def test_a_known_name_wins_and_a_new_one_is_cleaned():
    assert canonical_name("extra-virgin olive oil", ["extra virgin olive oil"]) == (
        "extra virgin olive oil"
    )
    assert canonical_name("Extra-Virgin Olive Oil", []) == "olive oil"
    assert canonical_name("tomatoes", ["tomato"]) == "tomato"


def test_a_known_name_is_only_reused_when_the_unit_kind_matches():
    known = {"tomato": "count"}
    assert canonical_name("tomatoes", known, "count") == "tomato"
    assert canonical_name("tomatoes", known, "g") == "tomatoes"


@pytest.mark.parametrize(
    "written, clean",
    [
        ("freshly cracked black pepper", "black pepper"),
        ("Freshly Ground Black Pepper", "black pepper"),
        ("pepper", "black pepper"),
        ("sea-salt", "salt"),
        ("Fine Sea Salt", "salt"),
        ("salt and pepper", "salt"),
        ("sea salt and black pepper", "salt"),
        ("extra-virgin olive oil", "olive oil"),
        ("chopped fresh cilantro", "cilantro"),
        ("medium-large english cucumber", "english cucumber"),
        ("organic sweet potatoes", "sweet potatoes"),
        ("diced red onion", "red onion"),
        ("ground cinnamon", "cinnamon"),
        ("ground beef", "ground beef"),
        ("dried basil", "dried basil"),
        ("red pepper", "red pepper"),
        ("green or red pepper", "green or red pepper"),
        ("fresh", "fresh"),
    ],
)
def test_input_is_cleaned_to_the_plain_name_of_the_food(written, clean):
    assert clean_name(written) == clean


def test_cleaning_never_changes_which_food_a_name_is():
    for name in ("freshly cracked black pepper", "pepper", "sea-salt", "extra-virgin olive oil"):
        assert name_key(clean_name(name)) == name_key(name)


def test_bare_pepper_is_black_pepper_but_other_peppers_are_not():
    assert (
        name_key("pepper") == name_key("black pepper") == name_key("freshly cracked black pepper")
    )
    assert name_key("red pepper") != name_key("black pepper")
    assert name_key("white pepper") != name_key("black pepper")


# --- new ingredients reuse the existing one ----------------------------------------------------


def test_a_recipe_reuses_an_ingredient_written_another_way(db_url):
    add_recipe(db_url, "A", [("extra virgin olive oil", 15, "ml")])
    add_recipe(db_url, "B", [("extra-virgin olive oil", 30, "ml")])
    assert names(db_url) == ["olive oil"]


def test_the_same_ingredient_twice_in_one_recipe_written_two_ways_is_added_together(db_url):
    recipe_id = add_recipe(
        db_url, "A", [("extra-virgin olive oil", 10, "ml"), ("extra virgin olive oil", 5, "ml")]
    )
    with RecipeRepository(db_url) as repo:
        [line] = repo.get(recipe_id).lines
    assert line.quantity == 15


def test_salt_in_its_variants_is_one_ingredient(db_url):
    add_recipe(db_url, "A", [("salt", 5, "g")])
    add_recipe(db_url, "B", [("sea salt", 3, "g")])
    add_recipe(db_url, "C", [("Salt & pepper", 2, "g")])
    add_recipe(db_url, "D", [("sea-salt", 2, "g")])
    assert names(db_url) == ["salt"]


def test_a_variant_in_another_kind_of_unit_stays_separate(db_url):
    add_recipe(db_url, "A", [("tomato", 2, "count")])
    add_recipe(db_url, "B", [("chopped tomatoes", 400, "g")])
    assert sorted(names(db_url)) == ["tomato", "tomatoes"]


def test_plurals_are_reused_too(db_url):
    add_recipe(db_url, "A", [("tomato", 1, "count")])
    add_recipe(db_url, "B", [("tomatoes", 2, "count")])
    assert names(db_url) == ["tomato"]


def test_a_new_name_is_stored_cleaned(db_url):
    add_recipe(
        db_url,
        "A",
        [("Extra-Virgin Olive Oil", 15, "ml"), ("Freshly Cracked Black Pepper", 1, "g")],
    )
    assert sorted(names(db_url)) == ["black pepper", "olive oil"]


def test_pepper_and_black_pepper_are_one_ingredient(db_url):
    add_recipe(db_url, "A", [("freshly cracked black pepper", 1, "g")])
    add_recipe(db_url, "B", [("pepper", 2, "g")])
    assert names(db_url) == ["black pepper"]


def test_stock_names_are_cleaned_too(db_url):
    with StockRepository(db_url) as stock:
        stock.add("Fresh Basil", 1, "bunch", Location.FRIDGE)
    assert names(db_url) == ["basil"]


def test_stock_reuses_the_ingredient_too(db_url):
    add_recipe(db_url, "A", [("olive oil", 15, "ml")])
    with StockRepository(db_url) as stock:
        stock.add("Extra-virgin olive oil", 500, "ml", Location.PANTRY)
        assert [i.ingredient for i in stock.list_items()] == ["olive oil"]
        stock.use("extra-virgin olive oil", 100, "ml")
        assert stock.list_items()[0].quantity == 400
    assert names(db_url) == ["olive oil"]


# --- merging twins that already exist ----------------------------------------------------------


def make_twins(db_url):
    """Two ingredients that are the same food, as an older version could create them."""
    import psycopg

    add_recipe(db_url, "A", [("olive oil", 15, "ml")])
    db = psycopg.connect(db_url, autocommit=True)
    db.execute("INSERT INTO ingredients (name, base_unit) VALUES ('extra-virgin olive oil', 'ml')")
    db.commit()
    twin = db.execute(
        "SELECT id FROM ingredients WHERE name = 'extra-virgin olive oil'"
    ).fetchone()[0]
    db.close()
    return twin


def test_twins_are_found(db_url):
    make_twins(db_url)
    with StockRepository(db_url) as stock:
        [group] = stock.twin_groups()
    assert sorted(i.name for i in group) == ["extra-virgin olive oil", "olive oil"]


def test_no_twins_means_no_groups(db_url):
    add_recipe(db_url, "A", [("olive oil", 15, "ml"), ("sesame oil", 5, "ml")])
    with StockRepository(db_url) as stock:
        assert stock.twin_groups() == []


def test_merging_moves_recipe_lines_and_stock_to_the_one_kept(db_url):
    twin = make_twins(db_url)
    with RecipeRepository(db_url) as repo:
        repo.add("B", 2, [MealType.LUNCH], Weather.ANY, (), [("salt", 1, "g")])
    import psycopg

    db = psycopg.connect(db_url, autocommit=True)
    db.execute(
        "INSERT INTO recipe_lines (recipe_id, ingredient_id, quantity, position)"
        " VALUES (2, %s, 7, 5)",
        (twin,),
    )
    db.execute(
        "INSERT INTO stock_items (ingredient_id, quantity, location) VALUES (%s, 250, 'pantry')",
        (twin,),
    )
    db.commit()
    db.close()
    with StockRepository(db_url) as stock:
        keep = next(i for i in stock.ingredients() if i.name == "olive oil")
        stock.merge_ingredients(keep.id, twin)
        assert [i.name for i in stock.ingredients() if "olive" in i.name] == ["olive oil"]
        assert [(i.ingredient, i.quantity) for i in stock.list_items()] == [("olive oil", 250)]
    with RecipeRepository(db_url) as repo:
        used = {r.name: [(x.ingredient.name, x.quantity) for x in r.lines] for r in repo.recipes()}
    assert ("olive oil", 7) in used["B"]
    assert used["A"] == [("olive oil", 15)]


def test_merging_adds_quantities_when_a_recipe_had_both(db_url):
    twin = make_twins(db_url)
    import psycopg

    db = psycopg.connect(db_url, autocommit=True)
    db.execute(
        "INSERT INTO recipe_lines (recipe_id, ingredient_id, quantity, position)"
        " VALUES (1, %s, 10, 9)",
        (twin,),
    )
    db.commit()
    db.close()
    with StockRepository(db_url) as stock:
        keep = next(i for i in stock.ingredients() if i.name == "olive oil")
        stock.merge_ingredients(keep.id, twin)
    with RecipeRepository(db_url) as repo:
        [line] = repo.get(1).lines
    assert line.quantity == 25


def test_merging_keeps_the_safer_union_of_what_was_ticked(db_url):
    twin = make_twins(db_url)
    with StockRepository(db_url) as stock:
        keep = next(i for i in stock.ingredients() if i.name == "olive oil")
        stock.set_ingredient_details(keep.id, 884, (), ())
        stock.set_ingredient_details(twin, None, (FoodGroup.DAIRY,), (Allergen.MILK,))
        stock.merge_ingredients(keep.id, twin)
        merged = stock.get_ingredient(keep.id)
    assert merged.reviewed and merged.kcal == 884
    assert merged.allergens == (Allergen.MILK,) and merged.food_groups == (FoodGroup.DAIRY,)


def test_merging_an_unreviewed_twin_into_a_reviewed_one_keeps_it_reviewed(db_url):
    twin = make_twins(db_url)
    with StockRepository(db_url) as stock:
        keep = next(i for i in stock.ingredients() if i.name == "olive oil")
        stock.set_ingredient_details(keep.id, 884, (), ())
        stock.merge_ingredients(keep.id, twin)
        assert stock.get_ingredient(keep.id).reviewed


def test_merging_an_unreviewed_into_an_unreviewed_stays_unreviewed(db_url):
    twin = make_twins(db_url)
    with StockRepository(db_url) as stock:
        keep = next(i for i in stock.ingredients() if i.name == "olive oil")
        stock.merge_ingredients(keep.id, twin)
        assert not stock.get_ingredient(keep.id).reviewed


def test_twins_stored_in_different_kinds_of_unit_are_not_merged(db_url):
    import psycopg

    add_recipe(db_url, "A", [("tomato", 2, "count")])
    db = psycopg.connect(db_url, autocommit=True)
    db.execute("INSERT INTO ingredients (name, base_unit) VALUES ('tomatoes', 'g')")
    db.commit()
    other = db.execute("SELECT id FROM ingredients WHERE name = 'tomatoes'").fetchone()[0]
    db.close()
    with StockRepository(db_url) as stock:
        keep = next(i for i in stock.ingredients() if i.name == "tomato")
        with pytest.raises(IncompatibleUnitError):
            stock.merge_ingredients(keep.id, other)
        assert len(stock.ingredients()) == 2


# --- the page ----------------------------------------------------------------------------------


@pytest.fixture
def client(db_url):
    app = create_app(
        db_url, today=lambda: date(2026, 11, 2), fetch_product=lambda code: {"status": 0}
    )
    return TestClient(app, follow_redirects=False)


def text_of(html):
    html = re.sub(r"<(style|script)\b.*?</\1>", "", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_the_ingredients_page_offers_to_tidy_twins_and_names_them(client, db_url):
    make_twins(db_url)
    text = text_of(client.get("/ingredients").text)
    assert "olive oil" in text and "extra-virgin olive oil" in text
    assert "Tidy the ticked names" in text
    assert 'action="/ingredients/tidy"' in client.get("/ingredients").text


def test_the_page_says_nothing_about_tidying_when_names_are_clean(client, db_url):
    add_recipe(db_url, "A", [("olive oil", 15, "ml")])
    assert "/ingredients/tidy" not in client.get("/ingredients").text


def test_pressing_merge_merges_every_group_and_returns_to_the_list(client, db_url):
    make_twins(db_url)
    response = client.post("/ingredients/tidy")
    assert response.status_code == 303 and response.headers["location"] == "/ingredients"
    assert names(db_url) == ["olive oil"]


def test_pressing_merge_with_no_twins_changes_nothing(client, db_url):
    add_recipe(db_url, "A", [("olive oil", 15, "ml")])
    assert client.post("/ingredients/tidy").status_code == 303
    assert names(db_url) == ["olive oil"]


def test_the_tidy_route_does_not_clash_with_an_ingredient_id(client, db_url):
    add_recipe(db_url, "A", [("olive oil", 15, "ml")])
    assert client.post("/ingredients/tidy").status_code == 303


# --- tidying names that are already stored -----------------------------------------------------


def insert(db_url, *rows):
    import psycopg

    db = psycopg.connect(db_url, autocommit=True)
    for name, unit in rows:
        db.execute("INSERT INTO ingredients (name, base_unit) VALUES (%s, %s)", (name, unit))
    db.commit()
    db.close()


def test_the_plan_lists_merges_and_renames(db_url):
    with StockRepository(db_url):
        pass
    insert(
        db_url,
        ("freshly cracked black pepper", "g"),
        ("pepper", "g"),
        ("chopped fresh cilantro", "g"),
        ("garlic", "g"),
    )
    with StockRepository(db_url) as stock:
        plan = stock.tidy_plan()
    summary = {tuple(sorted(i.name for i in members)): target for members, target in plan}
    assert summary == {
        ("freshly cracked black pepper", "pepper"): "black pepper",
        ("chopped fresh cilantro",): "cilantro",
    }


def test_tidying_merges_and_renames_and_returns_how_many_changes(db_url):
    with StockRepository(db_url):
        pass
    insert(
        db_url,
        ("freshly cracked black pepper", "g"),
        ("pepper", "g"),
        ("chopped fresh cilantro", "g"),
    )
    with StockRepository(db_url) as stock:
        assert stock.tidy() == 2
        assert sorted(i.name for i in stock.ingredients()) == ["black pepper", "cilantro"]
        assert stock.tidy() == 0
        assert stock.tidy_plan() == []


def test_tidying_prefers_a_name_that_is_already_clean_and_moves_everything_to_it(db_url):
    add_recipe(db_url, "A", [("black pepper", 1, "g")])
    insert(db_url, ("freshly cracked black pepper", "g"))
    with StockRepository(db_url) as stock:
        messy = next(i for i in stock.ingredients() if i.name.startswith("freshly"))
        stock.set_ingredient_details(messy.id, None, (), (Allergen.CELERY,))
        stock.tidy()
        [only] = stock.ingredients()
    assert only.name == "black pepper"
    assert only.allergens == (Allergen.CELERY,)
    with RecipeRepository(db_url) as repo:
        assert repo.get(1).lines[0].ingredient.name == "black pepper"


def test_tidying_does_not_rename_onto_a_name_used_by_another_kind_of_unit(db_url):
    add_recipe(db_url, "A", [("tomatoes", 2, "count")])
    insert(db_url, ("chopped tomatoes", "g"))
    with StockRepository(db_url) as stock:
        assert stock.tidy() == 0
        assert sorted(i.name for i in stock.ingredients()) == ["chopped tomatoes", "tomatoes"]


def test_tidying_with_nothing_to_do_changes_nothing(db_url):
    add_recipe(db_url, "A", [("olive oil", 15, "ml"), ("salt", 1, "g")])
    with StockRepository(db_url) as stock:
        assert stock.tidy_plan() == []
        assert stock.tidy() == 0


def test_the_page_lists_the_planned_changes_and_the_button_does_them(client, db_url):
    with StockRepository(db_url):
        pass
    insert(db_url, ("freshly cracked black pepper", "g"), ("pepper", "g"))
    page = client.get("/ingredients").text
    text = text_of(page)
    assert "freshly cracked black pepper" in text and "black pepper" in text
    assert 'action="/ingredients/tidy"' in page
    assert client.post("/ingredients/tidy").status_code == 303
    assert names(db_url) == ["black pepper"]


@pytest.mark.parametrize(
    "written, clean",
    [
        ("all-purpose flour", "all-purpose flour"),
        ("gluten-free rolled oats", "gluten-free rolled oats"),
        ("fine-grain sea salt", "salt"),
        ("finely chopped fresh mint", "mint"),
        ("finely diced red onion", "red onion"),
        ("can diced tomatoes", "can tomatoes"),
        ("tomatoes (counted)", "tomatoes (counted)"),
        ("baking soda / bi-carb", "baking soda / bi-carb"),
        ("sea-salt", "salt"),
    ],
)
def test_real_compounds_keep_their_hyphens_and_cooking_words_go(written, clean):
    assert clean_name(written) == clean


def test_two_groups_never_end_up_with_the_same_name(db_url):
    with StockRepository(db_url):
        pass
    insert(db_url, ("chopped tomatoes", "g"), ("diced tomatoes", "ml"))
    with StockRepository(db_url) as stock:
        stock.tidy()
        assert sorted(i.name for i in stock.ingredients()) == ["diced tomatoes", "tomatoes"]


def test_a_tinned_ingredient_is_not_the_same_as_the_fresh_one():
    assert name_key("can diced tomatoes") != name_key("tomato")
    assert name_key("tin chickpeas") != name_key("chickpeas")
