from datetime import date

import pytest
from fastapi.testclient import TestClient

from emu_the_menu.holidays import Holiday, HolidaysUnavailableError, fetch_holidays
from emu_the_menu.occasion_repository import OccasionNotFoundError, OccasionRepository
from emu_the_menu.occasions import (
    Occasion,
    Source,
    combine,
    parse_extra_percent,
    parse_headcount,
    parse_name,
    read_form,
)
from emu_the_menu.web import create_app

TODAY = date(2026, 11, 3)


# ---- the rules ----


def test_parse_name():
    assert parse_name("  Sam's   birthday ") == "Sam's birthday"
    for bad in ("", "   ", "x" * 81):
        with pytest.raises(ValueError):
            parse_name(bad)


def test_parse_headcount():
    assert parse_headcount("") is None and parse_headcount(" 8 ") == 8
    for bad in ("0", "201", "-3", "2.5", "many", "٣"):
        with pytest.raises(ValueError, match="headcount"):
            parse_headcount(bad)


def test_parse_extra_percent():
    assert parse_extra_percent("") == 0 and parse_extra_percent("25%") == 25
    assert parse_extra_percent("0") == 0 and parse_extra_percent("50") == 50
    for bad in ("51", "-5", "1.5", "lots"):
        with pytest.raises(ValueError, match="extra calories"):
            parse_extra_percent(bad)


def test_read_form_checks_every_field():
    assert read_form("Feast", "2026-12-25", "6", "20", "Festive, big") == (
        "Feast",
        date(2026, 12, 25),
        6,
        20,
        ("festive", "big"),
    )
    with pytest.raises(ValueError, match="valid date"):
        read_form("Feast", "25 Dec", "", "", "")
    with pytest.raises(ValueError, match="tags"):
        read_form("Feast", "2026-12-25", "", "", ",".join(f"t{i}" for i in range(30)))


def test_guests_are_the_people_beyond_the_household():
    assert Occasion("x", TODAY, headcount=6).guests(4) == 2
    assert Occasion("x", TODAY, headcount=3).guests(4) == 0
    assert Occasion("x", TODAY).guests(4) == 0


def test_combine_takes_the_biggest_table_allowance_and_all_tags():
    a = Occasion("Holiday", TODAY, None, 0, ("festive",), Source.HOLIDAY)
    b = Occasion("Party", TODAY, 10, 25, ("festive", "big"))
    c = Occasion("Visit", TODAY, 6, 10, ())
    merged = combine([a, b, c])
    assert (merged.name, merged.headcount, merged.extra_percent, merged.tags) == (
        "Holiday and Party and Visit",
        10,
        25,
        ("festive", "big"),
    )
    assert combine([a]) is a and combine([]) is None
    assert combine([a, Occasion("y", TODAY)]).headcount is None


# ---- holidays ----


def nager(*entries):
    return list(entries)


def entry(day, name, **extra):
    return {"date": day, "name": name, "localName": name, "global": True, **extra}


def test_fetch_holidays_keeps_nationwide_ones_of_the_year_in_order():
    seen = []

    def get(url):
        seen.append(url)
        return nager(
            entry("2026-12-25", "Christmas Day"),
            entry("2026-01-01", "New Year's Day"),
            entry("2026-05-01", "Regional day", **{"global": False}),
            entry("2027-01-01", "Next year"),
            entry("2026-12-25", "Christmas Day"),
        )

    assert fetch_holidays(2026, "nl", get) == [
        Holiday(date(2026, 1, 1), "New Year's Day"),
        Holiday(date(2026, 12, 25), "Christmas Day"),
    ]
    assert seen == ["https://date.nager.at/api/v3/PublicHolidays/2026/NL"]


