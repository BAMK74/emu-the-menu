import pytest

from emu_the_menu import eaters
from emu_the_menu.food_groups import FoodGroup, parse_food_groups


def test_the_food_groups_are_the_ones_diets_can_exclude():
    assert [g.value for g in FoodGroup] == ["meat", "poultry", "fish", "dairy", "eggs", "honey"]


def test_eaters_still_offers_the_same_food_groups():
    assert eaters.FoodGroup is FoodGroup


def test_every_food_group_has_a_label_with_examples_and_a_short_name():
    for group in FoodGroup:
        assert group.label and group.short_label
    assert FoodGroup.MEAT.short_label == "Meat"
    assert "beef" in FoodGroup.MEAT.label
    assert FoodGroup.FISH.short_label == "Fish and seafood"
    assert "chicken" in FoodGroup.POULTRY.label
    assert len({g.short_label for g in FoodGroup}) == len(FoodGroup)


def test_parse_food_groups_keeps_the_standard_order_and_drops_repeats():
    assert parse_food_groups(["honey", "meat", "honey", "fish"]) == (
        FoodGroup.MEAT,
        FoodGroup.FISH,
        FoodGroup.HONEY,
    )


def test_parse_food_groups_ignores_blanks_and_spaces():
    assert parse_food_groups(["", " dairy ", "  "]) == (FoodGroup.DAIRY,)
    assert parse_food_groups([]) == ()


@pytest.mark.parametrize("bad", ["beef", "Meat", "seafood", "dairy2"])
def test_parse_food_groups_rejects_anything_not_on_the_list(bad):
    with pytest.raises(ValueError, match="food group"):
        parse_food_groups(["meat", bad])
