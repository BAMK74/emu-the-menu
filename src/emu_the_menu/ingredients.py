"""What Emu the Menu knows about an Ingredient besides its name: calories, food groups, allergens.

Calories are entered by hand, per 100 g or 100 ml for things measured by weight or volume and per
piece for things counted, and the barcode lookup can fill them in. Food groups and allergens are
ticked by a person. The suggestions below only pre-tick likely ones from the name, to save typing,
and they are never saved until the person confirms them. They are not a safety check: a missing
suggestion means nothing, and an allergen can hide in an ingredient whose name gives no hint.
"""

import re
from dataclasses import dataclass

from emu_the_menu.allergens import Allergen
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.units import BaseUnit

_MAX_KCAL = {BaseUnit.GRAM: 950, BaseUnit.MILLILITRE: 950, BaseUnit.COUNT: 3000}


def kcal_basis(base: BaseUnit) -> str:
    """What the calories of an Ingredient are measured against: "per 100 g", "per piece"..."""
    return "per piece" if base is BaseUnit.COUNT else f"per 100 {base.value}"


def check_kcal(kcal: float | None, base: BaseUnit) -> float | None:
    """Raise ValueError unless the calories are blank or between 0 and the ceiling for the unit."""
    if kcal is None:
        return None
    if kcal < 0:
        raise ValueError("The calories cannot be below 0")
    if kcal > _MAX_KCAL[base]:
        raise ValueError(f"The calories {kcal_basis(base)} cannot be more than {_MAX_KCAL[base]}")
    return float(kcal)


def parse_kcal(text: str, base: BaseUnit) -> float | None:
    """Read typed calories such as "539", "53.5" or "53,5". Blank means not known yet."""
    text = text.strip()
    if not text:
        return None
    if not re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        raise ValueError("Enter the calories as a plain number such as 53.5, or leave it blank")
    return check_kcal(float(text.replace(",", ".")), base)


def calories_in(quantity: float, base: BaseUnit, kcal: float | None) -> float | None:
    """The calories in `quantity` of an Ingredient (in its base unit), or None if not known."""
    if kcal is None:
        return None
    return quantity * kcal if base is BaseUnit.COUNT else quantity / 100 * kcal


@dataclass(frozen=True)
class Suggestion:
    food_groups: tuple[FoodGroup, ...] = ()
    allergens: tuple[Allergen, ...] = ()


# A plant milk or a nut butter is not dairy, so these words cancel the dairy rule.
_NOT_DAIRY = frozenset(
    "coconut almond oat soy soya rice plant peanut cocoa shea nut hazelnut cashew seed".split()
)

# (words, food groups, allergens, words that cancel the rule)
_Rule = tuple[frozenset[str], tuple[FoodGroup, ...], tuple[Allergen, ...], frozenset[str]]


def _rule(words: str, groups=(), allergens=(), unless=frozenset()) -> _Rule:
    return frozenset(_stem(w) for w in words.split()), groups, allergens, unless


def _stem(word: str) -> str:
    """Drop a plural s, so "prawns" matches "prawn"."""
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


_RULES: list[_Rule] = [
    _rule("chicken turkey duck goose poultry", (FoodGroup.POULTRY,)),
    _rule(
        "beef pork lamb veal mutton venison bacon ham sausage salami chorizo prosciutto pancetta"
        " gammon",
        (FoodGroup.MEAT,),
    ),
    _rule(
        "salmon tuna cod haddock trout mackerel sardine anchovy anchovie herring fish seafood"
        " plaice pollock tilapia halibut",
        (FoodGroup.FISH,),
        (Allergen.FISH,),
    ),
    _rule(
        "prawn shrimp crab lobster crayfish langoustine", (FoodGroup.FISH,), (Allergen.CRUSTACEANS,)
    ),
    _rule(
        "mussel oyster clam squid calamari octopus scallop snail",
        (FoodGroup.FISH,),
        (Allergen.MOLLUSCS,),
    ),
    _rule(
        "milk cheese cheddar mozzarella parmesan feta ricotta mascarpone gouda brie cream yoghurt"
        " yogurt butter ghee buttermilk quark skyr",
        (FoodGroup.DAIRY,),
        (Allergen.MILK,),
        _NOT_DAIRY,
    ),
    _rule("egg", (FoodGroup.EGGS,), (Allergen.EGGS,)),
    _rule("honey", (FoodGroup.HONEY,)),
    _rule(
        "wheat barley rye spelt semolina couscous bulgur pasta spaghetti penne macaroni noodle"
        " bread breadcrumb seitan",
        (),
        (Allergen.GLUTEN,),
    ),
    _rule("peanut", (), (Allergen.PEANUTS,)),
    _rule("almond hazelnut walnut cashew pistachio pecan macadamia", (), (Allergen.TREE_NUTS,)),
    _rule("soy soya tofu edamame miso", (), (Allergen.SOY,)),
    _rule("sesame tahini", (), (Allergen.SESAME,)),
    _rule("mustard", (), (Allergen.MUSTARD,)),
    _rule("celery celeriac", (), (Allergen.CELERY,)),
    _rule("lupin", (), (Allergen.LUPIN,)),
]


def suggest_tags(name: str) -> Suggestion:
    """Food groups and allergens that an Ingredient with this name probably has.

    Matches whole words only, so "ham" is not found in "hamper". Only a hint to check.
    """
    words = {_stem(w) for w in re.findall(r"[a-z]+", name.lower())}
    groups: set[FoodGroup] = set()
    allergens: set[Allergen] = set()
    for keywords, rule_groups, rule_allergens, unless in _RULES:
        if keywords & words and not (unless and {_stem(w) for w in unless} & words):
            groups.update(rule_groups)
            allergens.update(rule_allergens)
    return Suggestion(
        tuple(g for g in FoodGroup if g in groups), tuple(a for a in Allergen if a in allergens)
    )
