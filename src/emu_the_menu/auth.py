"""Signing in, and the guard every route passes through (ADR 0008).

`guard` is a dependency on the whole app, so a route added later is protected without anyone
remembering to. On every route it:

- lets through the few that need no account (`OPEN_ROUTES`);
- sends anyone signed out to the sign-in page (a 303, never a 401: the Server's uptime monitor
  counts any status under 400 as up);
- checks the CSRF token on every POST, from the form's `csrf_token` field or an `X-CSRF-Token`
  header (the pages' scripts send that one);
- lets a Teen POST only to `TEEN_ROUTES`; everything else that changes data is for Adults.

Static files are not routes and are served to anyone. Pages show `{{ csrf_field() }}` in every
form that posts and `<meta name="csrf-token">` for scripts.
"""

import secrets
from datetime import timedelta
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from emu_the_menu.accounts import (
    MIN_PASSWORD,
    SESSION_DAYS,
    Account,
    AccountError,
    AccountRepository,
    Role,
    Session,
)
from emu_the_menu.eaters import Category, EaterRepository
from emu_the_menu.templating import TEMPLATES

SESSION_COOKIE = "emu_session"
SIGN_IN_COOKIE = "emu_sign_in"  # the CSRF token of the sign-in form itself (double submit)

# Routes anyone may use, signed in or not, by method and path pattern.
OPEN_ROUTES = {
    ("GET", "/sign-in"),
    ("POST", "/sign-in"),
    ("GET", "/healthz"),
    ("GET", "/disclaimer"),  # readable before signing in; accepting it needs an Adult
}

# What a Teen may change. Everything else that changes data needs an Adult.
TEEN_ROUTES = {
    "/menus/{menu_id}/meals/{meal_id}/cooked",
    "/menus/{menu_id}/meals/{meal_id}/not-cooked",
    "/eaters/{eater_id}/preferences",  # their own only: the route checks
    "/account/password",
    "/sign-out",
}

# Tests only (tests/conftest.py): treat a request with no session as this account and skip the
# CSRF check, so the tests of pages need not sign in. None, as here, in the app.
test_account: Account | None = None


class SignInRequired(Exception):  # noqa: N818 - a signal, not an error
    def __init__(self, next_path: str):
        self.next_path = next_path


class Forbidden(Exception):  # noqa: N818
    def __init__(self, message: str, status: int = 403):
        self.message = message
        self.status = status


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


def current_session(request: Request) -> Session | None:
    with AccountRepository(request.app.state.database) as accounts:
        return accounts.session(request.cookies.get(SESSION_COOKIE))


async def guard(request: Request) -> None:
    """Every route depends on this: see the module's docstring.

    It is async only to read a posted form; the session is looked up in a worker thread, since
    waiting for a pooled connection on the event loop would stall every request.
    """
    method, pattern = request.method, _route_path(request)
    if (method, pattern) in OPEN_ROUTES or (method == "HEAD" and ("GET", pattern) in OPEN_ROUTES):
        request.state.session = (
            await run_in_threadpool(current_session, request) if pattern == "/disclaimer" else None
        )
        return
    session = await run_in_threadpool(current_session, request)
    if session is None and test_account is not None:
        request.state.session = Session(test_account, "", "")
        return
    if session is None:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise SignInRequired(target)
    request.state.session = session
    if method in ("GET", "HEAD"):
        return
    sent = request.headers.get("x-csrf-token")
    if sent is None:
        form = await request.form()
        sent = form.get("csrf_token")
    if not isinstance(sent, str) or not secrets.compare_digest(sent, session.csrf_token):
        raise Forbidden(
            "This form had expired, so nothing was changed."
            " Go back, reload the page and try again.",
            400,
        )
    if not session.account.is_adult and pattern not in TEEN_ROUTES:
        raise Forbidden("Only an Adult can change this.")


def account_of(request: Request) -> Account:
    """The signed-in account of a request that passed `guard`."""
    return request.state.session.account


def _safe_next(next_path: str | None) -> str:
    """Where to go after signing in: a path on this site, never another site."""
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return "/"
    parts = urlsplit(next_path)
    if parts.scheme or parts.netloc or "\\" in next_path:
        return "/"
    return next_path


def _set_cookie(response: Response, name: str, value: str, max_age: int | None) -> None:
    response.set_cookie(
        name, value, max_age=max_age, path="/", secure=True, httponly=True, samesite="lax"
    )


def message_page(request: Request, message: str, status: int) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "message.html", {"message": message}, status_code=status
    )


def install(app) -> None:
    """Add the guard's answers to the app: sign-in redirects and refusals as pages."""

    @app.exception_handler(SignInRequired)
    async def to_sign_in(request: Request, exc: SignInRequired):
        target = "/sign-in" if exc.next_path == "/" else f"/sign-in?next={quote(exc.next_path)}"
        return RedirectResponse(target, status_code=303)

    @app.exception_handler(Forbidden)
    def refused(request: Request, exc: Forbidden):  # not async: the page reads the database
        return message_page(request, exc.message, exc.status)


def _wait_text(wait: timedelta) -> str:
    seconds = int(wait.total_seconds()) + 1
    if seconds < 90:
        return f"{seconds} seconds"
    return f"{(seconds + 59) // 60} minutes"


