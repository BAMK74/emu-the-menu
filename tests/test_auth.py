"""Signing in, sessions, CSRF, roles and the accounts command (ADR 0008).

These use the app's real sign-in (`real_auth`), over https so the Secure cookie is sent back.
"""

import io
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from emu_the_menu import accounts as accounts_cli
from emu_the_menu import auth
from emu_the_menu.accounts import AccountError, AccountRepository, Role
from emu_the_menu.eaters import Category, EaterRepository
from emu_the_menu.import_sqlite import import_sqlite
from emu_the_menu.repository import StockRepository
from emu_the_menu.web import create_app

TODAY = date(2026, 11, 10)
ADULT_PW = "correct horse battery"
TEEN_PW = "teen password 123"
FIXTURE = Path(__file__).parent / "data" / "emu-sqlite.db"


@pytest.fixture
def app(db_url, real_auth):
    return create_app(db_url, today=lambda: TODAY, fetch_product=lambda code: {})


def new_client(app):
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


@pytest.fixture
def household(db_url):
    """The test household (Alex, Mia, and Sam who is a child), Alex an Adult and Mia a Teen."""
    import_sqlite(FIXTURE, db_url)
    with EaterRepository(db_url) as eaters, AccountRepository(db_url) as accounts:
        by_name = {e.name: e for e in eaters.list()}
        accounts.create(by_name["Alex"].id, Role.ADULT, ADULT_PW)
        accounts.create(by_name["Mia"].id, Role.TEEN, TEEN_PW)
    return by_name


def sign_in(client, name, password, next_path="/"):
    page = client.get("/sign-in").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    return client.post(
        "/sign-in",
        data={"name": name, "password": password, "next": next_path, "csrf_token": token},
    )


def csrf(client):
    return re.search(r'<meta name="csrf-token" content="([^"]*)">', client.get("/").text).group(1)


@pytest.fixture
def adult(app, household):
    client = new_client(app)
    assert sign_in(client, "Alex", ADULT_PW).status_code == 303
    return client


@pytest.fixture
def teen(app, household):
    client = new_client(app)
    assert sign_in(client, "mia", TEEN_PW).status_code == 303  # any case
    return client


# --- signed out -------------------------------------------------------------------------------


def every_route(app):
    """Every (method, path pattern) the app answers, including those of included routers (which
    this FastAPI keeps nested rather than copying into `app.routes`)."""

    def walk(routes, prefix=""):
        for route in routes:
            if isinstance(route, APIRoute):
                for method in sorted(route.methods):
                    yield method, prefix + route.path
            elif hasattr(route, "original_router"):
                nested = getattr(route.include_context, "prefix", "") or ""
                yield from walk(route.original_router.routes, prefix + nested)

    return sorted(set(walk(app.routes)))


def test_signed_out_every_route_but_the_open_ones_goes_to_sign_in(app):
    client = new_client(app)
    checked = 0
    for method, pattern in every_route(app):
        if (method, pattern) in auth.OPEN_ROUTES:
            continue
        path = re.sub(r"\{[^}]+\}", "1", pattern)
        response = client.request(method, path)
        assert response.status_code == 303, (method, pattern, response.status_code)
        assert response.headers["location"].startswith("/sign-in"), (method, pattern)
        checked += 1
    assert checked > 60  # the app's routes, not an empty list


def test_the_open_routes_are_only_these():
    assert auth.OPEN_ROUTES == {
        ("GET", "/sign-in"),
        ("POST", "/sign-in"),
        ("GET", "/healthz"),
        ("GET", "/disclaimer"),
    }


def test_the_front_page_redirects_so_the_uptime_monitor_sees_it_up(app):
    response = new_client(app).get("/")
    assert (response.status_code, response.headers["location"]) == (303, "/sign-in")


def test_a_page_asked_for_comes_back_after_signing_in(app, household):
    client = new_client(app)
    assert client.get("/menus/1").headers["location"] == "/sign-in?next=/menus/1"
    response = sign_in(client, "Alex", ADULT_PW, next_path="/menus/1")
    assert response.headers["location"] == "/menus/1"


def test_a_failed_try_still_returns_to_the_page_asked_for(app, household):
    client = new_client(app)
    failed = sign_in(client, "Alex", "not the password", next_path="/menus/1")
    assert 'name="next" value="/menus/1"' in failed.text
    assert sign_in(client, "Alex", ADULT_PW, next_path="/menus/1").headers["location"] == "/menus/1"


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example/", "/\\evil.example"])
def test_signing_in_never_sends_you_to_another_site(app, household, target):
    response = sign_in(new_client(app), "Alex", ADULT_PW, next_path=target)
    assert response.headers["location"] == "/"


