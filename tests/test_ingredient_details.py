import sqlite3

import pytest

from emu_the_menu.allergens import Allergen
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.repository import IngredientNotFoundError, StockRepository
from emu_the_menu.stock import Location


@pytest.fixture
def repo(tmp_path):
    with StockRepository(tmp_path / "emu.db") as repo:
        yield repo


def ingredient(repo, name):
    return next(i for i in repo.ingredients() if i.name == name)


def test_a_new_ingredient_has_no_details(repo):
    repo.add("chicken", 500, "g", Location.FRIDGE)
    chicken = ingredient(repo, "chicken")
    assert (chicken.kcal, chicken.food_groups, chicken.allergens) == (None, (), ())


def test_every_ingredient_has_an_id_that_can_be_looked_up(repo):
    repo.add("chicken", 500, "g", Location.FRIDGE)
    chicken = ingredient(repo, "chicken")
    assert chicken.id is not None
    assert repo.get_ingredient(chicken.id) == chicken
    assert repo.get_ingredient(999) is None


def test_details_are_saved_and_come_back_in_the_standard_order(repo):
    repo.add("chicken", 500, "g", Location.FRIDGE)
    chicken = ingredient(repo, "chicken")
    repo.set_ingredient_details(
        chicken.id,
        165.0,
        [FoodGroup.POULTRY, FoodGroup.MEAT],
        [Allergen.TREE_NUTS, Allergen.CELERY],
    )
    saved = repo.get_ingredient(chicken.id)
    assert saved.kcal == 165.0
    assert saved.food_groups == (FoodGroup.MEAT, FoodGroup.POULTRY)
    assert saved.allergens == (Allergen.CELERY, Allergen.TREE_NUTS)
    assert ingredient(repo, "chicken") == saved


def test_saving_again_replaces_everything(repo):
    repo.add("chicken", 500, "g", Location.FRIDGE)
    chicken = ingredient(repo, "chicken")
    repo.set_ingredient_details(chicken.id, 165, [FoodGroup.POULTRY], [Allergen.CELERY])
    repo.set_ingredient_details(chicken.id, None, [], [Allergen.SOY])
    saved = repo.get_ingredient(chicken.id)
    assert (saved.kcal, saved.food_groups, saved.allergens) == (None, (), (Allergen.SOY,))


def test_details_belong_to_one_ingredient_only(repo):
    repo.add("chicken", 500, "g", Location.FRIDGE)
    repo.add("rice", 1, "kg", Location.PANTRY)
    repo.set_ingredient_details(ingredient(repo, "chicken").id, 165, [FoodGroup.POULTRY], [])
    rice = ingredient(repo, "rice")
    assert (rice.kcal, rice.food_groups) == (None, ())


def test_details_survive_more_stock_being_added_and_used(repo):
    repo.add("chicken", 500, "g", Location.FRIDGE)
    repo.set_ingredient_details(ingredient(repo, "chicken").id, 165, [FoodGroup.POULTRY], [])
    repo.add("chicken", 300, "g", Location.FRIDGE)
    repo.use("chicken", 800, "g")
    assert ingredient(repo, "chicken").kcal == 165


@pytest.mark.parametrize(
    ("name", "quantity", "unit", "too_much"),
    [("butter", 250, "g", 951), ("olive oil", 750, "ml", 951), ("egg", 6, "count", 3001)],
)
def test_calories_have_a_ceiling_that_suits_the_unit_and_nothing_changes_when_it_is_passed(
    repo, name, quantity, unit, too_much
):
    repo.add(name, quantity, unit, Location.PANTRY)
    item = ingredient(repo, name)
    repo.set_ingredient_details(item.id, 100, [FoodGroup.DAIRY], [])
    with pytest.raises(ValueError, match="calories"):
        repo.set_ingredient_details(item.id, too_much, [FoodGroup.EGGS], [Allergen.SOY])
    kept = repo.get_ingredient(item.id)
    assert (kept.kcal, kept.food_groups, kept.allergens) == (100, (FoodGroup.DAIRY,), ())


def test_negative_calories_are_rejected(repo):
    repo.add("butter", 250, "g", Location.FRIDGE)
    item = ingredient(repo, "butter")
    with pytest.raises(ValueError, match="calories"):
        repo.set_ingredient_details(item.id, -1, [], [])


def test_details_for_a_missing_ingredient_raise(repo):
    with pytest.raises(IngredientNotFoundError):
        repo.set_ingredient_details(999, 100, [], [])


def test_details_survive_reopening_the_database(tmp_path):
    path = tmp_path / "emu.db"
    with StockRepository(path) as repo:
        repo.add("chicken", 500, "g", Location.FRIDGE)
        repo.set_ingredient_details(
            ingredient(repo, "chicken").id, 165.5, [FoodGroup.POULTRY], [Allergen.EGGS]
        )
    with StockRepository(path) as repo:
        chicken = ingredient(repo, "chicken")
        assert (chicken.kcal, chicken.food_groups, chicken.allergens) == (
            165.5,
            (FoodGroup.POULTRY,),
            (Allergen.EGGS,),
        )


