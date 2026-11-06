"""Which drawing goes with a food, a Recipe or a place in the day (the art is in static/art.svg).

A drawing is picked from the words in a name, so it is only decoration: it never says anything
about allergens or diets and nothing depends on it.
"""

from collections.abc import Iterable

from emu_the_menu.names import name_has_term

# Checked in this order, so "chicken stock" is meat and "peanut butter" is not dairy.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fish", ("fish", "salmon", "tuna", "cod", "prawn", "shrimp", "haddock", "trout", "sardine")),
    ("meat", ("chicken", "beef", "pork", "lamb", "mince", "bacon", "sausage", "ham", "turkey")),
    ("egg", ("egg",)),
    ("dairy", ("milk", "cheese", "yogurt", "yoghurt", "butter", "cream", "cheddar", "mozzarella")),
    ("bread", ("bread", "bun", "roll", "toast", "wrap", "tortilla", "bagel")),
    ("grain", ("rice", "pasta", "spaghetti", "noodle", "oat", "flour", "couscous", "quinoa")),
    ("fruit", ("apple", "banana", "orange", "lemon", "lime", "berry", "strawberry", "grape", "pear")),
    ("herb", ("basil", "parsley", "coriander", "mint", "thyme", "rosemary", "spinach", "kale")),
    (
        "veg",
        (
            "carrot", "broccoli", "potato", "onion", "garlic", "tomato", "pepper", "courgette",
            "cucumber", "pea", "bean", "cabbage", "lettuce", "mushroom", "pumpkin", "leek",
        ),
    ),
)  # fmt: skip

# For a Recipe, the main ingredient decides, so look for the heartiest first.
_RECIPE_ORDER = ("meat", "fish", "egg", "dairy", "grain", "bread", "veg", "herb", "fruit")

_SLOTS = {
    "breakfast": "sunrise",
    "morning-snack": "cookie",
    "lunch": "sandwich",
    "afternoon-snack": "fruit",
    "dinner": "pot",
    "evening-snack": "cookie",
}


def food_icon(name: str) -> str:
    """The drawing for an ingredient or stock item, or the plain jar when nothing fits."""
    for icon, words in _RULES:
        if any(name_has_term(name, word) for word in words):
            return icon
    return "jar"


def recipe_icon(ingredient_names: Iterable[str]) -> str:
    """The drawing for a Recipe, from its main ingredient, or a bowl when nothing fits."""
    found = {food_icon(n) for n in ingredient_names} - {"jar"}
    for icon in _RECIPE_ORDER:
        if icon in found:
            return icon
    return next(iter(sorted(found)), "bowl")


def slot_icon(slot) -> str:
    return _SLOTS.get(str(getattr(slot, "value", slot)), "bowl")
