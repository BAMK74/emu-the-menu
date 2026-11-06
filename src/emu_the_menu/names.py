"""Comparing food names: ignoring case, hyphens, plurals and words that only describe the food."""

import re
from collections.abc import Iterable, Mapping


def stem(word: str) -> str:
    """A word without a plural ending, so "tomatoes" and "tomato" match."""
    word = word.lower().strip()
    for ending in ("es", "s"):
        if word.endswith(ending) and len(word) > len(ending) + 2:
            return word[: -len(ending)]
    return word


def _words(text: str) -> list[str]:
    return [stem(w) for w in text.replace("-", " ").split()]


def name_has_term(name: str, term: str) -> bool:
    """True if all the words of `term` appear in order, as whole words, in `name`.

    "peanut" is in "peanut butter" and "kiwis" is in "kiwi", but "pine" is not in "pineapple".
    """
    wanted, found = _words(term), _words(name)
    if not wanted:
        return False
    return any(found[i : i + len(wanted)] == wanted for i in range(len(found) - len(wanted) + 1))


# Words that describe how a food is cut, grown or sold without making it another food. Colours and
# "dried" are not here: red and green peppers, and dried and fresh herbs, are not interchangeable.
# "Ground" is dropped for spices only, and "grain" after fine or coarse (see `_core`).
_DESCRIBING = frozenset(
    stem(w)
    for w in """
    sea kosher fine finely flaky table coarse coarsely freshly fresh cracked chopped diced sliced
    minced grated shredded crushed roughly thinly lightly organic ripe raw large small medium
    extra virgin peeled deseeded trimmed washed halved quartered cubed torn beaten melted softened
    boneless skinless good quality
    """.split()
)
_SEASONING = frozenset({"salt", "pepper", "black"})
# "Ground" only describes a spice. With these it means minced meat, which is not the same food.
_MINCED = frozenset(stem(w) for w in "beef pork lamb turkey chicken veal meat sausage".split())


# Words that say how much, never which food: "a drizzle of olive oil" is olive oil, "some olive
# oil" and "a tomato" are olive oil and tomato, "a clove of garlic" is garlic. An amount word counts
# before "of" (or after "a"), so a "pinch pot" keeps its name, and "little" is left in "little gem
# lettuce".
_AMOUNTS = """
    drizzle pinch dash splash handful sprinkle glug knob sprig bunch dollop squeeze slug touch hint
    smidgen drop shake twist bit sliver couple lots plenty dusting sprinkling scattering smattering
    dab trickle lug smidge grind blob scoop spoonful slice clove stick head can tin jar bottle bag
    pack packet box cup glass teaspoon tablespoon wedge piece cube loaf rasher fillet bulb stalk
    block tub carton
"""
_AMOUNT = rf"(?:{'|'.join(_AMOUNTS.split())})(?:es|s)?\s+"
# "zest of 1 lemon", "juice of half a lemon": the fruit is what is needed. A measured "1 tsp lemon
# zest" is not here: it is its own ingredient, because a lemon is counted and zest is not.
_PART = (
    r"(?:(?:finely|freshly|coarsely|thinly)\s+)?(?:(?:grated|pared)\s+)?(?:zest|juice|rind|peel)"
    r"(?:\s+(?:and|&)\s+(?:zest|juice|rind|peel))?\s+of\s+"
    r"(?:(?:the|a|an|one|half|\d+(?:[./]\d+)?)\s+)*"
)
_QUANTITY_LEAD = re.compile(
    r"^\s*(?:(?:a|an|some|any|the)\s+(?:(?:few|little(?!\s+gem)|good|generous|large|small|big"
    r"|healthy|liberal|couple|bit)\s+(?:of\s+)?)*(?:" + _AMOUNT + r"(?:of\s+)?)?"
    r"|" + _AMOUNT + r"of\s+)?(?:" + _PART + r")?",
    re.IGNORECASE,
)


def strip_quantity(name: str) -> str:
    """A name without the words that only say how much, or the name itself if nothing is left."""
    stripped = _QUANTITY_LEAD.sub("", name, count=1)
    return stripped if stripped.strip() else name


def _core(words: list[str]) -> list[str]:
    """The words of a name that say which food it is, given its words stemmed and lower case."""
    ground_spice = "ground" in words and not _MINCED & set(words)
    kept = []
    for i, word in enumerate(words):
        if word in _DESCRIBING or word == "and":
            continue
        if word == "ground" and ground_spice:
            continue
        if word in ("grain", "grained") and i and words[i - 1] in ("fine", "coarse"):
            continue
        kept.append(word)
    return kept or words


