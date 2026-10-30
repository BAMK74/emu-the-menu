"""Reading a Recipe from a web page.

Most recipe sites publish the recipe as structured data (schema.org, in a JSON-LD script). Only
the facts are taken: the name, the servings, the ingredient lines and some tags. The method and
the photos are not copied. Ingredient lines are free text ("1 1/2 cups flour"), so each one is read
as well as it can be, and any line that cannot be read is handed back untouched for a person to fix.
Nothing here is trusted: the result only fills in the Recipe form for a person to check and save.
"""

import json
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

from emu_the_menu.names import stem
from emu_the_menu.recipes import MAX_SERVINGS, MAX_TAG_LENGTH, MAX_TAGS


class NoRecipeFoundError(ValueError):
    """The page has no recipe with ingredients that Emu can read."""


@dataclass(frozen=True)
class IngredientLine:
    raw: str
    name: str
    quantity: float | None  # in `unit`, None if the line has no usable amount
    unit: str | None

    @property
    def readable(self) -> bool:
        return bool(self.name) and self.quantity is not None and self.unit is not None


@dataclass(frozen=True)
class ImportedRecipe:
    name: str  # blank if the page gave none
    servings: int | None
    lines: tuple[str, ...]  # the ingredient lines as written
    tags: tuple[str, ...]


# --- one ingredient line ----------------------------------------------------------------------

_FRACTIONS = {"½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4", "⅛": "1/8"}

# What a unit word means: (Emu unit, factor), where a factor turns the amount into that unit.
_UNITS: dict[str, tuple[str, float]] = {}


def _units(words: str, unit: str, factor: float = 1) -> None:
    for word in words.split():
        _UNITS[word] = (unit, factor)


_units("g gram grams gramme grammes", "g")
_units("kg kilo kilos kilogram kilograms", "kg")
_units("ml millilitre millilitres milliliter milliliters", "ml")
_units("l litre litres liter liters", "l")
_units("tsp tsps teaspoon teaspoons", "tsp")
_units("tbsp tbsps tbs tablespoon tablespoons", "tbsp")
_units("cup cups", "cup")
_units("oz ounce ounces", "g", 28.35)
_units("lb lbs pound pounds", "g", 453.6)
_units("clove cloves slice slices piece pieces stick sticks", "count")
_units("ear ears stem stems stalk stalks rib ribs bundle bundles", "count")
_units("bunch bunches", "bunch")
_units("head heads", "head")
_CONTAINERS = frozenset("can cans tin tins jar jars pack packs packet packets bag bags".split())
_CONTAINERS |= frozenset("bottle bottles package packages box boxes".split())
# Amounts that cannot be turned into a number, so the line is left for a person.
_VAGUE = frozenset("pinch pinches dash dashes handful handfuls splash sprig sprigs knob".split())
_SIZES = frozenset("small medium large big".split())
# Words that describe the measure rather than the food: "2 heaped tablespoons yoghurt".
_MODIFIERS = frozenset("heaped heaping scant level generous rounded packed lightly tightly".split())

_NUMBER = r"\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?"
_START = re.compile(rf"(?P<q>{_NUMBER})(?:\s*(?:-|–|to)\s*(?:{_NUMBER}))?\s*", re.I)
# A size written before a container, such as "15-ounce can", "400g tin" or "6-inch tortillas".
_INNER = re.compile(rf"(?P<q>{_NUMBER})\s*-?\s*(?P<u>[a-zA-Z]+)\.?\s*")
_HEADING = re.compile(
    r"^(?:optional|recommended|any of the following|for|to serve|garnish)[a-z ]*:\s*", re.I
)
_MAX_NAME = 60
# A word that comes before the amount: "scant 1/2 tsp", "about 2 cups".
_LEAD_WORD = re.compile(
    r"^(?:scant|about|approx\.?|approximately|roughly|generous|heaped|heaping)\s+(?=\d|[½⅓⅔¼¾⅛])",
    re.I,
)


