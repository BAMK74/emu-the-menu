import sqlite3

import pytest

from emu_the_menu.allergens import Allergen
from emu_the_menu.eaters import (
    DEFAULT_CALORIE_GOALS,
    Category,
    Diet,
    Eater,
    EaterNotFoundError,
    EaterRepository,
)
from emu_the_menu.repository import StockRepository
from emu_the_menu.stock import Location


@pytest.fixture
def repo(tmp_path):
    with EaterRepository(tmp_path / "emu.db") as repo:
        yield repo


def test_a_new_database_has_no_eaters(repo):
    assert repo.list() == []


def test_adding_an_eater_stores_every_field(repo):
    eater = repo.add("Sam", Category.TEEN, 200, ["Peanuts", "shellfish"], ["Olives"])
    assert eater.id is not None
    assert repo.list() == [
        Eater(
            eater.id,
            "Sam",
            Category.TEEN,
            200,
            allergies=("peanuts", "shellfish"),
            dislikes=("olives",),
            default_goal=2200,
        )
    ]
    assert repo.list()[0].calorie_goal == 2400


def test_an_eater_without_an_adjustment_gets_the_default(repo):
    eater = repo.add("Mia", Category.CHILD)
    assert eater.calorie_adjustment == 0
    assert eater.calorie_goal == 1600


def test_eaters_are_listed_in_the_order_they_were_added(repo):
    for name in ("Zoe", "Adam", "Mia"):
        repo.add(name, Category.WOMAN)
    assert [e.name for e in repo.list()] == ["Zoe", "Adam", "Mia"]


def test_names_are_cleaned_and_must_be_unique_ignoring_case(repo):
    repo.add("  Mary   Ann ", Category.WOMAN)
    assert repo.list()[0].name == "Mary Ann"
    with pytest.raises(ValueError, match="already"):
        repo.add("mary ann", Category.WOMAN)
    assert len(repo.list()) == 1


def test_an_allergy_is_not_also_kept_as_a_dislike(repo):
    eater = repo.add("Sam", Category.MAN, allergies=["peanuts"], dislikes=["peanuts", "olives"])
    assert eater.allergies == ("peanuts",)
    assert eater.dislikes == ("olives",)


def test_only_listed_adjustments_are_accepted(repo):
    with pytest.raises(ValueError, match="adjustment"):
        repo.add("Sam", Category.MAN, 150)
    assert repo.list() == []


def test_an_adjustment_that_pushes_the_goal_out_of_range_is_rejected(repo):
    repo.set_default_goals({Category.CHILD: 500})
    with pytest.raises(ValueError, match="calorie goal"):
        repo.add("Mia", Category.CHILD, -100)
    assert repo.list() == []


def test_updating_changes_everything(repo):
    eater = repo.add("Sam", Category.MAN, 0, ["peanuts"], ["olives"])
    updated = repo.update(eater.id, "Samuel", Category.TEEN, -100, ["eggs"], [])
    assert updated == Eater(
        eater.id,
        "Samuel",
        Category.TEEN,
        -100,
        allergies=("eggs",),
        dislikes=(),
        default_goal=2200,
    )
    assert repo.list() == [updated]


def test_updating_can_reset_the_adjustment(repo):
    eater = repo.add("Sam", Category.MAN, 500)
    assert repo.update(eater.id, "Sam", Category.MAN, 0).calorie_goal == 2500


def test_an_eater_can_keep_their_own_name_when_updating(repo):
    eater = repo.add("Sam", Category.MAN)
    repo.update(eater.id, "sam", Category.MAN)
    assert repo.list()[0].name == "sam"


def test_updating_to_another_eaters_name_is_rejected(repo):
    repo.add("Sam", Category.MAN)
    other = repo.add("Mia", Category.CHILD)
    with pytest.raises(ValueError, match="already"):
        repo.update(other.id, "SAM", Category.CHILD)
    assert repo.list()[1].name == "Mia"


def test_deleting_removes_the_eater_and_their_terms(repo):
    sam = repo.add("Sam", Category.MAN, allergies=["peanuts"], dislikes=["olives"])
    repo.add("Mia", Category.CHILD)
    repo.delete(sam.id)
    assert [e.name for e in repo.list()] == ["Mia"]
    assert repo.get(sam.id) is None


def test_get_returns_none_for_an_unknown_id(repo):
    assert repo.get(999) is None


def test_a_missing_eater_raises_on_update_and_delete(repo):
    with pytest.raises(EaterNotFoundError):
        repo.update(999, "Nobody", Category.MAN)
    with pytest.raises(EaterNotFoundError):
        repo.delete(999)


