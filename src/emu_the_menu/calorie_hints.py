"""Rough calories for common foods, offered as a starting point when filling in calories in bulk.

These are typical figures for the plain food (mostly the raw or dry state), picked by Claude from
general nutrition tables. They are suggestions, never saved by themselves: a person looks at each
and confirms it, and a branded product's label always wins. A name gets a figure only when it is
exactly a listed food, ignoring plurals and words that only describe it (see `names.name_key`), so
"carrot cake" or "tomato ketchup" get nothing. Figures are per 100 g, per 100 ml or per piece,
matching `ingredients.kcal_basis`.
"""

from emu_the_menu.names import name_key
from emu_the_menu.units import BaseUnit

# name | kcal per 100 g | kcal per 100 ml | kcal per piece   ("-" for no figure)
_TABLE = """
rice | 360 | - | -
pasta | 360 | - | -
spaghetti | 360 | - | -
couscous | 365 | - | -
quinoa | 370 | - | -
oats | 380 | - | -
rolled oats | 380 | - | -
flour | 360 | - | -
plain flour | 360 | - | -
cornflour | 360 | - | -
breadcrumbs | 395 | - | -
bread | 265 | - | 80
sugar | 387 | - | -
brown sugar | 380 | - | -
honey | 304 | 420 | -
maple syrup | 260 | 340 | -
onion | 40 | - | 44
red onion | 40 | - | 44
garlic | 149 | - | -
ginger | 80 | - | -
carrot | 41 | - | 25
potato | 77 | - | 130
sweet potato | 86 | - | 110
tomato | 18 | - | 22
cucumber | 15 | - | 45
courgette | 17 | - | 33
zucchini | 17 | - | 33
aubergine | 25 | - | -
eggplant | 25 | - | -
broccoli | 34 | - | -
cauliflower | 25 | - | -
spinach | 23 | - | -
kale | 49 | - | -
lettuce | 15 | - | -
cabbage | 25 | - | -
celery | 16 | - | -
leek | 61 | - | -
mushroom | 22 | - | -
bell pepper | 31 | - | 37
red bell pepper | 31 | - | 37
green bell pepper | 20 | - | 24
red pepper | 31 | - | 37
green pepper | 20 | - | 24
pumpkin | 26 | - | -
butternut squash | 45 | - | -
peas | 81 | - | -
sweetcorn | 86 | - | -
spring onion | 32 | - | -
beetroot | 43 | - | -
green beans | 31 | - | -
asparagus | 20 | - | -
avocado | 160 | - | 240
apple | 52 | - | 95
banana | 89 | - | 105
orange | 47 | - | 62
lemon | 29 | - | 17
lime | 30 | - | 20
strawberry | 32 | - | -
blueberry | 57 | - | -
raspberry | 52 | - | -
grapes | 69 | - | -
pear | 57 | - | 100
mango | 60 | - | -
pineapple | 50 | - | -
peach | 39 | - | 60
raisins | 299 | - | -
dates | 282 | - | -
egg | 143 | - | 72
tofu | 76 | - | -
tempeh | 193 | - | -
minced beef | 250 | - | -
beef mince | 250 | - | -
pork mince | 263 | - | -
bacon | 417 | - | -
salmon | 208 | - | -
cod | 82 | - | -
prawns | 99 | - | -
milk | 59 | 61 | -
whole milk | 59 | 61 | -
semi-skimmed milk | 45 | 46 | -
skimmed milk | 35 | 35 | -
double cream | 340 | 330 | -
single cream | 190 | 185 | -
heavy cream | 340 | 330 | -
yogurt | 61 | - | -
yoghurt | 61 | - | -
greek yogurt | 97 | - | -
butter | 717 | - | -
cheddar | 403 | - | -
cheese | 400 | - | -
mozzarella | 280 | - | -
parmesan | 431 | - | -
feta | 264 | - | -
cream cheese | 342 | - | -
sour cream | 198 | - | -
ricotta | 174 | - | -
olive oil | 884 | 810 | -
vegetable oil | 884 | 810 | -
sunflower oil | 884 | 810 | -
sesame oil | 884 | 810 | -
coconut oil | 862 | 790 | -
mayonnaise | 680 | 650 | -
peanut butter | 588 | - | -
chocolate | 546 | - | -
cocoa powder | 228 | - | -
almonds | 579 | - | -
walnuts | 654 | - | -
cashews | 553 | - | -
peanuts | 567 | - | -
pine nuts | 673 | - | -
sesame seeds | 573 | - | -
sunflower seeds | 584 | - | -
chia seeds | 486 | - | -
salt | 0 | - | -
black pepper | 251 | - | -
paprika | 282 | - | -
cumin | 375 | - | -
cinnamon | 247 | - | -
turmeric | 312 | - | -
chili powder | 282 | - | -
oregano | 265 | - | -
curry powder | 325 | - | -
basil | 23 | - | -
parsley | 36 | - | -
coriander | 23 | - | -
mint | 44 | - | -
thyme | 101 | - | -
tomato paste | 82 | - | -
passata | 32 | 32 | -
soy sauce | 53 | 60 | -
coconut milk | 197 | 190 | -
vegetable stock | 5 | 5 | -
chicken stock | 5 | 5 | -
vinegar | 18 | 20 | -
lemon juice | 22 | 22 | -
red wine | 85 | 85 | -
white wine | 82 | 82 | -
baking powder | 53 | - | -
yeast | 325 | - | -
water | 0 | 0 | -
"""


def _number(text: str) -> float | None:
    text = text.strip()
    return None if text == "-" else float(text)


HINTS: dict[str, tuple[float | None, float | None, float | None]] = {}
for _line in _TABLE.strip().splitlines():
    _name, _g, _ml, _piece = (part.strip() for part in _line.split("|"))
    HINTS[name_key(_name)] = (_number(_g), _number(_ml), _number(_piece))


def suggest_kcal(name: str, base: BaseUnit) -> float | None:
    """A rough figure for the food, in the unit the Ingredient is measured in, or None."""
    found = HINTS.get(name_key(name))
    if found is None:
        return None
    per_g, per_ml, per_piece = found
    return {BaseUnit.GRAM: per_g, BaseUnit.MILLILITRE: per_ml, BaseUnit.COUNT: per_piece}[base]