def _number(text: str) -> float:
    text = text.strip()
    if " " in text:  # "1 1/2"
        whole, fraction = text.split(None, 1)
        return int(whole) + _number(fraction)
    if "/" in text:
        top, bottom = text.split("/")
        return int(top) / int(bottom)
    return float(text.replace(",", "."))


def _strip_brackets(text: str) -> str:
    """Remove bracketed notes, even nested or left unclosed, and the marks sites add."""
    previous = None
    while previous != text:
        previous, text = text, re.sub(r"\([^()]*\)", " ", text)
    return re.sub(r"[()*$]", "", text)


def _drop_alternative_amount(text: str) -> str:
    """Drop the second way of writing an amount: "250g / 10 oz pasta" leaves "pasta"."""
    text = text.strip()
    if not text.startswith("/"):
        return text
    text = text[1:].strip()
    if match := _START.match(text):
        rest = text[match.end() :]
        first = rest.split()[0].lower().rstrip(".") if rest.split() else ""
        if first in _UNITS or first in _CONTAINERS:
            return rest.split(None, 1)[1] if len(rest.split(None, 1)) > 1 else ""
        return rest
    return text


def _clean_name(text: str) -> str:
    text = _drop_alternative_amount(_strip_brackets(text))
    text = re.split(r",| - | – |;| plus | \+ ", text)[0]
    text = re.sub(r"\s+(to|for)\s+(taste|serve|serving|garnish|decorate)\b.*", "", text.lower())
    words = text.split()
    while words and words[0] in {"of", "a", "an", "each"}:
        words.pop(0)
    while words and (words[0] in _SIZES or words[0].startswith("medium-to")):
        words.pop(0)
    if "garlic" in words:  # "garlic cloves", "clove of garlic"
        words = [w for w in words if w not in {"clove", "cloves", "of"}]
    name = " ".join(words)
    if len(name) > _MAX_NAME:
        name = name[:_MAX_NAME].rsplit(" ", 1)[0]
    return name


def parse_ingredient_line(text: str) -> IngredientLine:
    """Read a line such as "1 1/2 cups flour, sifted" as flour, 1.5, cup."""
    raw = text
    text = " ".join(text.split())
    for symbol, fraction in _FRACTIONS.items():
        text = re.sub(rf"(\d){symbol}", rf"\1 {fraction}", text).replace(symbol, fraction)
    text = _HEADING.sub("", text)
    text = _LEAD_WORD.sub("", text)
    match = _START.match(text)
    if not match:
        # "a pinch of saffron", "salt to taste": no number, so nothing to measure.
        words = text.split()
        rest = " ".join(words[1:]) if words and words[0].lower() in {"a", "an"} else text
        first = rest.split()[0].lower() if rest.split() else ""
        return IngredientLine(
            raw, _clean_name(rest[len(first) :] if first in _VAGUE else rest), None, None
        )
    amount = _number(match.group("q"))
    rest = text[match.end() :]
    measure = None  # the size of one container, when the line says so
    if paren := re.match(r"\(([^()]*)\)\s*", rest):
        measure, rest = _measure(paren.group(1)), rest[paren.end() :]
    rest = re.sub(r"^x\s*(?=\d)", "", rest)  # "2 x 400g tins"
    if measure is None and (inner := _INNER.match(rest)):
        word = inner.group("u").lower()
        if word == "inch":  # "6-inch tortillas": the size is not an amount
            rest = rest[inner.end() :]
        elif word in _UNITS and _UNITS[word][0] != "count":
            measure = (
                _scaled(_number(inner.group("q")), _UNITS[word][1]),
                _UNITS[word][0],
            )
            rest = rest[inner.end() :]
    words = rest.split()
    while words and words[0].lower() in _MODIFIERS:
        words.pop(0)
    first = words[0].lower().rstrip(".") if words else ""
    if first in _VAGUE:
        return IngredientLine(raw, _clean_name(" ".join(words[1:])), None, None)
    if first in _CONTAINERS:
        name = _clean_name(" ".join(words[1:]))
        if measure:
            return _line(raw, name, amount * measure[0], measure[1])
        return _line(raw, name, amount, "count")
    if first in _UNITS:
        unit, factor = _UNITS[first]
        rest_words = words[1:]
        if rest_words and rest_words[0].lower().rstrip(".") in _CONTAINERS:
            rest_words = rest_words[1:]  # "15 oz can black beans"
        return _line(raw, _clean_name(" ".join(rest_words)), _scaled(amount, factor), unit)
    return _line(raw, _clean_name(" ".join(words)), amount, "count")  # "3 eggs", "1 onion"