def test_open_pages_and_files_need_no_account(app):
    client = new_client(app)
    for path in ("/healthz", "/sign-in", "/disclaimer", "/static/js/shared.js", "/static/base.css"):
        assert client.get(path).status_code == 200, path


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_the_generated_api_docs_are_not_served(app, path):
    assert new_client(app).get(path).status_code == 404


def test_the_sign_in_page_shows_no_menu_and_no_data(app, household):
    page = new_client(app).get("/sign-in").text
    assert 'href="/menu"' not in page and "chicken thighs" not in page.lower()


# --- signing in -------------------------------------------------------------------------------


def test_signing_in_sets_a_secure_http_only_lax_cookie(app, household):
    response = sign_in(new_client(app), "Alex", ADULT_PW)
    cookie = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith("emu_session=")
    )
    attributes = {part.strip().split("=")[0].lower() for part in cookie.split(";")}
    assert {"secure", "httponly", "path", "max-age"} <= attributes
    assert "samesite=lax" in cookie.lower()


def test_signed_in_the_pages_work(adult):
    assert adult.get("/").status_code == 200
    assert "chicken thighs" in adult.get("/").text


def test_a_wrong_password_and_an_unknown_name_get_the_same_answer(app, household):
    wrong = sign_in(new_client(app), "Alex", "not the password")
    nobody = sign_in(new_client(app), "Nobody", "not the password")
    assert wrong.status_code == nobody.status_code == 400
    assert "That name and password do not match." in wrong.text
    assert "That name and password do not match." in nobody.text


def test_a_child_cannot_sign_in(app, household):
    assert sign_in(new_client(app), "Sam", ADULT_PW).status_code == 400


def test_the_sign_in_form_needs_its_own_token(app, household):
    client = new_client(app)
    client.get("/sign-in")
    response = client.post(
        "/sign-in", data={"name": "Alex", "password": ADULT_PW, "csrf_token": "forged"}
    )
    assert response.status_code == 400
    assert "emu_session" not in response.headers.get("set-cookie", "")


def test_only_a_hash_of_the_session_token_is_stored(app, household, run_sql):
    response = sign_in(new_client(app), "Alex", ADULT_PW)
    token = re.search(r"emu_session=([^;]+)", response.headers["set-cookie"]).group(1)
    stored = [hash_ for (hash_,) in run_sql("SELECT token_hash FROM sessions")]
    assert len(stored) == 1 and token not in stored[0] and len(stored[0]) == 64


def test_passwords_are_stored_as_argon2id_hashes(household, run_sql):
    hashes = [h for (h,) in run_sql("SELECT password_hash FROM accounts")]
    assert all(h.startswith("$argon2id$") for h in hashes)
    assert not any(ADULT_PW in h or TEEN_PW in h for h in hashes)


def test_signing_out_ends_the_session(adult):
    response = adult.post("/sign-out", headers={"X-CSRF-Token": csrf(adult)})
    assert response.headers["location"] == "/sign-in"
    assert adult.get("/").status_code == 303


def test_a_stolen_cookie_stops_working_after_signing_out(app, adult):
    thief = new_client(app)
    thief.cookies.set("emu_session", adult.cookies.get("emu_session"))
    assert thief.get("/").status_code == 200
    adult.post("/sign-out", headers={"X-CSRF-Token": csrf(adult)})
    assert thief.get("/").status_code == 303


def test_an_expired_session_needs_signing_in_again(adult, run_sql):
    run_sql("UPDATE sessions SET expires_at = now() - interval '1 second'")
    assert adult.get("/").status_code == 303


# --- slowing down guesses ---------------------------------------------------------------------


def test_after_five_failures_even_the_right_password_must_wait(app, household, run_sql):
    for _ in range(5):
        assert sign_in(new_client(app), "Alex", "guess guess guess").status_code == 400
    response = sign_in(new_client(app), "Alex", ADULT_PW)
    assert response.status_code == 429
    assert "Too many tries. Wait 30 seconds" in response.text
    run_sql("UPDATE sign_in_failures SET last_failed_at = now() - interval '31 seconds'")
    assert sign_in(new_client(app), "Alex", ADULT_PW).status_code == 303
    assert run_sql("SELECT COUNT(*) FROM sign_in_failures") == [(0,)]  # a success forgives