def test_eaters_survive_reopening_the_database(tmp_path):
    path = tmp_path / "emu.db"
    with EaterRepository(path) as repo:
        repo.add("Sam", Category.MAN, 300, ["eggs"])
    with EaterRepository(path) as repo:
        assert repo.list()[0].allergies == ("eggs",)
        assert repo.list()[0].calorie_goal == 2800


def test_eaters_and_stock_share_one_database_without_interfering(tmp_path):
    path = tmp_path / "emu.db"
    with StockRepository(path) as stock, EaterRepository(path) as eaters:
        stock.add("flour", 500, "g", Location.PANTRY)
        eaters.add("Sam", Category.MAN)
        assert len(stock.list_items()) == 1
        assert len(eaters.list()) == 1


# --- default goals per category -------------------------------------------------------------


def test_the_default_goals_start_as_the_built_in_ones(repo):
    assert repo.default_goals() == DEFAULT_CALORIE_GOALS


def test_default_goals_can_be_set_for_some_categories_only(repo):
    repo.set_default_goals({Category.WOMAN: 2100, Category.CHILD: 1700})
    goals = repo.default_goals()
    assert goals[Category.WOMAN] == 2100
    assert goals[Category.CHILD] == 1700
    assert goals[Category.MAN] == 2500
    assert goals[Category.TEEN] == 2200


def test_a_blank_default_goal_goes_back_to_the_built_in_one(repo):
    repo.set_default_goals({Category.WOMAN: 2100})
    repo.set_default_goals({Category.WOMAN: None})
    assert repo.default_goals()[Category.WOMAN] == 2000


def test_changing_a_default_moves_everyone_in_that_category(repo):
    repo.add("Ann", Category.WOMAN, 0)
    repo.add("Beth", Category.WOMAN, 200)
    repo.add("Carl", Category.MAN, 0)
    repo.set_default_goals({Category.WOMAN: 2100})
    goals = {e.name: e.calorie_goal for e in repo.list()}
    assert goals == {"Ann": 2100, "Beth": 2300, "Carl": 2500}


@pytest.mark.parametrize("bad", [499, 6001, 0, -2000])
def test_a_default_goal_outside_the_range_is_rejected_and_nothing_changes(repo, bad):
    with pytest.raises(ValueError, match="calorie goal"):
        repo.set_default_goals({Category.MAN: 2600, Category.WOMAN: bad})
    assert repo.default_goals() == DEFAULT_CALORIE_GOALS


def test_default_goals_survive_reopening_the_database(tmp_path):
    path = tmp_path / "emu.db"
    with EaterRepository(path) as repo:
        repo.set_default_goals({Category.TEEN: 2300})
    with EaterRepository(path) as repo:
        assert repo.default_goals()[Category.TEEN] == 2300


# --- upgrading older databases --------------------------------------------------------------


def test_an_old_absolute_goal_becomes_an_adjustment_to_the_default(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """CREATE TABLE eaters (id INTEGER PRIMARY KEY,
               name TEXT NOT NULL UNIQUE COLLATE NOCASE, category TEXT NOT NULL,
               calorie_goal INTEGER);
           INSERT INTO eaters (name, category, calorie_goal) VALUES
               ('Mia', 'child', 1500), ('Sam', 'man', NULL), ('Ann', 'woman', 2000),
               ('Odd', 'child', 1630), ('Big', 'man', 5000), ('Tiny', 'woman', 600);"""
    )
    db.commit()
    db.close()
    with EaterRepository(path) as repo:
        goals = {e.name: (e.calorie_adjustment, e.calorie_goal) for e in repo.list()}
    # Values between the drop-down's steps snap to the nearest step, and far ones to the limit.
    assert goals == {
        "Mia": (-100, 1500),
        "Sam": (0, 2500),
        "Ann": (0, 2000),
        "Odd": (0, 1600),
        "Big": (1000, 3500),
        "Tiny": (-1000, 1000),
    }


def test_upgrading_twice_does_not_change_anything(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """CREATE TABLE eaters (id INTEGER PRIMARY KEY,
               name TEXT NOT NULL UNIQUE COLLATE NOCASE, category TEXT NOT NULL,
               calorie_goal INTEGER);
           INSERT INTO eaters (name, category, calorie_goal) VALUES ('Mia', 'child', 1500);"""
    )
    db.commit()
    db.close()
    for _ in range(2):
        with EaterRepository(path) as repo:
            assert repo.list()[0].calorie_adjustment == -100


# --- diets ----------------------------------------------------------------------------------


def test_an_eater_eats_everything_unless_a_diet_is_chosen(repo):
    assert repo.add("Sam", Category.MAN).diet is Diet.OMNIVORE


