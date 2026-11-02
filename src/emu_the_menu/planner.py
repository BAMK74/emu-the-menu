"""Planning a Menu: which Recipe goes in which Meal. Pure functions, no database.

Every Meal is filled in the same way. First the hard rules throw Recipes out:

1. the Recipe is not tagged for that meal type;
2. it does not suit every Eater at the table (`suitability.assess`: allergies, diets and
   Ingredients nobody has reviewed);
3. it is still in its Cooldown (served less than `cooldown_days` ago);
4. it would take the Menu past its Shopping Allowance (too many Ingredients not in Stock).

The Recipes left are ranked by a weighted score: calorie fit, using Stock that expires soon, using
Stock at all, weather, and a penalty for dislikes. The weights are settings. The best one wins, and
ties are broken at random from a seed so that a Menu can be reshuffled and also repeated.

Stock is counted in amounts with expiry dates (`cooking.Pantry`): an Ingredient is covered when
what is held and has not expired by the day is at least what the Meal needs, and what a Meal
uses is not there for the next. A stock entry that is only a date (or None) means an amount that
is not tracked, always enough until then. Ingredients to buy are assumed to cover the rest of
the Menu once bought, so each counts once against the Shopping Allowance.
"""

import random
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from emu_the_menu.cooking import Held, Pantry, uses_for
from emu_the_menu.eaters import Eater
from emu_the_menu.recipe_repository import Recipe
from emu_the_menu.recipes import MealType, RecipeFacts, Weather, analyse
from emu_the_menu.repository import Ingredient
from emu_the_menu.suitability import assess


class Slot(StrEnum):
    """A place in the day that a Meal can fill, in the order the day goes."""

    BREAKFAST = "breakfast"
    MORNING_SNACK = "morning-snack"
    LUNCH = "lunch"
    AFTERNOON_SNACK = "afternoon-snack"
    DINNER = "dinner"
    EVENING_SNACK = "evening-snack"

    @property
    def label(self) -> str:
        return self.value.replace("-", " ").capitalize()

    @property
    def meal_type(self) -> MealType:
        """The kind of Recipe that fits this slot: the three snack slots all take snack Recipes."""
        return MealType.SNACK if self.value.endswith("snack") else MealType(self.value)


# How a day's calories are shared between slots. Only the chosen slots count, scaled to add up to
# the whole day, so a Menu of dinners alone gives each Eater their whole goal.
SLOT_SHARES = {
    Slot.BREAKFAST: 0.25,
    Slot.MORNING_SNACK: 0.08,
    Slot.LUNCH: 0.30,
    Slot.AFTERNOON_SNACK: 0.08,
    Slot.DINNER: 0.35,
    Slot.EVENING_SNACK: 0.08,
}
EXPIRING_WITHIN_DAYS = 7
SERVING_STEP = 0.25


@dataclass(frozen=True)
class Weights:
    calorie_fit: float = 3.0
    expiring: float = 2.0
    in_stock: float = 1.5
    weather: float = 1.0
    soft: float = 1.5  # the penalty for dislikes and, on a low-meat diet, meat


@dataclass(frozen=True)
class PlanSettings:
    shopping_allowance: int = 5  # Ingredients not in Stock that the whole Menu may need
    cooldown_days: int = 14
    weights: Weights = Weights()


DEFAULT_SETTINGS = PlanSettings()


@dataclass(frozen=True)
class Portion:
    eater: Eater
    servings: float
    kcal: float | None  # None when the Recipe's calories are not known


@dataclass(frozen=True)
class Meal:
    day: date
    slot: Slot
    recipe: Recipe | None
    portions: tuple[Portion, ...] = ()
    reason: str = ""  # why there is no Recipe, when there is none
    id: int | None = None  # the saved Meal this is, once the Menu is saved
    cooked_on: date | None = None
    short: str = ""  # what Stock could not cover when it was cooked

    @property
    def meal_type(self) -> MealType:
        return self.slot.meal_type

    @property
    def total_servings(self) -> float:
        """How many servings to cook so that every Eater gets their portion."""
        return sum(p.servings for p in self.portions)

    @property
    def scale(self) -> float:
        """How many times the Recipe as written that is, 0 for an empty Meal."""
        return self.total_servings / self.recipe.servings if self.recipe else 0