def test_fetch_holidays_needs_a_country_and_reports_failures():
    with pytest.raises(ValueError, match="country"):
        fetch_holidays(2026, "", lambda url: [])
    for get in (
        lambda url: (_ for _ in ()).throw(OSError("down")),
        lambda url: {"error": "nope"},
        lambda url: [{"date": "tomorrow", "name": "x"}],
        lambda url: [{"name": "x"}],
        lambda url: ["not a dict"],
    ):
        with pytest.raises(HolidaysUnavailableError):
            fetch_holidays(2026, "NL", get)


# ---- storage ----


def test_occasions_are_stored_read_and_removed(db_url):
    with OccasionRepository(db_url) as repo:
        oid = repo.add("Party", date(2026, 12, 1), 8, 20, ["big", "festive"])
        assert repo.get(oid) == Occasion(
            "Party", date(2026, 12, 1), 8, 20, ("big", "festive"), Source.MANUAL, oid
        )
        repo.update(oid, "Party!", date(2026, 12, 2), None, 0, [])
        assert repo.get(oid).name == "Party!" and repo.get(oid).tags == ()
        repo.delete(oid)
        assert repo.get(oid) is None
        with pytest.raises(OccasionNotFoundError):
            repo.delete(oid)
        with pytest.raises(OccasionNotFoundError):
            repo.update(oid, "x", TODAY, None, 0, [])


def test_from_day_lists_upcoming_in_date_order(db_url):
    with OccasionRepository(db_url) as repo:
        repo.add("Later", date(2026, 12, 5))
        repo.add("Past", date(2026, 10, 1))
        repo.add("Today", TODAY)
        assert [o.name for o in repo.from_day(TODAY)] == ["Today", "Later"]


def test_importing_holidays_twice_adds_them_once(db_url):
    holidays = [Holiday(date(2026, 12, 25), "Christmas Day"), Holiday(date(2026, 1, 1), "New Year")]
    with OccasionRepository(db_url) as repo:
        assert repo.add_holidays(holidays) == 2
        assert repo.add_holidays(holidays) == 0
        assert {o.source for o in repo.all()} == {Source.HOLIDAY}


def test_an_edited_holiday_becomes_manual_and_is_not_duplicated_by_a_new_import(db_url):
    holiday = Holiday(date(2026, 12, 25), "Christmas Day")
    with OccasionRepository(db_url) as repo:
        repo.add_holidays([holiday])
        (found,) = repo.all()
        repo.update(found.id, "Christmas dinner", found.day, 10, 30, ["festive"])
        assert repo.get(found.id).source is Source.MANUAL
        assert repo.add_holidays([holiday]) == 1  # the original name is a new holiday again


def test_a_manual_occasion_may_share_a_name_and_day_with_a_holiday(db_url):
    with OccasionRepository(db_url) as repo:
        repo.add("Christmas Day", date(2026, 12, 25), 6)
        assert repo.add_holidays([Holiday(date(2026, 12, 25), "Christmas Day")]) == 1
        assert len(repo.all()) == 2


def test_for_days_merges_each_days_occasions_and_stays_in_range(db_url):
    with OccasionRepository(db_url) as repo:
        repo.add("A", date(2026, 11, 4), 6, 10, ["x"])
        repo.add("B", date(2026, 11, 4), 4, 25, ["y"])
        repo.add("Out", date(2026, 11, 10))
        found = repo.for_days(TODAY, 7)
        assert list(found) == [date(2026, 11, 4)]
        merged = found[date(2026, 11, 4)]
        assert (merged.headcount, merged.extra_percent, merged.tags) == (6, 25, ("x", "y"))


def test_export_lists_occasions(db_url):
    with OccasionRepository(db_url) as repo:
        repo.add("Party", date(2026, 12, 1), 8, 20, ["big"])
        assert repo.export_data() == [
            {
                "name": "Party",
                "date": "2026-12-01",
                "headcount": 8,
                "extra_calories_percent": 20,
                "tags": ["big"],
                "source": "manual",
            }
        ]


# ---- the page ----


def make_client(db_url, get_holidays=None):
    app = create_app(
        db_url,
        today=lambda: TODAY,
        fetch_product=lambda c: {"status": 0},
        get_holidays=get_holidays or (lambda url: []),
    )
    return TestClient(app, follow_redirects=False)