def test_each_further_failure_doubles_the_wait_up_to_a_quarter_hour(household, db_url):
    with AccountRepository(db_url) as accounts:
        for _ in range(5):
            accounts.check_sign_in("Alex", "wrong")
        assert accounts.wait_before_trying("Alex") > timedelta(seconds=29)
        accounts.check_sign_in("Alex", "wrong")
        assert accounts.wait_before_trying("Alex") > timedelta(seconds=59)
        for _ in range(20):
            accounts.check_sign_in("Alex", "wrong")
        assert accounts.wait_before_trying("Alex") <= timedelta(minutes=15)


def test_names_that_do_not_exist_are_slowed_down_too(app, household):
    for _ in range(5):
        sign_in(new_client(app), "Someone Else", "guess guess guess")
    assert sign_in(new_client(app), "Someone Else", "guess").status_code == 429


# --- CSRF -------------------------------------------------------------------------------------


def stock_names(db_url):
    with StockRepository(db_url) as stock:
        return {item.ingredient for item in stock.list_items()}


FLOUR = {"ingredient": "rye flour", "quantity": "1", "unit": "kg", "location": "pantry"}


def test_a_form_without_the_token_changes_nothing(adult, db_url):
    response = adult.post("/items", data=FLOUR)
    assert response.status_code == 400
    assert "This form had expired" in response.text
    assert "rye flour" not in stock_names(db_url)


def test_a_form_with_another_sessions_token_changes_nothing(app, adult, household, db_url):
    other = new_client(app)
    sign_in(other, "Alex", ADULT_PW)
    response = adult.post("/items", data={**FLOUR, "csrf_token": csrf(other)})
    assert response.status_code == 400
    assert "rye flour" not in stock_names(db_url)


def test_the_token_works_in_the_form_or_in_a_header(adult, db_url):
    token = csrf(adult)
    assert adult.post("/items", data={**FLOUR, "csrf_token": token}).status_code == 303
    oats = {**FLOUR, "ingredient": "oats"}
    assert adult.post("/items", data=oats, headers={"X-CSRF-Token": token}).status_code == 303
    assert {"rye flour", "oats"} <= stock_names(db_url)


def test_every_form_that_posts_carries_the_token():
    templates = Path(auth.__file__).parent / "templates"
    for template in sorted(templates.glob("*.html")):
        text = template.read_text(encoding="utf-8")
        for form in re.findall(r"<form\b[^>]*method=\"post\"[^>]*>(.{0,40})", text, re.S | re.I):
            assert form.startswith("{{ csrf_field() }}") or template.name == "sign_in.html", (
                template.name,
                form,
            )


def test_the_pages_give_scripts_the_token(adult):
    assert len(csrf(adult)) > 30


# --- what a Teen may do -----------------------------------------------------------------------


def test_a_teen_sees_everything(teen):
    for path in ("/", "/menu", "/menus/1", "/recipes", "/eaters", "/occasions", "/settings"):
        assert teen.get(path).status_code == 200, path


def test_a_teen_cannot_change_shared_data(teen, db_url):
    token = csrf(teen)
    for path, data in [
        ("/items", FLOUR),
        ("/recipes/1/delete", {}),
        ("/settings", {"country": "FR"}),
        ("/occasions", {"name": "Party", "day": "2026-12-01"}),
        ("/menus/1/accept", {}),
    ]:
        response = teen.post(path, data={**data, "csrf_token": token})
        assert response.status_code == 403, path
        assert "Only an Adult can change this." in response.text
    assert "rye flour" not in stock_names(db_url)
    with StockRepository(db_url) as stock:
        assert stock.household_country() == "NZ"


def test_a_teen_can_mark_a_meal_cooked_and_not_cooked(teen, db_url):
    from emu_the_menu.menu_repository import MenuRepository

    with MenuRepository(db_url) as menus:
        meal = next(m for m in menus.get(1).meals if m.recipe_id and m.cooked_on is None)
    token = csrf(teen)
    cooked = teen.post(f"/menus/1/meals/{meal.id}/cooked", data={"csrf_token": token})
    assert cooked.status_code == 303
    with MenuRepository(db_url) as menus:
        assert next(m for m in menus.get(1).meals if m.id == meal.id).cooked_on is not None
    undone = teen.post(f"/menus/1/meals/{meal.id}/not-cooked", data={"csrf_token": token})
    assert undone.status_code == 303