def test_changing_the_unit_family_forgets_the_calories_but_keeps_the_tags(repo):
    # Calories are "per 100 ml" for a liquid and "per 100 g" for a solid, so they would be wrong.
    repo.add("chicken", 500, "ml", Location.FRIDGE)
    item = ingredient(repo, "chicken")
    repo.set_ingredient_details(item.id, 165, [FoodGroup.POULTRY], [Allergen.CELERY])
    repo.change_base_unit("chicken", "g")
    changed = repo.get_ingredient(item.id)
    assert changed.kcal is None
    assert (changed.food_groups, changed.allergens) == ((FoodGroup.POULTRY,), (Allergen.CELERY,))


def test_the_export_lists_calories_food_groups_and_allergens(repo):
    repo.add("chicken", 500, "g", Location.FRIDGE)
    repo.add("rice", 1, "kg", Location.PANTRY)
    repo.set_ingredient_details(
        ingredient(repo, "chicken").id, 165, [FoodGroup.POULTRY], [Allergen.CELERY]
    )
    exported = {i["name"]: i for i in repo.export_data()["ingredients"]}
    assert exported["chicken"]["kcal"] == 165
    assert exported["chicken"]["kcal_basis"] == "per 100 g"
    assert exported["chicken"]["food_groups"] == ["poultry"]
    assert exported["chicken"]["allergens"] == ["celery"]
    assert exported["rice"]["kcal"] is None
    assert exported["rice"]["food_groups"] == [] and exported["rice"]["allergens"] == []


def test_an_older_database_without_these_columns_and_tables_is_upgraded(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """CREATE TABLE ingredients (id INTEGER PRIMARY KEY,
               name TEXT NOT NULL UNIQUE COLLATE NOCASE, base_unit TEXT NOT NULL,
               shelf_life_amount INTEGER, shelf_life_unit TEXT, last_quantity REAL, last_unit TEXT);
           CREATE TABLE stock_items (id INTEGER PRIMARY KEY,
               ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
               quantity REAL NOT NULL, location TEXT NOT NULL, expires_on TEXT);
           INSERT INTO ingredients (name, base_unit) VALUES ('flour', 'g');"""
    )
    db.commit()
    db.close()
    with StockRepository(path) as repo:
        flour = ingredient(repo, "flour")
        assert (flour.kcal, flour.food_groups, flour.allergens) == (None, (), ())
        repo.set_ingredient_details(flour.id, 364, [], [Allergen.GLUTEN])
    with StockRepository(path) as repo:
        assert ingredient(repo, "flour").allergens == (Allergen.GLUTEN,)


# --- reviewed: has a person looked at this ingredient's details? ------------------------------


def test_a_new_ingredient_is_not_reviewed(repo):
    repo.add("chicken", 500, "g", Location.FRIDGE)
    assert ingredient(repo, "chicken").reviewed is False


def test_saving_details_marks_the_ingredient_reviewed_even_when_nothing_is_ticked(repo):
    repo.add("rice", 1, "kg", Location.PANTRY)
    item = ingredient(repo, "rice")
    repo.set_ingredient_details(item.id, None, [], [])
    assert repo.get_ingredient(item.id).reviewed is True


def test_a_rejected_save_does_not_mark_it_reviewed(repo):
    repo.add("butter", 250, "g", Location.FRIDGE)
    item = ingredient(repo, "butter")
    with pytest.raises(ValueError):
        repo.set_ingredient_details(item.id, 99999, [], [])
    assert repo.get_ingredient(item.id).reviewed is False


def test_reviewed_survives_more_stock_and_a_unit_change(repo):
    repo.add("chicken", 500, "ml", Location.FRIDGE)
    item = ingredient(repo, "chicken")
    repo.set_ingredient_details(item.id, 165, [], [])
    repo.add("chicken", 100, "ml", Location.FRIDGE)
    repo.change_base_unit("chicken", "g")
    assert repo.get_ingredient(item.id).reviewed is True


def test_the_export_says_whether_each_ingredient_was_reviewed(repo):
    repo.add("chicken", 500, "g", Location.FRIDGE)
    repo.add("rice", 1, "kg", Location.PANTRY)
    repo.set_ingredient_details(ingredient(repo, "rice").id, None, [], [])
    exported = {i["name"]: i["reviewed"] for i in repo.export_data()["ingredients"]}
    assert exported == {"chicken": False, "rice": True}


def test_ingredients_from_an_older_database_start_unreviewed(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """CREATE TABLE ingredients (id INTEGER PRIMARY KEY,
               name TEXT NOT NULL UNIQUE COLLATE NOCASE, base_unit TEXT NOT NULL,
               shelf_life_amount INTEGER, shelf_life_unit TEXT, last_quantity REAL, last_unit TEXT,
               kcal REAL);
           INSERT INTO ingredients (name, base_unit, kcal) VALUES ('flour', 'g', 364);"""
    )
    db.commit()
    db.close()
    with StockRepository(path) as repo:
        flour = ingredient(repo, "flour")
        assert (flour.kcal, flour.reviewed) == (364, False)
