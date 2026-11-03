from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.eaters import Category, EaterRepository
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.weather import (
    Limits,
    Place,
    PlaceNotFoundError,
    WeatherUnavailableError,
    c_to_f,
    classify,
    degrees,
    f_to_c,
    fetch_forecast,
    find_place,
    from_unit,
    parse_limits,
    parse_unit,
    to_unit,
)
from emu_the_menu.weather_store import WeatherRepository, kinds, weather_for
from emu_the_menu.web import create_app

TODAY = date(2026, 11, 2)
UTRECHT = Place("Utrecht", "Utrecht", "NL", 52.09, 5.12)


def geocode_answer(**over):
    top = {
        "name": "Utrecht",
        "admin1": "Utrecht",
        "country_code": "NL",
        "latitude": 52.09,
        "longitude": 5.12,
    }
    return {"results": [{**top, **over}]}


def forecast_answer(highs, start=TODAY):
    days = [(start + timedelta(days=i)).isoformat() for i in range(len(highs))]
    return {
        "daily": {
            "time": days,
            "temperature_2m_max": highs,
            "temperature_2m_min": [h - 5 if h is not None else None for h in highs],
        }
    }


class FakeService:
    """Answers geocoding and forecast requests, and counts them."""

    def __init__(self, highs=(25, 8, 15), fail=False, found=True):
        self.highs, self.fail, self.found = list(highs), fail, found
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if self.fail:
            raise OSError("down")
        if "geocoding" in url:
            return geocode_answer() if self.found else {}
        return forecast_answer(self.highs)

    def forecast_calls(self):
        return sum("forecast" in u and "geocoding" not in u for u in self.urls)


@pytest.mark.parametrize(
    "high, kind",
    [
        (30, Weather.WARM),
        (20, Weather.WARM),
        (19.9, None),
        (12.1, None),
        (12, Weather.COLD),
        (-5, Weather.COLD),
    ],
)
def test_classify(high, kind):
    assert classify(high) is kind


def test_find_place_reads_the_first_match_and_limits_to_the_country():
    service = FakeService()
    place = find_place("  Utrecht ", "nl", service)
    assert place == UTRECHT
    assert "name=Utrecht" in service.urls[0] and "countryCode=nl" in service.urls[0]
    assert place.label == "Utrecht, Utrecht, NL"


def test_find_place_says_when_nothing_matches_or_input_is_empty():
    with pytest.raises(PlaceNotFoundError, match="No town called Nowhere"):
        find_place("Nowhere", None, FakeService(found=False))
    with pytest.raises(PlaceNotFoundError):
        find_place("   ", None, FakeService())
    with pytest.raises(PlaceNotFoundError):
        find_place("x" * 81, None, FakeService())


def test_find_place_rejects_an_unreachable_or_nonsense_answer():
    with pytest.raises(WeatherUnavailableError):
        find_place("Utrecht", None, FakeService(fail=True))
    with pytest.raises(WeatherUnavailableError):
        find_place("Utrecht", None, lambda url: geocode_answer(latitude=999))
    with pytest.raises(WeatherUnavailableError):
        find_place("Utrecht", None, lambda url: {"results": [{"name": "x"}]})


def test_fetch_forecast_skips_days_without_figures_and_rejects_bad_ones():
    days = fetch_forecast(UTRECHT, lambda url: forecast_answer([25, None, 8]))
    assert [(d.day, d.high) for d in days] == [(TODAY, 25.0), (TODAY + timedelta(days=2), 8.0)]
    with pytest.raises(WeatherUnavailableError):
        fetch_forecast(UTRECHT, lambda url: forecast_answer([500]))
    with pytest.raises(WeatherUnavailableError):
        fetch_forecast(UTRECHT, lambda url: {"nothing": 1})


def test_the_town_is_stored_and_cleared(tmp_path):
    with WeatherRepository(tmp_path / "e.db") as repo:
        assert repo.place() is None
        repo.set_place(UTRECHT)
        assert repo.place() == UTRECHT
        repo.set_place(None)
        assert repo.place() is None


