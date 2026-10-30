"""Whether a Recipe suits an Eater. Pure functions, no database.

These are the hard rules that Menu generation will start from, so they lean towards "no":

- A ticked allergen in any Ingredient blocks the Recipe.
- A word from the Eater's other allergies in an Ingredient's name blocks it too. A name cannot show
  what a product hides, so such an Eater is always told to read the labels.
- A diet blocks the food groups it excludes.
- An Ingredient nobody has reviewed is unknown. For an Eater with any allergy or a diet that bans
  food groups, a Recipe containing one is blocked, never treated as safe. For everyone else it is
  only a caution.
- Dislikes and low-meat are soft: they never block, they are noted so a ranking can use them.
"""

from dataclasses import dataclass

from emu_the_menu.eaters import Eater
from emu_the_menu.names import name_has_term
from emu_the_menu.recipe_repository import Recipe
from emu_the_menu.recipes import RecipeFacts


@dataclass(frozen=True)
class Suitability:
    blocked: tuple[str, ...]  # why this Eater must not be given the Recipe
    cautions: tuple[str, ...]  # things to check that do not block
    soft: tuple[str, ...]  # dislikes and low-meat points, for ranking

    @property
    def ok(self) -> bool:
        return not self.blocked


def _unique(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def assess(recipe: Recipe, facts: RecipeFacts, eater: Eater) -> Suitability:
    """Whether `recipe` (with its `facts`) suits `eater`, and why not."""
    blocked: list[str] = []
    cautions: list[str] = []
    soft: list[str] = []
    names = [line.ingredient.name for line in recipe.lines]

    def providers(tag) -> str:
        """The Ingredients carrying a food group or allergen, for the reason's wording."""
        found = [
            line.ingredient.name
            for line in recipe.lines
            if tag in line.ingredient.food_groups or tag in line.ingredient.allergens
        ]
        return ", ".join(found)

    for allergen in eater.allergens:
        if allergen in facts.allergens:
            blocked.append(f"contains {allergen.short_label.lower()} ({providers(allergen)})")

    for term in eater.allergies:
        matching = [name for name in names if name_has_term(name, term)]
        if matching:
            blocked.append(f"contains {term} ({', '.join(matching)})")
        else:
            cautions.append(f"{term} is an allergy here, so check the labels for {term}")

    for group in eater.diet.excluded_groups:
        if group in facts.food_groups:
            blocked.append(f"contains {group.short_label.lower()} ({providers(group)})")

    if facts.unchecked:
        unchecked = ", ".join(facts.unchecked)
        strict = bool(eater.allergens or eater.allergies or eater.diet.excluded_groups)
        message = f"not checked: {unchecked}"
        if strict:
            blocked.append(f"{message}, so it may contain something this person cannot have")
        else:
            cautions.append(f"{message}, so allergens and food groups are unknown")

    if eater.diet.limits_meat and any(g.value in ("meat", "poultry") for g in facts.food_groups):
        soft.append("contains meat, and this person eats little meat")

    for term in eater.dislikes:
        matching = [name for name in names if name_has_term(name, term)]
        if matching:
            soft.append(f"{term} is disliked ({', '.join(matching)})")

    return Suitability(_unique(blocked), _unique(cautions), _unique(soft))
