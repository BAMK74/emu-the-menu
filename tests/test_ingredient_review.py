import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import StockRepository
from emu_the_menu.web import create_app


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


@pytest.fixture
def client(db_path):
    app = create_app(
        db_path, today=lambda: date(2026, 11, 2), fetch_product=lambda code: {"status": 0}
    )
    return TestClient(app, follow_redirects=False)


def text_of(html):
    html = re.sub(r"<(style|script)\b.*?</\1>", "", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def recipe(db_path, name, ingredients):
    with RecipeRepository(db_path) as repo:
        repo.add(name, 2, [MealType.DINNER], Weather.ANY, (), [(i, 100, "g") for i in ingredients])


def ingredient(db_path, name):
    with StockRepository(db_path) as stock:
        return next(i for i in stock.ingredients() if i.name == name)


def plain_names(page):
    return re.findall(r'name="plain" value="\d+" checked> ([^<]+)<', page)


def row_names(page):
    return re.findall(r'<strong class="row-name">([^<]+)</strong>', page)


def review(client, query=""):
    return client.get("/ingredients/review" + query)


def post(client, **data):
    return client.post("/ingredients/review", data=data)


def seed(db_path):
    """Plain: garlic, salt. Needs a look: chicken breast, taco seasoning, zhoug."""
    recipe(db_path, "A", ["garlic", "salt", "chicken breast"])
    recipe(db_path, "B", ["garlic", "taco seasoning", "zhoug"])


# --- what the page shows -----------------------------------------------------------------------


def test_with_nothing_unchecked_it_says_everything_is_reviewed(client):
    assert "Everything is reviewed" in text_of(review(client).text)


def test_plain_looking_ingredients_are_one_ticked_group_and_the_rest_are_rows(client, db_path):
    seed(db_path)
    page = review(client).text
    assert sorted(plain_names(page)) == ["garlic", "salt"]
    assert sorted(row_names(page)) == ["chicken breast", "taco seasoning", "zhoug"]


def test_the_page_counts_what_is_left(client, db_path):
    seed(db_path)
    text = text_of(review(client).text)
    assert "5 left" in text and "2 look plain" in text and "3 need a look" in text


def test_the_most_used_come_first_in_each_group(client, db_path):
    recipe(db_path, "A", ["salt", "garlic", "zhoug", "chicken breast"])
    recipe(db_path, "B", ["garlic", "chicken breast"])
    page = review(client).text
    assert plain_names(page) == ["garlic", "salt"]
    assert row_names(page) == ["chicken breast", "zhoug"]


def test_each_row_says_why_it_needs_a_look(client, db_path):
    seed(db_path)
    text = text_of(review(client).text)
    assert "the name suggests poultry" in text
    assert "processed food" in text and "read the label" in text
    assert "not recognised as a plain food" in text


def test_rows_start_with_the_ticks_the_name_suggests(client, db_path):
    recipe(db_path, "A", ["chicken breast"])
    ing = ingredient(db_path, "chicken breast")
    page = review(client).text
    assert re.search(rf'name="fg_{ing.id}" value="poultry" checked', page)
    assert not re.search(rf'name="fg_{ing.id}" value="meat" checked', page)


def test_the_risky_filter_hides_the_plain_group(client, db_path):
    seed(db_path)
    page = review(client, "?show=risky").text
    assert plain_names(page) == []
    assert len(row_names(page)) == 3
    assert re.search(r'aria-current="true"[^>]*>[^<]*need a look', page)


def test_the_page_reminds_you_that_you_are_the_check(client, db_path):
    seed(db_path)
    text = text_of(review(client).text)
    assert "You are the safety check" in text and "always read the label" in text


# --- confirming the plain ones -----------------------------------------------------------------


def test_confirming_plain_ones_saves_them_as_checked_with_nothing_ticked(client, db_path):
    seed(db_path)
    ids = [ingredient(db_path, n).id for n in ("garlic", "salt")]
    response = post(client, plain=ids)
    assert response.status_code == 303
    for name in ("garlic", "salt"):
        saved = ingredient(db_path, name)
        assert saved.reviewed and saved.allergens == () and saved.food_groups == ()
    assert not ingredient(db_path, "zhoug").reviewed


def test_unticked_plain_ones_stay_unchecked(client, db_path):
    seed(db_path)
    post(client, plain=[ingredient(db_path, "garlic").id])
    assert ingredient(db_path, "garlic").reviewed
    assert not ingredient(db_path, "salt").reviewed


def test_the_server_refuses_to_confirm_something_that_does_not_look_plain(client, db_path):
    seed(db_path)
    chicken = ingredient(db_path, "chicken breast")
    post(client, plain=[chicken.id])
    assert not ingredient(db_path, "chicken breast").reviewed


def test_unknown_ids_are_ignored(client, db_path):
    seed(db_path)
    assert post(client, plain=[99999], confirm=[88888]).status_code == 303


def test_an_ingredient_someone_already_reviewed_is_not_overwritten(client, db_path):
    seed(db_path)
    garlic = ingredient(db_path, "garlic")
    client.post(f"/ingredients/{garlic.id}", data={"kcal": "149", "allergens": ["celery"]})
    post(client, plain=[garlic.id])
    saved = ingredient(db_path, "garlic")
    assert saved.kcal == 149 and [a.value for a in saved.allergens] == ["celery"]


# --- confirming rows ---------------------------------------------------------------------------


def test_confirming_a_row_saves_its_ticks_and_calories(client, db_path):
    seed(db_path)
    chicken = ingredient(db_path, "chicken breast")
    post(
        client,
        confirm=[chicken.id],
        **{f"kcal_{chicken.id}": "165", f"fg_{chicken.id}": ["poultry"]},
    )
    saved = ingredient(db_path, "chicken breast")
    assert saved.reviewed and saved.kcal == 165
    assert [g.value for g in saved.food_groups] == ["poultry"]
    assert saved.allergens == ()


def test_a_row_can_be_confirmed_with_allergens_and_a_comma_decimal(client, db_path):
    seed(db_path)
    zhoug = ingredient(db_path, "zhoug")
    post(
        client,
        confirm=[zhoug.id],
        **{f"kcal_{zhoug.id}": "10,5", f"al_{zhoug.id}": ["celery", "peanuts"]},
    )
    saved = ingredient(db_path, "zhoug")
    assert saved.kcal == 10.5
    assert sorted(a.value for a in saved.allergens) == ["celery", "peanuts"]


def test_rows_that_were_not_confirmed_are_left_alone_even_with_edits(client, db_path):
    seed(db_path)
    chicken, zhoug = ingredient(db_path, "chicken breast"), ingredient(db_path, "zhoug")
    post(
        client,
        confirm=[zhoug.id],
        **{f"fg_{chicken.id}": ["poultry"], f"kcal_{chicken.id}": "165"},
    )
    assert not ingredient(db_path, "chicken breast").reviewed
    assert ingredient(db_path, "chicken breast").kcal is None
    assert ingredient(db_path, "zhoug").reviewed


def test_a_bad_value_rejects_the_whole_save_and_keeps_what_was_typed(client, db_path):
    seed(db_path)
    chicken, zhoug = ingredient(db_path, "chicken breast"), ingredient(db_path, "zhoug")
    response = post(
        client,
        confirm=[chicken.id, zhoug.id],
        plain=[ingredient(db_path, "garlic").id],
        **{f"kcal_{chicken.id}": "165", f"kcal_{zhoug.id}": "lots"},
    )
    assert response.status_code == 400
    text = text_of(response.text)
    assert "zhoug" in text and "calories" in text.lower()
    assert 'value="165"' in response.text
    for name in ("chicken breast", "zhoug", "garlic"):
        assert not ingredient(db_path, name).reviewed


# --- paging, moving on, finding it -------------------------------------------------------------


def test_rows_are_shown_twenty_at_a_time(client, db_path):
    recipe(db_path, "Big", [f"mystery{chr(97 + n // 5)}{chr(97 + n % 5)}x" for n in range(25)])
    assert len(row_names(review(client).text)) == 20
    assert len(row_names(review(client, "?page=2").text)) == 5
    assert 'href="/ingredients/review?page=2"' in review(client).text


def test_a_junk_page_number_means_the_first_page(client, db_path):
    seed(db_path)
    assert len(row_names(review(client, "?page=abc").text)) == 3
    assert len(row_names(review(client, "?page=-4").text)) == 3


def test_saving_returns_to_the_same_view_and_says_how_many_were_saved(client, db_path):
    seed(db_path)
    ids = [ingredient(db_path, n).id for n in ("garlic", "salt")]
    response = post(client, plain=ids, show="risky", page="1")
    assert response.headers["location"] == "/ingredients/review?show=risky&done=2"
    assert "Saved 2 ingredients" in text_of(review(client, "?show=risky&done=2").text)


def test_odd_values_in_the_return_address_are_dropped(client, db_path):
    seed(db_path)
    response = post(client, plain=[ingredient(db_path, "garlic").id], show="//evil", page="x")
    assert response.headers["location"] == "/ingredients/review?done=1"


def test_the_ingredients_page_offers_the_review_only_while_something_is_unchecked(client, db_path):
    seed(db_path)
    assert 'href="/ingredients/review"' in client.get("/ingredients").text
    client.post(
        "/ingredients/review",
        data={"plain": [ingredient(db_path, n).id for n in ("garlic", "salt")]},
    )
    for name in ("chicken breast", "taco seasoning", "zhoug"):
        client.post(f"/ingredients/{ingredient(db_path, name).id}", data={"kcal": ""})
    assert 'href="/ingredients/review"' not in client.get("/ingredients").text


def test_the_review_does_not_clash_with_an_ingredient_page(client, db_path):
    seed(db_path)
    assert review(client).status_code == 200
    assert client.get(f"/ingredients/{ingredient(db_path, 'garlic').id}").status_code == 200


# --- groups that share a likely answer ---------------------------------------------------------


def group_names(page):
    return re.findall(r'name="grp" value="\d+" checked> ([^<]+)<', page)


def seed_groups(db_path):
    recipe(db_path, "Mac", ["butter", "cheddar cheese", "zhoug"])
    recipe(db_path, "Chick", ["chicken breast", "mystery herb mix"])


def test_ingredients_that_share_a_likely_answer_are_grouped(client, db_path):
    seed_groups(db_path)
    page = review(client).text
    assert sorted(group_names(page)) == ["butter", "cheddar cheese"]
    assert sorted(row_names(page)) == ["chicken breast", "mystery herb mix", "zhoug"]


def test_a_group_says_what_confirming_it_saves(client, db_path):
    seed_groups(db_path)
    text = text_of(review(client).text)
    assert "Probably dairy, milk" in text
    assert "Confirming saves them as containing: dairy, milk" in text


def test_confirming_a_group_saves_its_tags_for_the_ticked_ingredients(client, db_path):
    seed_groups(db_path)
    butter, cheese = ingredient(db_path, "butter"), ingredient(db_path, "cheddar cheese")
    response = post(client, grp=[butter.id, cheese.id], grp_fg=["dairy"], grp_al=["milk"])
    assert response.status_code == 303
    for name in ("butter", "cheddar cheese"):
        saved = ingredient(db_path, name)
        assert saved.reviewed
        assert [g.value for g in saved.food_groups] == ["dairy"]
        assert [a.value for a in saved.allergens] == ["milk"]
    assert not ingredient(db_path, "zhoug").reviewed


def test_an_unticked_member_of_a_group_stays_unchecked(client, db_path):
    seed_groups(db_path)
    butter = ingredient(db_path, "butter")
    post(client, grp=[butter.id], grp_fg=["dairy"], grp_al=["milk"])
    assert ingredient(db_path, "butter").reviewed
    assert not ingredient(db_path, "cheddar cheese").reviewed


def test_confirming_a_group_never_overwrites_a_reviewed_ingredient(client, db_path):
    seed_groups(db_path)
    butter = ingredient(db_path, "butter")
    client.post(f"/ingredients/{butter.id}", data={"kcal": "717", "allergens": ["milk", "soy"]})
    post(client, grp=[butter.id], grp_fg=["dairy"], grp_al=["milk"])
    assert sorted(a.value for a in ingredient(db_path, "butter").allergens) == ["milk", "soy"]


def test_a_bad_tag_in_a_group_is_rejected(client, db_path):
    seed_groups(db_path)
    butter = ingredient(db_path, "butter")
    response = post(client, grp=[butter.id], grp_al=["not-an-allergen"])
    assert response.status_code == 400
    assert not ingredient(db_path, "butter").reviewed


def test_the_most_used_groups_come_first(client, db_path):
    recipe(db_path, "A", ["butter", "cheddar cheese", "salmon fillet", "tuna steak"])
    recipe(db_path, "B", ["salmon fillet", "tuna steak"])
    recipe(db_path, "C", ["salmon fillet", "tuna steak"])
    headings = re.findall(r"Probably ([a-z, -]+) \(", text_of(review(client).text))
    assert headings[0].startswith("fish")


# --- reviewing one recipe's ingredients --------------------------------------------------------


def test_a_recipe_filter_shows_only_that_recipes_ingredients(client, db_path):
    seed(db_path)
    page = review(client, "?recipe=1").text
    assert sorted(plain_names(page)) == ["garlic", "salt"]
    assert row_names(page) == ["chicken breast"]
    assert "Checking: A" in text_of(page)


def test_an_unknown_recipe_filter_shows_everything(client, db_path):
    seed(db_path)
    assert len(row_names(review(client, "?recipe=999").text)) == 3
    assert len(row_names(review(client, "?recipe=abc").text)) == 3


def test_saving_keeps_the_recipe_filter(client, db_path):
    seed(db_path)
    response = post(client, plain=[ingredient(db_path, "garlic").id], recipe="1")
    assert response.headers["location"] == "/ingredients/review?recipe=1&done=1"


def test_the_recipes_page_links_an_unchecked_recipe_to_its_review(client, db_path):
    seed(db_path)
    page = client.get("/recipes").text
    assert 'href="/ingredients/review?recipe=1"' in page
    assert "check the 3 ingredients" in text_of(page)


def test_a_fully_checked_recipe_has_no_review_link(client, db_path):
    recipe(db_path, "Plain", ["garlic"])
    client.post(f"/ingredients/{ingredient(db_path, 'garlic').id}", data={"kcal": ""})
    assert "/ingredients/review?recipe=" not in client.get("/recipes").text


def test_the_menu_links_a_not_checked_meal_to_the_review_of_its_recipe(client, db_path):
    from emu_the_menu.eaters import Category, EaterRepository

    with EaterRepository(db_path) as eaters:
        eaters.add("Sam", Category.MAN)
    recipe(db_path, "Mystery", ["zhoug"])
    page = client.get("/menu?go=1&days=1&eater=1&meal_types=dinner").text
    assert 'href="/ingredients/review?recipe=1"' in page
