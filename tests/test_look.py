"""The season and festive look that colours the pages."""

from datetime import date

import pytest

from emu_the_menu.look import (
    AUTO,
    COPY,
    PHOTOS,
    Look,
    LookChoice,
    Season,
    festive_of,
    is_southern,
    look_for,
    parse_look_choice,
    season_of,
)


@pytest.mark.parametrize(
    ("day", "north", "south"),
    [
        (date(2026, 1, 15), Season.WINTER, Season.SUMMER),
        (date(2026, 2, 28), Season.WINTER, Season.SUMMER),
        (date(2026, 3, 1), Season.SPRING, Season.AUTUMN),
        (date(2026, 5, 31), Season.SPRING, Season.AUTUMN),
        (date(2026, 6, 1), Season.SUMMER, Season.WINTER),
        (date(2026, 8, 31), Season.SUMMER, Season.WINTER),
        (date(2026, 9, 1), Season.AUTUMN, Season.SPRING),
        (date(2026, 11, 30), Season.AUTUMN, Season.SPRING),
        (date(2026, 12, 1), Season.WINTER, Season.SUMMER),
    ],
)
def test_the_season_follows_the_month_and_flips_in_the_south(day, north, south):
    assert season_of(day) is north
    assert season_of(day, southern=True) is south


@pytest.mark.parametrize(
    ("day", "festive"),
    [
        (date(2026, 10, 23), None),
        (date(2026, 10, 24), "halloween"),
        (date(2026, 10, 31), "halloween"),
        (date(2026, 11, 1), None),
        (date(2026, 12, 14), None),
        (date(2026, 12, 15), "christmas"),
        (date(2026, 12, 26), "christmas"),
        (date(2026, 12, 27), None),
    ],
)
def test_festive_colours_have_short_windows(day, festive):
    assert festive_of(day) == festive


def test_following_the_date_gives_the_season_and_any_festive_overlay():
    look = look_for(date(2026, 12, 20))
    assert look == Look(Season.WINTER, "christmas") and look.scene == "christmas"
    assert look_for(date(2026, 7, 4)).scene == "summer"


def test_a_pinned_season_keeps_the_festive_overlay_unless_it_is_off():
    pinned = look_for(date(2026, 12, 20), LookChoice("summer", festive=True))
    assert pinned.season is Season.SUMMER and pinned.scene == "christmas"
    plain = look_for(date(2026, 12, 20), LookChoice("summer", festive=False))
    assert plain.scene == "summer"
    assert look_for(date(2026, 10, 28), LookChoice(AUTO, festive=False)).scene == "autumn"


def test_every_scene_has_a_banner_line_a_sentence_and_six_photos():
    scenes = {s.value for s in Season} | {"halloween", "christmas"}
    assert set(COPY) == set(PHOTOS) == scenes
    for scene in scenes:
        line, sub = COPY[scene]
        assert line and sub and len(PHOTOS[scene]) == 6
    look = look_for(date(2026, 4, 1))
    assert (look.line, look.sub, look.photos) == (*COPY["spring"], PHOTOS["spring"])


def test_the_settings_choice_is_read_and_checked():
    assert parse_look_choice("", "") == LookChoice(AUTO, True)
    assert parse_look_choice("Winter", "off") == LookChoice("winter", False)
    with pytest.raises(ValueError, match="season"):
        parse_look_choice("monsoon", "on")
    with pytest.raises(ValueError, match="on or off"):
        parse_look_choice("auto", "maybe")


def test_the_hemisphere_comes_from_the_town_before_the_country():
    assert is_southern("AU", None) and not is_southern("FR", None) and not is_southern(None, None)
    assert is_southern("FR", -33.9)  # a southern town wins over the country
    assert not is_southern("AU", 51.5)