def test_the_forecast_is_kept_and_only_fetched_when_old(tmp_path):
    service = FakeService()
    now = datetime(2026, 11, 2, 8, tzinfo=UTC)
    with WeatherRepository(tmp_path / "e.db") as repo:
        assert repo.refresh(service, now) is False  # no town yet
        repo.set_place(UTRECHT)
        assert repo.refresh(service, now) is True
        assert repo.refresh(service, now + timedelta(hours=5)) is True
        assert service.forecast_calls() == 1
        repo.refresh(service, now + timedelta(hours=7))
        assert service.forecast_calls() == 2
        assert [d.high for d in repo.days(TODAY)] == [25, 8, 15]
        assert repo.days(TODAY + timedelta(days=1))[0].high == 8  # past days are not returned


def test_a_failed_refresh_keeps_the_last_forecast(tmp_path):
    now = datetime(2026, 11, 2, 8, tzinfo=UTC)
    with WeatherRepository(tmp_path / "e.db") as repo:
        repo.set_place(UTRECHT)
        repo.refresh(FakeService(), now)
        assert repo.refresh(FakeService(fail=True), now + timedelta(hours=9)) is False
        assert len(repo.days(TODAY)) == 3


def test_changing_the_town_forgets_the_old_forecast(tmp_path):
    with WeatherRepository(tmp_path / "e.db") as repo:
        repo.set_place(UTRECHT)
        repo.refresh(FakeService())
        repo.set_place(Place("Madrid", "", "ES", 40.4, -3.7))
        assert repo.days(TODAY) == []


def test_weather_for_is_empty_without_a_town_and_makes_no_request(tmp_path):
    service = FakeService()
    assert weather_for(tmp_path / "e.db", TODAY, service) == {}
    assert service.urls == []


# ---- through the pages ----


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "emu.db"


def make_client(db_path, service):
    app = create_app(
        db_path, today=lambda: TODAY, fetch_product=lambda c: {"status": 0}, get_json=service
    )
    return TestClient(app, follow_redirects=False)


def test_settings_save_the_town_and_show_it(db_path):
    service = FakeService()
    client = make_client(db_path, service)
    r = client.post("/settings", data={"country": "NL", "town": "Utrecht"})
    assert r.status_code == 303
    assert 'value="Utrecht, Utrecht, NL"' in client.get("/settings").text
    assert "countryCode=NL" in service.urls[0]


def test_settings_do_not_look_up_an_unchanged_town_again(db_path):
    service = FakeService()
    client = make_client(db_path, service)
    client.post("/settings", data={"country": "NL", "town": "Utrecht"})
    client.post("/settings", data={"country": "NL", "town": "Utrecht, Utrecht, NL"})
    assert len(service.urls) == 1


def test_settings_say_so_when_the_town_is_not_found_and_keep_the_old_one(db_path):
    client = make_client(db_path, FakeService())
    client.post("/settings", data={"country": "NL", "town": "Utrecht"})
    other = make_client(db_path, FakeService(found=False))
    r = other.post("/settings", data={"country": "NL", "town": "Nowhere"})
    assert r.status_code == 400 and "No town called Nowhere" in r.text
    assert 'value="Utrecht, Utrecht, NL"' in other.get("/settings").text


def test_settings_keep_the_old_town_when_the_service_is_down(db_path):
    make_client(db_path, FakeService()).post("/settings", data={"country": "NL", "town": "Utrecht"})
    down = make_client(db_path, FakeService(fail=True))
    r = down.post("/settings", data={"country": "NL", "town": "Madrid"})
    assert r.status_code == 400 and "town was not changed" in r.text
    assert 'value="Utrecht, Utrecht, NL"' in down.get("/settings").text


def test_a_blank_town_switches_weather_off(db_path):
    client = make_client(db_path, FakeService())
    client.post("/settings", data={"country": "NL", "town": "Utrecht"})
    client.post("/settings", data={"country": "NL", "town": ""})
    with WeatherRepository(db_path) as repo:
        assert repo.place() is None


def add_recipe(db_path, name, weather):
    with RecipeRepository(db_path) as repo:
        repo.add(name, 1, [MealType.DINNER], weather, [], [("rice", 100, "g")])


