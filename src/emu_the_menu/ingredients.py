"""What Emu the Menu knows about an Ingredient besides its name: calories, food groups, allergens.

Calories are entered by hand, per 100 g or 100 ml for things measured by weight or volume and per
piece for things counted, and the barcode lookup can fill them in. Food groups and allergens are
ticked by a person. The suggestions below only pre-tick likely ones from the name, to save typing,
and they are never saved until the person confirms them. They are not a safety check: a missing
suggestion means nothing, and an allergen can hide in an ingredient whose name gives no hint.
"""

import re
import unicodedata
from dataclasses import dataclass

from emu_the_menu.allergens import Allergen
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.names import split_alternatives, stem
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
        " worcestershire"
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
        " yogurt butter ghee buttermilk quark skyr parmigiano reggiano pecorino halloumi paneer"
        " gruyere emmental edam",
        (FoodGroup.DAIRY,),
        (Allergen.MILK,),
        _NOT_DAIRY,
    ),
    _rule("egg", (FoodGroup.EGGS,), (Allergen.EGGS,)),
    _rule("honey", (FoodGroup.HONEY,)),
    _rule(
        "wheat barley rye spelt semolina couscous bulgur pasta spaghetti penne macaroni noodle"
        " fettuccine linguine tagliatelle lasagne lasagna rigatoni fusilli farfalle orzo"
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
    # Typical contents of common foods. Only hints: the person still ticks what is really in it.
    _rule(
        "flour",
        (),
        (Allergen.GLUTEN,),
        frozenset(
            "corn rice almond coconut gluten chickpea cassava tapioca potato buckwheat".split()
        ),
    ),
    _rule("oat oats", (), (Allergen.GLUTEN,), frozenset({"gluten"})),
    _rule("granola muesli cereal cracker biscuit malt", (), (Allergen.GLUTEN,)),
    _rule(
        "wine vinegar balsamic sherry sulphite raisin sultana prune",
        (),
        (Allergen.SULPHITES,),
    ),
    _rule(
        "mayonnaise mayo meringue custard",
        (FoodGroup.EGGS,),
        (Allergen.EGGS,),
        frozenset({"vegan"}),
    ),
    _rule("stock broth bouillon", (), (Allergen.CELERY,)),
    _rule("hummus", (), (Allergen.SESAME,)),
]


def _ascii(name: str) -> str:
    """The name in lower case without accents, so "jalapeño" is read as "jalapeno"."""
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()


def suggest_tags(name: str) -> Suggestion:
    """Food groups and allergens that an Ingredient with this name probably has.

    Matches whole words only, so "ham" is not found in "hamper". Only a hint to check.
    """
    words = {_stem(w) for w in re.findall(r"[a-z]+", _ascii(name))}
    groups: set[FoodGroup] = set()
    allergens: set[Allergen] = set()
    for keywords, rule_groups, rule_allergens, unless in _RULES:
        if keywords & words and not (unless and {_stem(w) for w in unless} & words):
            groups.update(rule_groups)
            allergens.update(rule_allergens)
    return Suggestion(
        tuple(g for g in FoodGroup if g in groups), tuple(a for a in Allergen if a in allergens)
    )


# --- is this ingredient plainly harmless? ------------------------------------------------------

PLAIN = "plain"
NEEDS_LOOK = "needs-look"

