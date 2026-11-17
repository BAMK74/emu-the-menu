import pytest

from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.recipe_repository import RecipeNotFoundError, RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import StockRepository
from emu_the_menu.stock import Location
from emu_the_menu.units import BaseUnit, IncompatibleUnitError, UnknownUnitError


@pytest.fixture
def repo(db_url):
    with RecipeRepository(db_url) as r:
        yield r


def add(repo, name="Tomato rice", servings=2, lines=None, meals=(MealType.DINNER,), **extra):
    lines = [("rice", 200, "g"), ("tomatoes", 4, "count")] if lines is None else lines
    return repo.add(
        name,
        servings,
        meals,
        extra.get("weather", Weather.ANY),
        extra.get("tags", ()),
        lines,
    )


def test_a_saved_recipe_comes_back_with_everything(repo):
    recipe_id = add(repo, tags=("quick", "kids"), weather=Weather.WARM)
    recipe = repo.get(recipe_id)
    assert (recipe.name, recipe.servings) == ("Tomato rice", 2)
    assert recipe.meal_types == (MealType.DINNER,)
    assert recipe.weather is Weather.WARM
    assert recipe.tags == ("quick", "kids")
    assert [(line.ingredient.name, line.quantity) for line in recipe.lines] == [
        ("rice", 200),
        ("tomatoes", 4),
    ]


def test_quantities_are_stored_in_the_base_unit(repo):
    recipe_id = add(repo, lines=[("flour", 1.5, "kg"), ("milk", 2, "cup")])
    quantities = {line.ingredient.name: line.quantity for line in repo.get(recipe_id).lines}
    assert quantities == {"flour": 1500, "milk": 480}


def test_new_ingredients_are_created_unchecked_and_without_calories(repo, db_url):
    add(repo)
    with StockRepository(db_url) as stock:
        found = {i.name: i for i in stock.ingredients()}
    assert found["rice"].base_unit is BaseUnit.GRAM
    assert found["tomatoes"].base_unit is BaseUnit.COUNT
    assert not found["rice"].reviewed and found["rice"].kcal is None


def test_an_existing_ingredient_is_reused_by_name_ignoring_case(repo, db_url):
    with StockRepository(db_url) as stock:
        stock.add("Rice", 1, "kg", Location.PANTRY)
        stock.set_ingredient_details(stock.ingredients()[0].id, 360, (FoodGroup.EGGS,), ())
    recipe_id = add(repo, lines=[("  RICE ", 100, "g")])
    ingredient = repo.get(recipe_id).lines[0].ingredient
    assert ingredient.reviewed and ingredient.kcal == 360
    with StockRepository(db_url) as stock:
        assert len(stock.ingredients()) == 1


def test_a_unit_of_another_family_than_the_ingredient_is_rejected(repo):
    add(repo, name="First", lines=[("chicken", 500, "g")])
    with pytest.raises(IncompatibleUnitError):
        add(repo, name="Second", lines=[("chicken", 500, "ml")])


def test_an_unknown_unit_is_rejected(repo):
    with pytest.raises(UnknownUnitError):
        add(repo, lines=[("rice", 1, "handful")])


def test_a_failed_save_leaves_nothing_behind_not_even_new_ingredients(repo, db_url):
    with pytest.raises(UnknownUnitError):
        add(repo, lines=[("rice", 100, "g"), ("salt", 1, "pinch")])
    assert repo.recipes() == []
    with StockRepository(db_url) as stock:
        assert stock.ingredients() == []


@pytest.mark.parametrize("quantity", [0, -5])
def test_a_quantity_must_be_above_zero(repo, quantity):
    with pytest.raises(ValueError, match="quantity"):
        add(repo, lines=[("rice", quantity, "g")])


def test_a_recipe_needs_at_least_one_ingredient(repo):
    with pytest.raises(ValueError, match="ingredient"):
        add(repo, lines=[])


def test_a_blank_ingredient_name_is_rejected(repo):
    with pytest.raises(ValueError, match="ingredient"):
        add(repo, lines=[("  ", 1, "g")])


def test_the_same_ingredient_listed_twice_is_added_together(repo):
    recipe_id = add(repo, lines=[("rice", 100, "g"), ("Rice", 0.1, "kg")])
    lines = repo.get(recipe_id).lines
    assert [(line.ingredient.name, line.quantity) for line in lines] == [("rice", 200)]


def test_two_recipes_cannot_share_a_name_ignoring_case(repo):
    add(repo, name="Soup")
    with pytest.raises(ValueError, match="already"):
        add(repo, name="soup")


def test_recipes_are_listed_by_name(repo):
    add(repo, name="Soup")
    add(repo, name="Apple pie", lines=[("apples", 4, "count")])
    assert [r.name for r in repo.recipes()] == ["Apple pie", "Soup"]