def test_the_menu_shows_the_forecast_and_favours_matching_recipes(db_path):
    with EaterRepository(db_path) as eaters:
        eaters.add("Sam", Category.MAN)
    add_recipe(db_path, "Hot soup", Weather.COLD)
    add_recipe(db_path, "Cold salad", Weather.WARM)
    client = make_client(db_path, FakeService(highs=(25, 8, 15, 8, 8, 8, 8)))
    client.post("/settings", data={"country": "NL", "town": "Utrecht"})
    page = client.get("/menu?go=1&meal_types=dinner&eater=1&days=3&cooldown=0&allowance=50").text
    assert "25° / 20° C · warm" in page
    assert "8° / 3° C · cold" in page
    assert "15° / 10° C</span>" in page
    first, second, third = (part for part in page.split('class="day"')[1:4])
    assert "Cold salad" in first and "Hot soup" in second


def test_the_menu_works_without_a_town(db_path):
    with EaterRepository(db_path) as eaters:
        eaters.add("Sam", Category.MAN)
    add_recipe(db_path, "Rice", Weather.ANY)
    page = make_client(db_path, FakeService()).get("/menu?go=1&meal_types=dinner&eater=1").text
    assert "Rice" in page and 'class="weather"' not in page


def test_the_menu_still_plans_when_the_weather_service_is_down(db_path):
    with EaterRepository(db_path) as eaters:
        eaters.add("Sam", Category.MAN)
    add_recipe(db_path, "Rice", Weather.ANY)
    make_client(db_path, FakeService()).post("/settings", data={"country": "NL", "town": "Utrecht"})
    page = (
        make_client(db_path, FakeService(fail=True))
        .get("/menu?go=1&meal_types=dinner&eater=1")
        .text
    )
    assert "Rice" in page


def test_kinds_maps_days_to_warm_cold_or_none(tmp_path):
    service = FakeService()
    with WeatherRepository(tmp_path / "e.db") as repo:
        repo.set_place(UTRECHT)
    forecast = weather_for(tmp_path / "e.db", TODAY, service)
    assert kinds(forecast) == {
        TODAY: Weather.WARM,
        TODAY + timedelta(days=1): Weather.COLD,
        TODAY + timedelta(days=2): None,
    }


def test_classify_uses_the_limits_given():
    assert classify(16, Limits(15, 5)) is Weather.WARM
    assert classify(5, Limits(15, 5)) is Weather.COLD
    assert classify(10, Limits(15, 5)) is None


def test_fahrenheit_conversion():
    assert c_to_f(20) == 68 and c_to_f(12) == pytest.approx(53.6) and c_to_f(0) == 32
    assert f_to_c(68) == pytest.approx(20)


def test_parse_limits():
    assert parse_limits("", "") == Limits(20, 12)
    assert parse_limits("22,5", " 8 ") == Limits(22.5, 8)
    for warm, cold, message in [
        ("hot", "", "Warm from must be a number"),
        ("", "abc", "Cold up to must be a number"),
        ("99", "", "between -30 and 50"),
        ("nan", "", "between"),
        ("10", "12", "Cold up to must be lower"),
        ("12", "", "Cold up to must be lower"),  # equal to the built-in cold figure
    ]:
        with pytest.raises(ValueError, match=message):
            parse_limits(warm, cold)


def test_limits_are_stored_and_change_what_counts_as_warm(tmp_path):
    with WeatherRepository(tmp_path / "e.db") as repo:
        assert repo.limits() == Limits(20, 12)
        repo.set_limits(Limits(14, 9))
        assert repo.limits() == Limits(14, 9)
        repo.set_place(UTRECHT)
        repo.refresh(FakeService(highs=(25, 8, 15)))
        assert [d.kind for d in repo.days(TODAY)] == [Weather.WARM, Weather.COLD, Weather.WARM]


def test_settings_show_the_limits_with_fahrenheit_and_save_them(db_path):
    client = make_client(db_path, FakeService())
    page = client.get("/settings").text
    assert 'name="warm_from"' in page and 'value="20"' in page and 'value="12"' in page
    assert ">68<" in page and ">53.6<" in page
    assert client.post("/settings", data={"warm_from": "24", "cold_up_to": "10"}).status_code == 303
    page = client.get("/settings").text
    assert 'value="24"' in page and ">75.2<" in page and ">50<" in page


def test_bad_limits_are_refused_and_what_was_typed_is_kept(db_path):
    client = make_client(db_path, FakeService())
    r = client.post("/settings", data={"warm_from": "5", "cold_up_to": "9"})
    assert r.status_code == 400 and "Cold up to must be lower" in r.text
    assert 'value="5"' in r.text and 'value="9"' in r.text
    assert 'value="20"' in client.get("/settings").text  # nothing was saved