# Single unprocessed plant foods, spices and basics. A name made only of these words (and the
# describing words below) is shown as plain-looking, so many can be confirmed in one go. This is
# only a way to sort a long list: a person still confirms every one, and it is deliberately short.
# Anything with a word not on the list needs a look, and so does every hint of an allergen.
_PLAIN_FOODS = frozenset(
    """
    onion garlic carrot tomato potato cucumber spinach lettuce romaine kale cabbage broccoli
    cauliflower zucchini courgette pepper capsicum aubergine eggplant beet beetroot radish leek
    shallot scallion mushroom corn pea bean chickpea lentil quinoa rice avocado lemon lime apple
    banana pear peach orange berry strawberry blueberry raspberry grape pineapple mango melon
    olive pumpkin squash ginger basil parsley cilantro coriander mint thyme rosemary oregano sage
    dill chive bay cumin paprika cinnamon turmeric nutmeg clove cayenne chili chilli salt sugar
    water oil juice zest vegetable sunflower canola rapeseed seed asparagus
    jasmine basmati long grain steamed uncooked cranberry date fig cherry plum apricot grapefruit
    kiwi watermelon kidney pinto cannellini haricot vert chile jalapeno serrano fresno habanero
    iceberg sprout choy bok pak chard collard microgreen russet yukon piper maris wedge strip
    chia flax flaxseed meal kalamata nicoise heirloom med eating sum bundle rainbow roasted
    toasted salted unsalted lacinato flat thai beluga kernel reserved cob on caraway smoked bird
    birds eye herb fruit caster baking soda bi carb bicarbonate pie
    """.split()
)
_PLAIN_WORDS = frozenset(
    """
    fresh dried frozen ground chopped diced sliced minced grated shredded crushed whole raw
    cooked ripe large small medium baby cherry roma red green yellow white black brown purple
    sweet hot bell extra virgin sea kosher fine flaky freshly cracked flake leaf leaves head bunch
    sprig stem peeled pitted packed organic english or and of a the ice granulated
    """.split()
)
_PLAIN_VOCABULARY = _PLAIN_FOODS | _PLAIN_WORDS | frozenset({"slice", "pepper"})


def _singulars(word: str) -> set[str]:
    """The ways a plural can be written in the singular, so "limes" finds "lime"."""
    forms = {word}
    if word.endswith("s"):
        forms.add(word[:-1])
    if word.endswith("es"):
        forms.add(word[:-2])
    if word.endswith("ies"):
        forms.add(word[:-3] + "y")
    return forms


def _is_plain_word(word: str) -> bool:
    return bool(_singulars(word) & _PLAIN_VOCABULARY)


_PROCESSED = frozenset(
    stem(w)
    for w in """
    sauce stock broth bouillon paste mix seasoning blend dressing mayonnaise mayo mustard ketchup
    vinegar bread pasta noodle flour cereal granola muesli cracker chip tortilla sausage
    chocolate cocoa candy syrup jam wine beer spread margarine powder extract
    """.split()
)


@dataclass(frozen=True)
class Judgement:
    kind: str  # PLAIN or NEEDS_LOOK
    reason: str = ""  # why it needs a look


def lists_alternatives(name: str) -> bool:
    """True if the name offers a choice ("water or vegetable broth"): more than one food."""
    return len(split_alternatives(name)) > 1


def judge(name: str) -> Judgement:
    """Sort an Ingredient name into plain-looking or needing a look, with the reason.

    Plain means every word is a basic plant food or a word that only describes it. It is never
    plain when the name hints at a food group or allergen. A guess from the name, not a check.
    """
    if lists_alternatives(name):
        return Judgement(
            NEEDS_LOOK,
            "it lists alternatives, which are more than one ingredient: tick everything"
            " that any of them contains",
        )
    raw = re.findall(r"[a-z]+", re.sub(r"\([^)]*\)", " ", _ascii(name)))
    words = [stem(w) for w in raw]
    hint = suggest_tags(name)
    if hint.food_groups or hint.allergens:
        tags = [g.short_label.lower() for g in hint.food_groups]
        tags += [a.short_label.lower() for a in hint.allergens if a.short_label.lower() not in tags]
        reason = "the name suggests " + ", ".join(tags)
        if _PROCESSED & set(words):
            reason += "; a processed food, so read the label"
        return Judgement(NEEDS_LOOK, reason)
    if set(words) == {"oil"}:
        return Judgement(NEEDS_LOOK, "an unspecified oil, which could be peanut or sesame")
    if raw and all(_is_plain_word(w) for w in raw):
        return Judgement(PLAIN)
    if _PROCESSED & set(words):
        return Judgement(
            NEEDS_LOOK, "a processed food, where hidden allergens are possible: read the label"
        )
    return Judgement(NEEDS_LOOK, "not recognised as a plain food")