def test_a_teen_changes_their_own_dislikes_and_diet_only(teen, household, db_url):
    mia, alex = household["Mia"], household["Alex"]
    token = csrf(teen)
    response = teen.post(
        f"/eaters/{mia.id}/preferences",
        data={"dislikes": "olives, celery", "diet": "vegan", "csrf_token": token},
    )
    assert response.status_code == 303
    with EaterRepository(db_url) as eaters:
        after = eaters.get(mia.id)
    assert after.dislikes == ("olives", "celery") and after.diet.value == "vegan"
    assert after.allergens == mia.allergens and after.calorie_adjustment == -100  # untouched
    others = teen.post(f"/eaters/{alex.id}/preferences", data={"dislikes": "", "csrf_token": token})
    assert others.status_code == 403
    everything = teen.post(
        f"/eaters/{mia.id}",
        data={"name": "Mia", "category": "woman", "allergens": [], "csrf_token": token},
    )
    assert everything.status_code == 403
    with EaterRepository(db_url) as eaters:
        assert eaters.get(mia.id).allergens == mia.allergens


def test_the_eaters_page_offers_a_teen_only_their_own_preferences(teen, household):
    page = teen.get("/eaters").text
    assert f'action="/eaters/{household["Mia"].id}/preferences"' in page
    assert 'action="/eaters"' not in page
    assert f'action="/eaters/{household["Alex"].id}"' not in page


def test_a_teen_cannot_manage_accounts(teen):
    assert teen.get("/accounts").status_code == 403
    response = teen.post("/accounts/1/role", data={"role": "teen", "csrf_token": csrf(teen)})
    assert response.status_code == 403


def test_an_adult_can_change_everything(adult, db_url):
    token = csrf(adult)
    assert adult.post("/items", data={**FLOUR, "csrf_token": token}).status_code == 303
    assert adult.post("/menus/1/accept", data={"csrf_token": token}).status_code == 303


# --- accounts, for Adults ---------------------------------------------------------------------


def test_an_adult_gives_an_eater_an_account_who_can_then_sign_in(app, adult, db_url):
    with EaterRepository(db_url) as eaters:
        robin = eaters.add("Robin", Category.TEEN)
    response = adult.post(
        "/accounts",
        data={
            "eater_id": robin.id,
            "role": "teen",
            "password": "robins pw 2026",
            "csrf_token": csrf(adult),
        },
    )
    assert response.status_code == 303
    assert sign_in(new_client(app), "Robin", "robins pw 2026").status_code == 303


def test_a_child_cannot_be_given_an_account(adult, household):
    response = adult.post(
        "/accounts",
        data={
            "eater_id": household["Sam"].id,
            "role": "teen",
            "password": "a long password",
            "csrf_token": csrf(adult),
        },
    )
    assert response.status_code == 400
    assert "Sam is a child" in response.text


def test_a_short_password_is_refused(adult, household, db_url):
    with EaterRepository(db_url) as eaters:
        robin = eaters.add("Robin", Category.TEEN)
    response = adult.post(
        "/accounts",
        data={"eater_id": robin.id, "role": "teen", "password": "short", "csrf_token": csrf(adult)},
    )
    assert "at least 10 characters" in response.text


def test_the_only_adult_cannot_be_demoted_or_removed(adult, db_url):
    with AccountRepository(db_url) as accounts:
        alex = next(a for a in accounts.accounts() if a.is_adult)
    token = csrf(adult)
    demote = adult.post(f"/accounts/{alex.id}/role", data={"role": "teen", "csrf_token": token})
    assert "only Adult" in demote.text
    remove = adult.post(f"/accounts/{alex.id}/delete", data={"csrf_token": token})
    assert "only Adult" in remove.text
    with AccountRepository(db_url) as accounts:
        assert accounts.adult_count() == 1


def test_a_new_password_signs_that_account_out_everywhere(app, adult, teen, db_url):
    with AccountRepository(db_url) as accounts:
        mia = next(a for a in accounts.accounts() if a.name == "Mia")
    response = adult.post(
        f"/accounts/{mia.id}/password",
        data={"password": "a brand new one", "csrf_token": csrf(adult)},
    )
    assert response.status_code == 303
    assert teen.get("/").status_code == 303
    assert sign_in(new_client(app), "Mia", "a brand new one").status_code == 303


