import pytest

from emu_the_menu.eaters import Category, Eater, EaterNotFoundError, EaterRepository
from emu_the_menu.repository import StockRepository
from emu_the_menu.stock import Location


@pytest.fixture
def repo(tmp_path):
    with EaterRepository(tmp_path / "emu.db") as repo:
        yield repo


def test_a_new_database_has_no_eaters(repo):
    assert repo.list() == []


def test_adding_an_eater_stores_every_field(repo):
    eater = repo.add("Sam", Category.TEEN, 2400, ["Peanuts", "shellfish"], ["Olives"])
    assert eater.id is not None
    assert repo.list() == [
        Eater(
            eater.id,
            "Sam",
            Category.TEEN,
            2400,
            allergies=("peanuts", "shellfish"),
            dislikes=("olives",),
        )
    ]


def test_an_eater_without_a_custom_goal_uses_the_default(repo):
    eater = repo.add("Mia", Category.CHILD)
    assert eater.calorie_goal_override is None
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


def test_updating_changes_everything(repo):
    eater = repo.add("Sam", Category.MAN, None, ["peanuts"], ["olives"])
    updated = repo.update(eater.id, "Samuel", Category.TEEN, 2100, ["eggs"], [])
    assert updated == Eater(
        eater.id, "Samuel", Category.TEEN, 2100, allergies=("eggs",), dislikes=()
    )
    assert repo.list() == [updated]


def test_updating_can_clear_a_custom_goal(repo):
    eater = repo.add("Sam", Category.MAN, 3000)
    assert repo.update(eater.id, "Sam", Category.MAN, None).calorie_goal == 2500


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
        repo.add("Sam", Category.MAN, 2600, ["eggs"])
    with EaterRepository(path) as repo:
        assert repo.list()[0].allergies == ("eggs",)


def test_eaters_and_stock_share_one_database_without_interfering(tmp_path):
    path = tmp_path / "emu.db"
    with StockRepository(path) as stock, EaterRepository(path) as eaters:
        stock.add("flour", 500, "g", Location.PANTRY)
        eaters.add("Sam", Category.MAN)
        assert len(stock.list_items()) == 1
        assert len(eaters.list()) == 1
