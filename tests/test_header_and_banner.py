"""The header every page shares (four links, the emu, the tab rows) and the season banner."""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.look import PHOTOS
from emu_the_menu.web import create_app


def off(code):
    return {"status": 0}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "emu.db", today=lambda: date(2026, 10, 10), fetch_product=off)
    return TestClient(app, follow_redirects=False)


def main_links(page):
    nav = re.search(r'<nav aria-label="Main">(.*?)</nav>', page, re.S).group(1)
    return re.findall(r'<a[^>]*href="([^"]+)"[^>]*>([^<]*)</a>', nav)


def current(page):
    return re.findall(r'<a[^>]*href="([^"]+)"[^>]*aria-current="page"', page)


def tab_row(page):
    found = re.search(r'<nav class="subnav"[^>]*>(.*?)</nav>', page, re.S)
    return re.findall(r'href="([^"]+)"', found.group(1)) if found else None


def test_the_header_has_four_links(client):
    links = main_links(client.get("/").text)
    assert links == [
        ("/", "Stock"),
        ("/menu", "Menu"),
        ("/recipes", "Recipes"),
        ("/settings", "Settings"),
    ]


@pytest.mark.parametrize(
    ("path", "main", "tabs", "tab"),
    [
        ("/", "/", None, None),
        ("/menu", "/menu", ["/menu", "/occasions"], "/menu"),
        ("/menus", "/menu", ["/menu", "/occasions"], "/menu"),
        ("/occasions", "/menu", ["/menu", "/occasions"], "/occasions"),
        ("/recipes", "/recipes", ["/recipes", "/ingredients"], "/recipes"),
        ("/ingredients", "/recipes", ["/recipes", "/ingredients"], "/ingredients"),
        ("/settings", "/settings", ["/settings", "/eaters"], "/settings"),
        ("/eaters", "/settings", ["/settings", "/eaters"], "/eaters"),
    ],
)
def test_each_page_marks_its_section_and_shows_its_tab_row(client, path, main, tabs, tab):
    page = client.get(path).text
    marked = current(page)
    assert marked[0] == main
    assert tab_row(page) == tabs
    assert (marked[1:] == [tab]) if tab else (len(marked) == 1)


def test_the_brand_is_the_emu_and_the_tab_icon_too(client):
    page = client.get("/").text
    assert '<link rel="icon" href="/static/emu.svg"' in page
    assert re.search(r'<a class="brand" href="/">\s*<svg class="logo"', page)
    assert client.get("/static/emu.svg").status_code == 200


def banner_line(page):
    """The banner line read back from its word spans."""
    return " ".join(re.findall(r'<span style="--w:\d+">([^<]+)</span>', page))


def test_the_front_pages_carry_the_full_banner_and_the_others_a_compact_one(client):
    for path in ("/", "/menu", "/menus"):
        page = client.get(path).text
        assert re.search(r'<section class="hero" id="hero">', page), path
        assert banner_line(page) == "Warm bowls and slow simmers"  # autumn, on 10 October
        assert "Pumpkin, apples and mushrooms are in season" in page
    for path in ("/recipes", "/ingredients", "/occasions", "/eaters", "/settings"):
        page = client.get(path).text
        assert re.search(r'<section class="hero compact" id="hero">', page), path
        assert banner_line(page) == "Warm bowls and slow simmers"
        assert "Pumpkin, apples and mushrooms" not in page


def test_the_banner_shows_the_six_photos_of_the_scene(client):
    page = client.get("/").text
    shown = re.findall(r'<img class="tile"[^>]*src="/static/photos/([a-z]+)\.jpg"', page)
    assert tuple(shown) == PHOTOS["autumn"]
    for name in shown:
        assert client.get(f"/static/photos/{name}.jpg").status_code == 200


def test_the_banner_invites_planning_except_on_the_menu_page(client):
    assert 'class="hero-cta" href="/menu"' in client.get("/").text
    assert 'class="hero-cta"' not in client.get("/menu").text


def test_the_banner_line_is_split_into_words_for_the_movement(client):
    page = client.get("/").text
    words = re.findall(r'<span class="w"><span style="--w: ?(\d+)">([^<]+)</span></span>', page)
    assert [w for _, w in words] == ["Warm", "bowls", "and", "slow", "simmers"]
    assert [int(i) for i, _ in words] == [0, 1, 2, 3, 4]


def test_every_photo_is_credited(client):
    page = client.get("/static/photos/credits.html").text
    assert page.count("<li>") == 24
    assert 'href="/static/photos/credits.html"' in client.get("/").text