def create_router(database: str) -> APIRouter:
    router = APIRouter()

    def sign_in_page(
        request: Request, name: str = "", error: str | None = None, status=200, next_path=None
    ):
        """The form. After a failed try `next_path` comes from the form, so it survives."""
        token = request.cookies.get(SIGN_IN_COOKIE) or secrets.token_urlsafe(32)
        response = TEMPLATES.TemplateResponse(
            request,
            "sign_in.html",
            {
                "name": name,
                "error": error,
                "next_path": _safe_next(next_path or request.query_params.get("next")),
                "sign_in_token": token,
            },
            status_code=status,
        )
        _set_cookie(response, SIGN_IN_COOKIE, token, max_age=None)
        return response

    @router.get("/sign-in", response_class=HTMLResponse)
    def show_sign_in(request: Request):
        if current_session(request):
            return RedirectResponse(_safe_next(request.query_params.get("next")), status_code=303)
        return sign_in_page(request)

    @router.post("/sign-in")
    def sign_in(
        request: Request,
        name: str = Form(""),
        password: str = Form(""),
        next: str = Form("/"),
        csrf_token: str = Form(""),
    ):
        expected = request.cookies.get(SIGN_IN_COOKIE, "")
        if not expected or not secrets.compare_digest(csrf_token, expected):
            return sign_in_page(request, name, "The page had expired. Try again.", 400, next)
        with AccountRepository(database) as accounts:
            wait = accounts.wait_before_trying(name)
            if wait:
                return sign_in_page(
                    request,
                    name,
                    f"Too many tries. Wait {_wait_text(wait)} before trying again.",
                    429,
                    next,
                )
            account = accounts.check_sign_in(name, password)
            if account is None:
                return sign_in_page(
                    request, name, "That name and password do not match.", 400, next
                )
            token, _ = accounts.start_session(account.id)
        response = RedirectResponse(_safe_next(next), status_code=303)
        _set_cookie(response, SESSION_COOKIE, token, max_age=SESSION_DAYS * 24 * 3600)
        response.delete_cookie(SIGN_IN_COOKIE, path="/")
        return response

    @router.post("/sign-out")
    def sign_out(request: Request):
        with AccountRepository(database) as accounts:
            accounts.end_session(request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/sign-in", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response

    # --- your own account ---------------------------------------------------------------------

    def account_page(request: Request, error=None, saved=False, status=200):
        return TEMPLATES.TemplateResponse(
            request,
            "account.html",
            {"me": account_of(request), "error": error, "saved": saved, "min": MIN_PASSWORD},
            status_code=status,
        )

    @router.get("/account", response_class=HTMLResponse)
    def show_account(request: Request):
        return account_page(request, saved=request.query_params.get("saved") == "1")

    @router.post("/account/password")
    def change_own_password(
        request: Request,
        current: str = Form(""),
        new: str = Form(""),
        again: str = Form(""),
    ):
        me = account_of(request)
        if new != again:
            return account_page(request, "The two new passwords are not the same.", status=400)
        with AccountRepository(database) as accounts:
            if accounts.check_sign_in(me.name, current) is None:
                return account_page(request, "The current password is not right.", status=400)
            try:
                accounts.set_password(me.id, new, keep_session=request.state.session.token_hash)
            except AccountError as exc:
                return account_page(request, str(exc), status=400)
        return RedirectResponse("/account?saved=1", status_code=303)

    # --- accounts, for Adults -----------------------------------------------------------------

    def accounts_page(request: Request, error=None, saved=None, status=200):
        with AccountRepository(database) as accounts, EaterRepository(database) as eaters:
            by_eater = {a.eater_id: a for a in accounts.accounts()}
            people = [(e, by_eater.get(e.id)) for e in eaters.list()]
        return TEMPLATES.TemplateResponse(
            request,
            "accounts.html",
            {
                "people": people,
                "roles": list(Role),
                "child": Category.CHILD,
                "me": account_of(request),
                "error": error,
                "saved": saved,
                "min": MIN_PASSWORD,
            },
            status_code=status,
        )

    @router.get("/accounts", response_class=HTMLResponse)
    def show_accounts(request: Request):
        if not account_of(request).is_adult:
            raise Forbidden("Only an Adult can manage accounts.")
        return accounts_page(request, saved=request.query_params.get("saved"))

    def change(request: Request, action, done: str):
        try:
            with AccountRepository(database) as accounts:
                action(accounts)
        except AccountError as exc:
            return accounts_page(request, str(exc), status=400)
        return RedirectResponse(f"/accounts?saved={quote(done)}", status_code=303)

    @router.post("/accounts")
    def add_account(
        request: Request,
        eater_id: int = Form(...),
        role: str = Form("teen"),
        password: str = Form(""),
    ):
        try:
            chosen = Role(role)
        except ValueError:
            return accounts_page(request, "Choose Adult or Teen.", status=400)
        return change(
            request, lambda a: a.create(eater_id, chosen, password), "The account was added."
        )

    @router.post("/accounts/{account_id}/role")
    def set_role(request: Request, account_id: int, role: str = Form("")):
        try:
            chosen = Role(role)
        except ValueError:
            return accounts_page(request, "Choose Adult or Teen.", status=400)
        return change(request, lambda a: a.set_role(account_id, chosen), "The role was changed.")

    @router.post("/accounts/{account_id}/password")
    def reset_password(request: Request, account_id: int, password: str = Form("")):
        keep = request.state.session.token_hash if account_of(request).id == account_id else None
        return change(
            request,
            lambda a: a.set_password(account_id, password, keep_session=keep),
            "The password was changed, and that account was signed out everywhere else.",
        )

    @router.post("/accounts/{account_id}/delete")
    def remove_account(request: Request, account_id: int):
        return change(request, lambda a: a.remove(account_id), "The account was removed.")

    return router
