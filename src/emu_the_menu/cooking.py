"""What cooking a Meal uses up from Stock. Pure functions, no database.

A Recipe's amounts are scaled to the servings actually cooked. Stock is taken as far as it goes and
the rest is reported as short: cooking is never blocked, because the ingredients may have been
bought and not yet added to Stock. Of a group of alternatives only one is used: the first that
covers the whole amount, else the one with the most in Stock, else the first.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from emu_the_menu.recipe_repository import Recipe
from emu_the_menu.repository import Ingredient

if TYPE_CHECKING:
    from emu_the_menu.planner import Meal

_EPSILON = 1e-9

# What is held of one Ingredient: (quantity, expiry or None) lots, a date or None for an amount
# that is not tracked (always enough until then), or a plain number with no expiry.
Held = Sequence[tuple[float, date | None]] | date | float | int | None


class Pantry:
    """What is in Stock by Ingredient id, with amounts and expiry dates, used up as meals are made.

    A lot that has expired by a day is not there on that day. Taking uses the earliest expiry first.
    """

    def __init__(self, stock: Mapping[int, Held]):
        self._lots: dict[int, list[list]] = {}
        for ingredient_id, held in stock.items():
            if held is None or isinstance(held, date):
                lots = [[math.inf, held]]
            elif isinstance(held, int | float):
                lots = [[float(held), None]]
            else:
                lots = [[float(q), e] for q, e in held if q > 0]
            self._lots[ingredient_id] = sorted(
                lots, key=lambda lot: (lot[1] is None, lot[1] or date.max)
            )

    def _usable(self, ingredient_id: int, day: date):
        return [
            lot
            for lot in self._lots.get(ingredient_id, [])
            if lot[0] > _EPSILON and (lot[1] is None or lot[1] >= day)
        ]

    def available(self, ingredient_id: int, day: date) -> float:
        return sum(lot[0] for lot in self._usable(ingredient_id, day))

    def expiring(self, ingredient_id: int, day: date, within: int) -> bool:
        limit = day + timedelta(days=within)
        return any(
            lot[1] is not None and lot[1] <= limit for lot in self._usable(ingredient_id, day)
        )

    def take(self, ingredient_id: int, quantity: float, day: date) -> None:
        remaining = quantity
        for lot in self._usable(ingredient_id, day):
            used = min(lot[0], remaining)
            if lot[0] != math.inf:
                lot[0] -= used
            remaining -= used
            if remaining <= _EPSILON:
                break


@dataclass(frozen=True)
class Use:
    ingredient: Ingredient
    needed: float  # in the Ingredient's base unit
    taken: float  # what Stock can give, never more than needed

    @property
    def short(self) -> float:
        return max(0.0, self.needed - self.taken)


def uses_for(recipe: Recipe, scale: float, held: Mapping[int, float]) -> list[Use]:
    """The Ingredients to take from Stock for `scale` times the Recipe.

    `held` maps an Ingredient id to the total in Stock, in its base unit.
    """
    if scale < 0:
        raise ValueError("scale cannot be negative")
    if scale == 0:
        return []
    amounts: dict[int, float] = {}
    for line in recipe.lines:
        amounts[line.ingredient.id] = line.quantity * scale
    uses = []
    for slot in recipe.slots:
        options = [(i, amounts[i.id]) for i in slot]
        covering = [o for o in options if held.get(o[0].id, 0) >= o[1] - _EPSILON]
        if covering:
            chosen = covering[0]
        else:
            chosen = max(options, key=lambda o: held.get(o[0].id, 0))  # first on a tie
        ingredient, needed = chosen
        uses.append(Use(ingredient, needed, min(needed, max(0.0, held.get(ingredient.id, 0)))))
    return uses


def shopping_amounts(
    meals: Sequence["Meal"], stock: Mapping[int, Held]
) -> list[tuple[Ingredient, float]]:
    """What to buy for the Meals still to cook, by name, with amounts in the base unit.

    Stock is shared out between the Meals in the order given, so what one Meal takes is not there
    for the next, and Stock that has expired by a Meal's day does not count. Meals already cooked
    have taken their share and need nothing more.
    """
    pantry = Pantry(stock)
    to_buy: dict[int, tuple[Ingredient, float]] = {}
    for meal in meals:
        if meal.recipe is None or meal.cooked_on is not None:
            continue
        scale = meal.total_servings / meal.recipe.servings
        held = {i.id: pantry.available(i.id, meal.day) for slot in meal.recipe.slots for i in slot}
        for use in uses_for(meal.recipe, scale, held):
            pantry.take(use.ingredient.id, use.taken, meal.day)
            if use.short > _EPSILON:
                bought = to_buy.get(use.ingredient.id, (use.ingredient, 0.0))[1]
                to_buy[use.ingredient.id] = (use.ingredient, bought + use.short)
    return sorted(to_buy.values(), key=lambda pair: pair[0].name)