def name_key(name: str) -> str:
    """What two spellings of one Ingredient have in common.

    "extra virgin olive oil" and "extra-virgin olive oil" match, so do "tomato" and "tomatoes", and
    so do salt, sea salt, sea-salt and "salt and pepper" (a seasoning pair counts as salt). It is
    not "red pepper" and "black pepper", or "olive oil" and "sesame oil".
    """
    kept = _core([stem(w) for w in re.findall(r"[a-z0-9]+", strip_quantity(name).lower())])
    if "salt" in kept and set(kept) <= _SEASONING:
        return "salt"
    if kept == ["pepper"]:  # a bare "pepper" in a recipe means black pepper
        return "black pepper"
    return " ".join(kept)


def clean_name(name: str) -> str:
    """The plain name to store an Ingredient under, written the way people say it.

    Words that only describe the food are dropped, so "freshly cracked black pepper" and a bare
    "pepper" are "black pepper", every kind of salt is "salt" and "extra-virgin olive oil" is
    "olive oil". Hyphens stay in real compounds ("gluten-free", "all-purpose") and go when they
    join a describing word ("sea-salt"). The food stays the same: `name_key` of the result is
    `name_key` of the input. Notes in brackets and other words are kept as they were.
    """
    key = name_key(name)
    if key in ("salt", "black pepper"):
        return key
    name = strip_quantity(name)
    tokens: list[str] = []
    for token in name.lower().split():
        first = token.split("-")[0]
        if "-" in token and stem(first) in _DESCRIBING:
            tokens.extend(part for part in token.split("-") if part)
        else:
            tokens.append(token)
    bare = [stem(re.sub(r"[^a-z0-9]", "", t)) for t in tokens]
    ground_spice = "ground" in bare and not _MINCED & set(bare)
    kept = []
    for i, (token, word) in enumerate(zip(tokens, bare, strict=True)):
        if word in _DESCRIBING or (word == "ground" and ground_spice):
            continue
        if word in ("grain", "grained") and i and bare[i - 1] in ("fine", "coarse"):
            continue
        kept.append(token)
    return " ".join(kept) if kept else " ".join(name.lower().split())


def canonical_name(
    name: str, known: Iterable[str] | Mapping[str, str | None], family: str | None = None
) -> str:
    """The name to store an Ingredient under.

    That is a known Ingredient with the same `name_key`, if its kind of unit (from `known` when it
    is a mapping of name to kind) is the same as `family`, and otherwise the plain name (see
    `clean_name`).
    """
    kinds = known if isinstance(known, Mapping) else dict.fromkeys(known)
    key = name_key(name)
    for existing, kind in kinds.items():
        if name_key(existing) == key and (family is None or kind is None or kind == family):
            return existing
    return clean_name(name)


# Words that tell one kind of a food from another, such as a colour or a type of oil. In "brown or
# green lentils" they are the two kinds, and the food is shared.
_KINDS = frozenset(
    """
    red green yellow orange brown white black purple light dark large small medium fresh dried
    frozen sweet salted unsalted cherry grape roma plum sunflower vegetable canola rapeseed olive
    sesame coconut peanut almond hazelnut cashew walnut pecan whole skim semi granulated caster
    """.split()
)


def split_alternatives(name: str) -> list[str]:
    """The ingredients a name offers a choice of: "water or vegetable broth" is two.

    Where each side is a whole ingredient they are kept as written. Where the sides are only two
    kinds of one food, the shared ending is added to both: "brown or green lentils" is "brown
    lentils" and "green lentils". A name with nothing to choose between is returned as it is.
    """
    text = name.strip()
    parts = [p.strip() for p in re.split(r"\s+(?:and/or|or)\s+", text, flags=re.IGNORECASE)]
    if len(parts) < 2 or not all(parts):
        return [text]
    tail = parts[-1].split()
    shared = tail[1:] if len(tail) >= 2 and tail[0].lower() in _KINDS else []
    result = []
    for part in parts[:-1]:
        words = part.split()
        # "all-natural salted peanut or almond butter": the last word is the kind, the rest of
        # the left side describes it.
        if shared and words and words[-1].lower() in _KINDS:
            part = " ".join([part, *shared])
        result.append(part)
    result.append(parts[-1])
    return list(dict.fromkeys(result))