def test_changing_your_own_password_keeps_you_signed_in_here_only(app, adult, household):
    elsewhere = new_client(app)
    sign_in(elsewhere, "Alex", ADULT_PW)
    response = adult.post(
        "/account/password",
        data={
            "current": ADULT_PW,
            "new": "another good one",
            "again": "another good one",
            "csrf_token": csrf(adult),
        },
    )
    assert response.headers["location"] == "/account?saved=1"
    assert adult.get("/").status_code == 200
    assert elsewhere.get("/").status_code == 303


def test_your_own_password_needs_the_current_one(adult):
    response = adult.post(
        "/account/password",
        data={"current": "wrong", "new": "x" * 12, "again": "x" * 12, "csrf_token": csrf(adult)},
    )
    assert "The current password is not right." in response.text


def test_removing_an_eater_removes_their_account(adult, household, db_url):
    with EaterRepository(db_url) as eaters:
        eaters.delete(household["Mia"].id)
    with AccountRepository(db_url) as accounts:
        assert [a.name for a in accounts.accounts()] == ["Alex"]


# --- the first Adult, from the command line ---------------------------------------------------


def run_cli(monkeypatch, db_url, *args, password=""):
    monkeypatch.setenv("EMU_DATABASE_URL", db_url)
    monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    return accounts_cli.main([*args, "--password-stdin"])


def test_the_first_adult_is_made_on_the_command_line(app, monkeypatch, db_url, capsys):
    assert run_cli(monkeypatch, db_url, "create-adult", "Alex", password=ADULT_PW) == 0
    assert "Alex is an Adult and can sign in now." in capsys.readouterr().out
    with EaterRepository(db_url) as eaters:
        assert [e.name for e in eaters.list()] == ["Alex"]  # a new Eater, for a new install
    assert sign_in(new_client(app), "Alex", ADULT_PW).status_code == 303


def test_the_first_adult_uses_an_eater_that_is_there_already(monkeypatch, db_url):
    import_sqlite(FIXTURE, db_url)  # the Pi's data first, then the account
    assert run_cli(monkeypatch, db_url, "create-adult", "mia", password=ADULT_PW) == 0
    with AccountRepository(db_url) as accounts, EaterRepository(db_url) as eaters:
        (mia,) = accounts.accounts()
        assert (mia.name, mia.role) == ("Mia", Role.ADULT)
        assert len(eaters.list()) == 3


def test_only_the_first_adult_comes_from_the_command_line(monkeypatch, db_url, capsys):
    run_cli(monkeypatch, db_url, "create-adult", "Alex", password=ADULT_PW)
    assert run_cli(monkeypatch, db_url, "create-adult", "Robin", password=ADULT_PW) == 1
    assert "An Adult account already exists" in capsys.readouterr().err


def test_the_command_line_gets_you_back_in(app, monkeypatch, db_url, household):
    assert run_cli(monkeypatch, db_url, "reset-password", "Alex", password="recovered pw 1") == 0
    assert sign_in(new_client(app), "Alex", "recovered pw 1").status_code == 303


def test_the_command_line_refuses_a_short_password(monkeypatch, db_url, capsys):
    assert run_cli(monkeypatch, db_url, "create-adult", "Alex", password="short") == 1
    assert "at least 10 characters" in capsys.readouterr().err


def test_the_importer_refuses_a_database_with_accounts(monkeypatch, db_url):
    from emu_the_menu.import_sqlite import ImportRefusedError

    run_cli(monkeypatch, db_url, "create-adult", "Alex", password=ADULT_PW)
    with pytest.raises(ImportRefusedError, match="accounts"):
        import_sqlite(FIXTURE, db_url)


def test_account_errors_are_value_errors_for_the_forms():
    assert issubclass(AccountError, ValueError)


def test_at_most_two_passwords_are_hashed_at_once(monkeypatch):
    """Each hash takes 19 MiB, so a burst of sign-in attempts queues for the hasher instead of
    taking the container's memory."""
    import threading
    import time

    from emu_the_menu import accounts as module

    running, most = [0], [0]
    lock = threading.Lock()

    class CountingHasher:
        def verify(self, hash_, password):
            with lock:
                running[0] += 1
                most[0] = max(most[0], running[0])
            time.sleep(0.05)
            with lock:
                running[0] -= 1

    monkeypatch.setattr(module, "_HASHER", CountingHasher())
    threads = [
        threading.Thread(target=module._verify, args=(module._DUMMY_HASH, "x")) for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert most[0] == module.HASHING_AT_ONCE == 2