def test_a_blank_limit_goes_back_to_the_built_in_figure(db_path):
    client = make_client(db_path, FakeService())
    client.post("/settings", data={"warm_from": "24", "cold_up_to": "10"})
    client.post("/settings", data={"warm_from": "", "cold_up_to": ""})
    with WeatherRepository(db_path) as repo:
        assert repo.limits() == Limits(20, 12)


def test_the_menu_uses_the_saved_limits(db_path):
    with EaterRepository(db_path) as eaters:
        eaters.add("Sam", Category.MAN)
    add_recipe(db_path, "Rice", Weather.ANY)
    client = make_client(db_path, FakeService(highs=(15, 15, 15)))
    client.post("/settings", data={"town": "Utrecht", "warm_from": "14", "cold_up_to": "5"})
    page = client.get("/menu?go=1&meal_types=dinner&eater=1&days=1").text
    assert "· warm" in page


def test_unit_helpers():
    assert parse_unit(" f ") == "F" and parse_unit("C") == "C"
    with pytest.raises(ValueError):
        parse_unit("K")
    assert to_unit(20, "F") == 68 and to_unit(20, "C") == 20
    assert from_unit(68, "F") == pytest.approx(20)
    assert degrees(25, "F") == 77 and degrees(25) == 25 and degrees(8, "F") == 46


def test_limits_typed_in_fahrenheit_are_kept_in_celsius():
    assert parse_limits("68", "53.6", "F") == Limits(20, 12)
    assert parse_limits("", "", "F") == Limits(20, 12)
    with pytest.raises(ValueError, match="between -22 and 122 °F"):
        parse_limits("200", "", "F")
    with pytest.raises(ValueError, match="degrees Fahrenheit"):
        parse_limits("warm", "", "F")
    with pytest.raises(ValueError, match="Cold up to must be lower"):
        parse_limits("50", "60", "F")


def test_the_unit_is_stored_and_defaults_to_celsius(tmp_path):
    with WeatherRepository(tmp_path / "e.db") as repo:
        assert repo.unit() == "C"
        repo.set_unit("F")
        assert repo.unit() == "F"
        with pytest.raises(ValueError):
            repo.set_unit("K")


def test_switching_to_fahrenheit_shows_the_limits_and_the_menu_in_fahrenheit(db_path):
    with EaterRepository(db_path) as eaters:
        eaters.add("Sam", Category.MAN)
    add_recipe(db_path, "Rice", Weather.ANY)
    client = make_client(db_path, FakeService(highs=(25, 8, 15)))
    r = client.post(
        "/settings",
        data={"town": "Utrecht", "temperature_unit": "F", "warm_from": "75", "cold_up_to": "50"},
    )
    assert r.status_code == 303
    with WeatherRepository(db_path) as repo:
        assert repo.unit() == "F"
        assert repo.limits() == Limits(23.89, 10)
    page = client.get("/settings").text
    assert 'value="75"' in page and 'value="50"' in page
    assert '<option value="F" selected>' in page and ">23.9<" in page  # the Celsius equivalent
    menu = client.get("/menu?go=1&meal_types=dinner&eater=1&days=3").text
    assert "77° / 68° F · warm" in menu and "46° / 37° F · cold" in menu
    assert "25° / 20° C" not in menu


def test_a_form_without_the_unit_box_keeps_the_unit(db_path):
    client = make_client(db_path, FakeService())
    client.post("/settings", data={"temperature_unit": "F", "warm_from": "75", "cold_up_to": "50"})
    client.post("/settings", data={"warm_from": "80", "cold_up_to": "40"})
    with WeatherRepository(db_path) as repo:
        assert repo.unit() == "F" and repo.limits() == Limits(26.67, 4.44)


def test_a_bad_unit_is_refused(db_path):
    r = make_client(db_path, FakeService()).post("/settings", data={"temperature_unit": "K"})
    assert r.status_code == 400 and "Choose Celsius or Fahrenheit" in r.text


def test_bad_fahrenheit_limits_keep_what_was_typed_in_fahrenheit(db_path):
    r = make_client(db_path, FakeService()).post(
        "/settings", data={"temperature_unit": "F", "warm_from": "40", "cold_up_to": "60"}
    )
    assert r.status_code == 400
    assert '<option value="F" selected>' in r.text and 'value="40"' in r.text