@dataclass(frozen=True)
class Menu:
    meals: tuple[Meal, ...]
    shopping: tuple[Ingredient, ...]  # Ingredients the chosen Recipes need that Stock lacks


@dataclass(frozen=True)
class _Candidate:
    recipe: Recipe
    facts: RecipeFacts
    soft_hits: int  # how many Eaters have something against it that does not block it
    ok: bool  # suits every Eater at the table


def _targets(eaters: Sequence[Eater], slot: Slot, slots: Collection[Slot]):
    total = sum(SLOT_SHARES[s] for s in slots)
    share = SLOT_SHARES[slot] / total
    return [eater.calorie_goal * share for eater in eaters]


def _portions(
    eaters: Sequence[Eater], targets: Sequence[float], kcal_per_serving: float | None
) -> tuple[Portion, ...]:
    portions = []
    for eater, target in zip(eaters, targets, strict=True):
        if kcal_per_serving:
            servings = max(
                SERVING_STEP, round(target / kcal_per_serving / SERVING_STEP) * SERVING_STEP
            )
            portions.append(Portion(eater, servings, servings * kcal_per_serving))
        else:
            portions.append(Portion(eater, 1.0, None))
    return tuple(portions)


def _covered(pantry: Pantry, slot: Sequence[Ingredient], need: Mapping[int, float], day: date):
    """Whether Stock holds enough of any one of a group of alternatives."""
    return any(pantry.available(i.id, day) >= need[i.id] - 1e-9 for i in slot)


def _score(
    candidate: _Candidate,
    day: date,
    pantry: Pantry,
    need: Mapping[int, float],
    targets: Sequence[float],
    weather: Weather | None,
    weights: Weights,
    eaters: int,
) -> float:
    recipe, facts = candidate.recipe, candidate.facts
    # A group of alternatives is one need, met if any of them is in Stock.
    slots = recipe.slots
    in_stock = sum(_covered(pantry, slot, need, day) for slot in slots) / len(slots)
    expiring = sum(
        any(
            pantry.expiring(i.id, day, EXPIRING_WITHIN_DAYS) and pantry.available(i.id, day) > 0
            for i in slot
        )
        for slot in slots
    ) / len(slots)
    kcal = facts.calories_per_serving
    if kcal and targets:
        # 1 when a serving is exactly an Eater's share of the day, falling to 0 at half or double.
        fit = sum(max(0.0, 1 - abs(kcal / t - 1)) for t in targets) / len(targets)
    else:
        fit = 0.5
    match = 0.0
    if weather is not None and recipe.weather is not Weather.ANY:
        match = 1.0 if recipe.weather is weather else -1.0
    return (
        weights.calorie_fit * fit
        + weights.expiring * expiring
        + weights.in_stock * in_stock
        + weights.weather * match
        - weights.soft * candidate.soft_hits / max(1, eaters)
    )


def plan(
    start: date,
    days: int,
    slots: Sequence[Slot],
    recipes: Sequence[Recipe],
    eaters: Sequence[Eater],
    stock: Mapping[int, Held],
    seed: int = 0,
    settings: PlanSettings = DEFAULT_SETTINGS,
    last_served: Mapping[int, date] | None = None,
    weather: Mapping[date, Weather | None] | None = None,
) -> Menu:
    """A Menu of `days` days from `start`, one Meal per day for each of `slots`.

    `stock` maps an Ingredient id to what is held: (quantity, expiry or None) lots, or just a
    date or None for an amount that is not tracked (see `cooking.Pantry`).
    `last_served` maps a Recipe id to when it was last served, for the Cooldown. `weather` maps a
    day to the weather expected, where known.
    """
    rng = random.Random(seed)
    pantry = Pantry(stock)
    served = dict(last_served or {})
    weather = weather or {}
    candidates = []
    for recipe in recipes:
        facts = analyse(recipe.for_analysis, recipe.servings)
        verdicts = [assess(recipe, facts, eater) for eater in eaters]
        candidates.append(
            _Candidate(
                recipe,
                facts,
                sum(1 for v in verdicts if v.soft),
                all(v.ok for v in verdicts),
            )
        )

    meals: list[Meal] = []
    missing: dict[int, Ingredient] = {}
    ordered_slots = [s for s in Slot if s in slots]
    for offset in range(days):
        day = start + timedelta(days=offset)
        for slot in ordered_slots:
            meal, newly_missing = _fill(
                day, slot, ordered_slots, candidates, eaters, pantry, served, missing,
                weather.get(day), settings, rng,
            )  # fmt: skip
            meals.append(meal)
            if meal.recipe is not None:
                served[meal.recipe.id] = day
                missing.update(newly_missing)
                _use_up(pantry, meal)
    return Menu(tuple(meals), tuple(sorted(missing.values(), key=lambda i: i.name)))


