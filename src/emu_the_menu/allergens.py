"""The 14 allergens that EU food law requires to be declared (Regulation 1169/2011, Annex II).

Eaters tick the ones they must avoid, and Ingredients carry the allergens they contain, so a
Recipe can be checked against a person without relying on words matching: tahini has no "sesame"
in its name, but it is tagged sesame.
"""

from collections.abc import Iterable
from enum import StrEnum


class Allergen(StrEnum):
    CELERY = "celery"
    CRUSTACEANS = "crustaceans"
    EGGS = "eggs"
    FISH = "fish"
    GLUTEN = "gluten"
    LUPIN = "lupin"
    MILK = "milk"
    MOLLUSCS = "molluscs"
    MUSTARD = "mustard"
    PEANUTS = "peanuts"
    SESAME = "sesame"
    SOY = "soy"
    SULPHITES = "sulphites"
    TREE_NUTS = "tree-nuts"

    @property
    def label(self) -> str:
        """The name with examples, for checkboxes."""
        return _LABELS[self]

    @property
    def short_label(self) -> str:
        """The name alone, such as "Tree nuts", for lists."""
        return self.label.split(" (")[0]


_LABELS: dict[Allergen, str] = {
    Allergen.CELERY: "Celery",
    Allergen.CRUSTACEANS: "Crustaceans (prawns, crab, lobster)",
    Allergen.EGGS: "Eggs",
    Allergen.FISH: "Fish",
    Allergen.GLUTEN: "Gluten (wheat, rye, barley, oats)",
    Allergen.LUPIN: "Lupin",
    Allergen.MILK: "Milk",
    Allergen.MOLLUSCS: "Molluscs (mussels, squid, snails)",
    Allergen.MUSTARD: "Mustard",
    Allergen.PEANUTS: "Peanuts",
    Allergen.SESAME: "Sesame",
    Allergen.SOY: "Soy",
    Allergen.SULPHITES: "Sulphites",
    Allergen.TREE_NUTS: "Tree nuts (almonds, hazelnuts, walnuts)",
}


def parse_allergens(values: Iterable[str]) -> tuple[Allergen, ...]:
    """Read ticked checkbox values. Blanks are ignored, repeats are dropped and the order is
    the standard one. Raises ValueError for anything not on the list."""
    chosen: set[Allergen] = set()
    for value in values:
        value = value.strip()
        if not value:
            continue
        try:
            chosen.add(Allergen(value))
        except ValueError:
            raise ValueError("Choose allergens from the list") from None
    return tuple(a for a in Allergen if a in chosen)