def test_a_diet_is_stored_with_the_eater(repo):
    eater = repo.add("Mia", Category.WOMAN, diet=Diet.PESCATARIAN)
    assert eater.diet is Diet.PESCATARIAN
    assert repo.get(eater.id).diet is Diet.PESCATARIAN
    assert repo.list()[0].diet is Diet.PESCATARIAN


def test_updating_can_change_the_diet(repo):
    eater = repo.add("Mia", Category.WOMAN, diet=Diet.VEGETARIAN)
    updated = repo.update(eater.id, "Mia", Category.WOMAN, diet=Diet.VEGAN)
    assert updated.diet is Diet.VEGAN
    assert repo.update(eater.id, "Mia", Category.WOMAN).diet is Diet.OMNIVORE


def test_diets_survive_reopening_the_database(tmp_path):
    path = tmp_path / "emu.db"
    with EaterRepository(path) as repo:
        repo.add("Mia", Category.WOMAN, diet=Diet.LOW_MEAT)
    with EaterRepository(path) as repo:
        assert repo.list()[0].diet is Diet.LOW_MEAT


def test_a_database_from_before_diets_gets_everyone_eating_everything(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """CREATE TABLE eaters (id INTEGER PRIMARY KEY,
               name TEXT NOT NULL UNIQUE COLLATE NOCASE, category TEXT NOT NULL,
               calorie_adjustment INTEGER NOT NULL DEFAULT 0);
           INSERT INTO eaters (name, category, calorie_adjustment) VALUES ('Mia', 'woman', 200);"""
    )
    db.commit()
    db.close()
    with EaterRepository(path) as repo:
        [mia] = repo.list()
        assert (mia.diet, mia.calorie_adjustment) == (Diet.OMNIVORE, 200)
        repo.update(mia.id, "Mia", Category.WOMAN, 200, diet=Diet.VEGAN)
    with EaterRepository(path) as repo:
        assert repo.list()[0].diet is Diet.VEGAN


# --- allergens ------------------------------------------------------------------------------


def test_an_eater_has_no_allergens_unless_some_are_chosen(repo):
    assert repo.add("Sam", Category.MAN).allergens == ()


def test_allergens_are_stored_in_the_standard_order(repo):
    eater = repo.add("Mia", Category.WOMAN, allergens=[Allergen.TREE_NUTS, Allergen.MILK])
    assert eater.allergens == (Allergen.MILK, Allergen.TREE_NUTS)
    assert repo.get(eater.id).allergens == (Allergen.MILK, Allergen.TREE_NUTS)


def test_allergens_and_free_text_allergies_are_kept_apart(repo):
    eater = repo.add("Mia", Category.WOMAN, allergies=["kiwi"], allergens=[Allergen.MILK])
    assert (eater.allergens, eater.allergies) == ((Allergen.MILK,), ("kiwi",))


def test_updating_replaces_the_allergens(repo):
    eater = repo.add("Mia", Category.WOMAN, allergens=[Allergen.MILK, Allergen.EGGS])
    updated = repo.update(eater.id, "Mia", Category.WOMAN, allergens=[Allergen.SESAME])
    assert updated.allergens == (Allergen.SESAME,)
    assert repo.update(eater.id, "Mia", Category.WOMAN).allergens == ()


def test_deleting_an_eater_removes_their_allergens(repo, tmp_path):
    eater = repo.add("Mia", Category.WOMAN, allergens=[Allergen.MILK])
    repo.delete(eater.id)
    db = sqlite3.connect(tmp_path / "emu.db")
    assert db.execute("SELECT COUNT(*) FROM eater_allergens").fetchone()[0] == 0


def test_allergens_survive_reopening_the_database(tmp_path):
    path = tmp_path / "emu.db"
    with EaterRepository(path) as repo:
        repo.add("Mia", Category.WOMAN, allergens=[Allergen.GLUTEN, Allergen.SOY])
    with EaterRepository(path) as repo:
        assert repo.list()[0].allergens == (Allergen.GLUTEN, Allergen.SOY)


def test_a_database_from_before_allergens_still_opens(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """CREATE TABLE eaters (id INTEGER PRIMARY KEY,
               name TEXT NOT NULL UNIQUE COLLATE NOCASE, category TEXT NOT NULL,
               calorie_adjustment INTEGER NOT NULL DEFAULT 0,
               diet TEXT NOT NULL DEFAULT 'omnivore');
           INSERT INTO eaters (name, category) VALUES ('Mia', 'woman');"""
    )
    db.commit()
    db.close()
    with EaterRepository(path) as repo:
        [mia] = repo.list()
        assert mia.allergens == ()
        repo.update(mia.id, "Mia", Category.WOMAN, allergens=[Allergen.FISH])
        assert repo.list()[0].allergens == (Allergen.FISH,)
