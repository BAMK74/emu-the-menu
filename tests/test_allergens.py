import pytest

from emu_the_menu.allergens import Allergen, parse_allergens


def test_there_are_the_fourteen_eu_allergens():
    assert [a.value for a in Allergen] == [
        "celery",
        "crustaceans",
        "eggs",
        "fish",
        "gluten",
        "lupin",
        "milk",
        "molluscs",
        "mustard",
        "peanuts",
        "sesame",
        "soy",
        "sulphites",
        "tree-nuts",
    ]


def test_every_allergen_has_a_label():
    for allergen in Allergen:
        assert allergen.label
    assert Allergen.PEANUTS.label == "Peanuts"
    assert "wheat" in Allergen.GLUTEN.label  # says what gluten means
    assert "almonds" in Allergen.TREE_NUTS.label


def test_labels_are_unique_so_people_can_tell_them_apart():
    labels = [a.label for a in Allergen]
    assert len(set(labels)) == len(labels)


def test_parse_allergens_keeps_the_standard_order_and_drops_repeats():
    assert parse_allergens(["tree-nuts", "milk", "milk", "celery"]) == (
        Allergen.CELERY,
        Allergen.MILK,
        Allergen.TREE_NUTS,
    )


def test_parse_allergens_ignores_blanks_and_surrounding_spaces():
    assert parse_allergens(["", " milk ", "  "]) == (Allergen.MILK,)
    assert parse_allergens([]) == ()


@pytest.mark.parametrize("bad", ["kiwi", "peanut", "tree nuts", "MILK2"])
def test_parse_allergens_rejects_anything_not_on_the_list(bad):
    with pytest.raises(ValueError, match="allergen"):
        parse_allergens(["milk", bad])