def test_the_page_starts_empty_and_is_in_the_navigation(db_url):
    page = make_client(db_url).get("/occasions").text
    assert "No upcoming occasions" in page and 'href="/occasions" aria-current="page"' in page


def test_adding_an_occasion_lists_it(db_url):
    client = make_client(db_url)
    r = client.post(
        "/occasions",
        data={"name": "Party", "day": "2026-12-01", "headcount": "8", "extra": "20", "tags": "Big"},
    )
    assert r.status_code == 303
    page = client.get("/occasions").text
    assert "Party" in page and "8 at the table" in page and "+20% calories" in page
    assert "Tags: big" in page


def test_a_bad_entry_says_why_and_keeps_what_was_typed(db_url):
    client = make_client(db_url)
    r = client.post("/occasions", data={"name": "Party", "day": "", "headcount": "8"})
    assert r.status_code == 400 and "Enter a valid date" in r.text and 'value="Party"' in r.text
    with OccasionRepository(db_url) as repo:
        assert repo.all() == []


def test_editing_and_removing(db_url):
    client = make_client(db_url)
    with OccasionRepository(db_url) as repo:
        oid = repo.add("Party", date(2026, 12, 1))
    r = client.post(
        f"/occasions/{oid}",
        data={"name": "Feast", "day": "2026-12-02", "headcount": "", "extra": "", "tags": ""},
    )
    assert r.status_code == 303 and "Feast" in client.get("/occasions").text
    assert client.post(f"/occasions/{oid}", data={"name": "", "day": "x"}).status_code == 400
    assert client.post("/occasions/999", data={"name": "A", "day": "2026-12-02"}).status_code == 404
    assert client.post(f"/occasions/{oid}/delete").status_code == 303
    assert client.post(f"/occasions/{oid}/delete").status_code == 404


def test_importing_holidays_needs_a_country(db_url):
    page = make_client(db_url).get("/occasions").text
    assert "Set your country in Settings first" in page
    r = make_client(db_url).post("/occasions/holidays", data={"year": "2026"})
    assert r.status_code == 400 and "Set the country in Settings" in r.text


def test_importing_holidays_adds_them_and_says_how_many(db_url):
    calls = []

    def get(url):
        calls.append(url)
        return [entry("2026-12-25", "Christmas Day"), entry("2026-11-01", "All Saints (past)")]

    client = make_client(db_url, get)
    client.post("/settings", data={"country": "NL"})
    r = client.post("/occasions/holidays", data={"year": "2026"})
    assert r.status_code == 303 and r.headers["location"] == "/occasions?imported=2"
    page = client.get("/occasions?imported=2").text
    assert "Added 2 public holidays" in page
    assert "Christmas Day" in page and "All Saints" not in page  # past ones are not listed
    assert calls == ["https://date.nager.at/api/v3/PublicHolidays/2026/NL"]
    again = client.post("/occasions/holidays", data={"year": "2026"})
    assert again.headers["location"] == "/occasions?imported=0"
    assert "already here" in client.get("/occasions?imported=0").text


def test_importing_only_allows_this_year_or_next_and_reports_an_unreachable_service(db_url):
    def down(url):
        raise OSError("down")

    client = make_client(db_url, down)
    client.post("/settings", data={"country": "NL"})
    assert client.post("/occasions/holidays", data={"year": "1999"}).status_code == 400
    assert client.post("/occasions/holidays", data={"year": "abc"}).status_code == 400
    r = client.post("/occasions/holidays", data={"year": "2027"})
    assert r.status_code == 400 and "could not be fetched" in r.text


def test_the_json_export_includes_occasions(db_url):
    client = make_client(db_url)
    client.post("/occasions", data={"name": "Party", "day": "2026-12-01"})
    names = [o["name"] for o in client.get("/export.json").json()["occasions"]]
    assert names == ["Party"]