def portions_for(
    recipe: Recipe, eaters: Sequence[Eater], slot: Slot, slots: Collection[Slot]
) -> tuple[Portion, ...]:
    """What each Eater gets of `recipe` in `slot`, when the day is shared between `slots`."""
    kcal = analyse(recipe.for_analysis, recipe.servings).calories_per_serving
    return _portions(eaters, _targets(eaters, slot, slots), kcal)


def choices_for(recipes: Sequence[Recipe], eaters: Sequence[Eater], slot: Slot) -> list[Recipe]:
    """The Recipes that may be put in a Meal: tagged for the slot and suiting every Eater.

    These are the hard rules that depend only on the Meal. Cooldown and the Shopping Allowance
    are left to the person choosing.
    """
    found = []
    for recipe in recipes:
        if slot.meal_type not in recipe.meal_types:
            continue
        facts = analyse(recipe.for_analysis, recipe.servings)
        if all(assess(recipe, facts, eater).ok for eater in eaters):
            found.append(recipe)
    return sorted(found, key=lambda r: r.name.lower())


def _need(recipe: Recipe, scale: float) -> dict[int, float]:
    """How much of each Ingredient the Recipe needs when cooked `scale` times over."""
    return {line.ingredient.id: line.quantity * scale for line in recipe.lines}


def _use_up(pantry: Pantry, meal: Meal) -> None:
    """Take from the Pantry what the Meal uses, as far as it goes."""
    assert meal.recipe is not None
    held = {i.id: pantry.available(i.id, meal.day) for slot in meal.recipe.slots for i in slot}
    for use in uses_for(meal.recipe, meal.scale, held):
        pantry.take(use.ingredient.id, use.taken, meal.day)


def _fill(day, slot, slots, candidates, eaters, pantry, served, missing, weather, settings, rng):
    meal_type = slot.meal_type
    tagged = [c for c in candidates if meal_type in c.recipe.meal_types]
    if not tagged:
        return Meal(day, slot, None, reason=f"No recipes are tagged for {meal_type.value}"), {}
    suitable = [c for c in tagged if c.ok]
    if not suitable:
        return Meal(day, slot, None, reason="No recipe suits everyone at the table"), {}
    cooled = [
        c
        for c in suitable
        if c.recipe.id not in served or (day - served[c.recipe.id]).days >= settings.cooldown_days
    ]
    if not cooled:
        reason = f"Cooldown: every suitable recipe was served within {settings.cooldown_days} days"
        return Meal(day, slot, None, reason=reason), {}
    targets = _targets(eaters, slot, slots)
    affordable = []
    for c in cooled:
        # What this Recipe needs, at the servings this table would eat. A group of alternatives
        # needs nothing more if any one of them is covered, and otherwise one purchase, of the
        # first.
        servings = sum(p.servings for p in _portions(eaters, targets, c.facts.calories_per_serving))
        need = _need(c.recipe, servings / c.recipe.servings)
        needed = {
            group[0].id: group[0]
            for group in c.recipe.slots
            if not _covered(pantry, group, need, day)
        }
        if len(missing.keys() | needed.keys()) <= settings.shopping_allowance:
            affordable.append((c, needed, need))
    if not affordable:
        reason = (
            f"Shopping Allowance: every remaining recipe needs more than "
            f"{settings.shopping_allowance} ingredients that are not in stock"
        )
        return Meal(day, slot, None, reason=reason), {}
    scored = [
        (
            _score(c, day, pantry, need, targets, weather, settings.weights, len(eaters)),
            rng.random(),
            c,
            n,
        )
        for c, n, need in affordable
    ]
    _, _, best, needed = max(scored, key=lambda s: (s[0], s[1]))
    portions = _portions(eaters, targets, best.facts.calories_per_serving)
    return Meal(day, slot, best.recipe, portions), needed
