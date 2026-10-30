"""The food groups a Diet can exclude, and that Ingredients are tagged with.

A strict diet (pescatarian, vegetarian, vegan) excludes whole groups, so tagging each Ingredient
with the groups it belongs to is what lets a Recipe be checked against a person's diet.
"""

from collections.abc import Iterable
from enum import StrEnum


class FoodGroup(StrEnum):
    MEAT = "meat"  # red meat and cured or processed meat
    POULTRY = "poultry"
    FISH = "fish"  # fish and seafood
    DAIRY = "dairy"
    EGGS = "eggs"
    HONEY = "honey"

    @property
    def label(self) -> str:
        """The name with examples, for tooltips."""
        return _LABELS[self]

    @property
    def short_label(self) -> str:
        """The name alone, such as "Fish and seafood", for pills and lists."""
        return self.label.split(" (")[0]


_LABELS: dict[FoodGroup, str] = {
    FoodGroup.MEAT: "Meat (beef, pork, lamb, bacon, ham)",
    FoodGroup.POULTRY: "Poultry (chicken, turkey, duck)",
    FoodGroup.FISH: "Fish and seafood (fish, prawns, mussels)",
    FoodGroup.DAIRY: "Dairy (milk, cheese, butter, yoghurt)",
    FoodGroup.EGGS: "Eggs",
    FoodGroup.HONEY: "Honey",
}


def parse_food_groups(values: Iterable[str]) -> tuple[FoodGroup, ...]:
    """Read ticked checkbox values. Blanks are ignored, repeats are dropped and the order is
    the standard one. Raises ValueError for anything not on the list."""
    chosen: set[FoodGroup] = set()
    for value in values:
        value = value.strip()
        if not value:
            continue
        try:
            chosen.add(FoodGroup(value))
        except ValueError:
            raise ValueError("Choose food groups from the list") from None
    return tuple(g for g in FoodGroup if g in chosen)