def test_updating_replaces_everything_and_keeps_the_id(repo):
    recipe_id = add(repo, tags=("old",))
    repo.update(
        recipe_id,
        "Tomato risotto",
        4,
        (MealType.LUNCH, MealType.DINNER),
        Weather.COLD,
        ("new",),
        [("rice", 300, "g")],
    )
    recipe = repo.get(recipe_id)
    assert (recipe.name, recipe.servings, recipe.weather) == ("Tomato risotto", 4, Weather.COLD)
    assert recipe.meal_types == (MealType.LUNCH, MealType.DINNER)
    assert recipe.tags == ("new",)
    assert [(line.ingredient.name, line.quantity) for line in recipe.lines] == [("rice", 300)]


def test_a_recipe_can_be_saved_again_under_its_own_name(repo):
    recipe_id = add(repo, name="Soup")
    repo.update(recipe_id, "soup", 3, (MealType.LUNCH,), Weather.ANY, (), [("rice", 1, "g")])
    assert repo.get(recipe_id).name == "soup"


def test_a_failed_update_changes_nothing(repo):
    recipe_id = add(repo)
    with pytest.raises(UnknownUnitError):
        repo.update(
            recipe_id, "Changed", 9, (MealType.SNACK,), Weather.WARM, ("x",), [("rice", 1, "pinch")]
        )
    recipe = repo.get(recipe_id)
    assert (recipe.name, recipe.servings) == ("Tomato rice", 2)
    assert len(recipe.lines) == 2


def test_deleting_a_recipe_keeps_its_ingredients(repo, db_url):
    recipe_id = add(repo)
    repo.delete(recipe_id)
    assert repo.recipes() == []
    with StockRepository(db_url) as stock:
        assert {i.name for i in stock.ingredients()} == {"rice", "tomatoes"}


def test_a_missing_recipe_is_reported(repo):
    assert repo.get(99) is None
    with pytest.raises(RecipeNotFoundError):
        repo.delete(99)
    with pytest.raises(RecipeNotFoundError):
        repo.update(99, "x", 1, (MealType.LUNCH,), Weather.ANY, (), [("rice", 1, "g")])


def test_a_recipe_sees_ingredient_details_saved_later(repo, db_url):
    recipe_id = add(repo)
    rice = next(
        line.ingredient for line in repo.get(recipe_id).lines if line.ingredient.name == "rice"
    )
    with StockRepository(db_url) as stock:
        stock.set_ingredient_details(rice.id, 360, (), ())
    fresh = next(
        line.ingredient for line in repo.get(recipe_id).lines if line.ingredient.name == "rice"
    )
    assert fresh.kcal == 360 and fresh.reviewed


def test_export_lists_recipes_with_their_lines(repo):
    add(repo, tags=("quick",), weather=Weather.COLD)
    [exported] = repo.export_data()
    assert exported["name"] == "Tomato rice"
    assert exported["servings"] == 2
    assert exported["meal_types"] == ["dinner"]
    assert exported["weather"] == "cold"
    assert exported["tags"] == ["quick"]
    assert exported["lines"] == [
        {"ingredient": "rice", "quantity": 200, "unit": "g", "alternative_group": None},
        {"ingredient": "tomatoes", "quantity": 4, "unit": "count", "alternative_group": None},
    ]


# --- where a recipe came from -----------------------------------------------------------------


def with_source(repo, url, name="Soup"):
    return repo.add(
        name, 2, (MealType.DINNER,), Weather.ANY, (), [("rice", 1, "g")], source_url=url
    )


def test_a_recipe_remembers_the_address_it_came_from(repo):
    recipe_id = with_source(repo, " https://example.com/soup ")
    assert repo.get(recipe_id).source_url == "https://example.com/soup"


def test_a_recipe_without_a_source_has_none(repo):
    assert repo.get(add(repo)).source_url is None
    assert repo.get(with_source(repo, "  ", "Other")).source_url is None


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "ftp://example.com", "example.com", "https://" + "x" * 600]
)
def test_a_source_must_be_a_web_address(repo, url):
    with pytest.raises(ValueError, match="web address"):
        with_source(repo, url)
    assert repo.recipes() == []


def test_updating_can_change_or_clear_the_source(repo):
    recipe_id = with_source(repo, "https://example.com/a")
    args = ("Soup", 2, (MealType.DINNER,), Weather.ANY, (), [("rice", 1, "g")])
    repo.update(recipe_id, *args, source_url="https://example.com/b")
    assert repo.get(recipe_id).source_url == "https://example.com/b"
    repo.update(recipe_id, *args)
    assert repo.get(recipe_id).source_url is None


def test_the_export_includes_the_source(repo):
    with_source(repo, "https://example.com/soup")
    assert repo.export_data()[0]["source_url"] == "https://example.com/soup"