def _scaled(amount: float, factor: float) -> float:
    """An amount in its new unit, rounded to one decimal only if it was converted."""
    return amount if factor == 1 else round(amount * factor, 1)


def _measure(text: str) -> tuple[float, str] | None:
    """An amount in brackets such as "400 g", as (quantity, unit), if it can be read."""
    match = _START.match(" ".join(text.split()))
    if not match:
        return None
    word = text.strip()[match.end() :].split()
    unit = _UNITS.get(word[0].lower().rstrip(".")) if word else None
    if not unit or unit[0] == "count":
        return None
    return _scaled(_number(match.group("q")), unit[1]), unit[0]


def _line(raw: str, name: str, quantity: float, unit: str) -> IngredientLine:
    if quantity <= 0 or not name:
        return IngredientLine(raw, name, None, None)
    return IngredientLine(raw, name, round(quantity, 3), unit)


def match_ingredient(written: str, known: list[str]) -> str:
    """The name of a known Ingredient that `written` means, ignoring plurals, else `written`."""
    wanted = stem(written)
    for name in known:
        if name.lower() == written.lower() or stem(name) == wanted:
            return name
    return written


# --- finding the recipe in a page -------------------------------------------------------------


class _JsonLd(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self._inside = False

    def handle_starttag(self, tag, attrs):
        self._inside = (
            tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json"
        )
        if self._inside:
            self.blocks.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._inside = False

    def handle_data(self, data):
        if self._inside:
            self.blocks[-1] += data


def _recipes_in(node):
    """Every schema.org Recipe inside parsed JSON-LD, whatever way the site nested it."""
    if isinstance(node, list):
        for item in node:
            yield from _recipes_in(item)
    elif isinstance(node, dict):
        kind = node.get("@type")
        if kind == "Recipe" or (isinstance(kind, list) and "Recipe" in kind):
            yield node
        yield from _recipes_in(node.get("@graph", []))


def _text(value) -> str:
    """Plain text from a value that may carry markup, character codes and stray spaces."""
    return " ".join(unescape(re.sub(r"<[^>]+>", "", str(value))).replace("\xa0", " ").split())


def _as_list(value) -> list:
    return value if isinstance(value, list) else [] if value is None else [value]


def _servings(value) -> int | None:
    for item in _as_list(value):
        if match := re.search(r"\d+", str(item)):
            number = int(match.group())
            return number if 1 <= number <= MAX_SERVINGS else None
    return None


def _tags(recipe: dict) -> tuple[str, ...]:
    found: dict[str, None] = {}
    for key in ("keywords", "recipeCategory"):
        for item in _as_list(recipe.get(key)):
            for part in re.split(r"[,;\n]", _text(item)):
                tag = part.strip().lower()
                if tag and len(tag) <= MAX_TAG_LENGTH:
                    found.setdefault(tag)
    return tuple(found)[:MAX_TAGS]


def extract_recipe(html: str) -> ImportedRecipe:
    """The first Recipe with ingredients in a page. Raises NoRecipeFoundError if there is none."""
    parser = _JsonLd()
    parser.feed(html)
    for block in parser.blocks:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for recipe in _recipes_in(data):
            lines = tuple(
                line for item in _as_list(recipe.get("recipeIngredient")) if (line := _text(item))
            )
            if lines:
                return ImportedRecipe(
                    _text(recipe.get("name", "")),
                    _servings(recipe.get("recipeYield")),
                    lines,
                    _tags(recipe),
                )
    raise NoRecipeFoundError("No recipe with ingredients was found on that page")
