"""Web forms for Stock: add, adjust, delete, and a list sorted by expiry."""

import json
import os
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from emu_the_menu.backup import BackupError, latest_backup, snapshot_gz
from emu_the_menu.countries import COUNTRIES
from emu_the_menu.eaters import (
    DEFAULT_CALORIE_GOALS,
    Category,
    EaterRepository,
    parse_calorie_goal,
)
from emu_the_menu.openfoodfacts import Fetch, LookupUnavailableError, find_product, http_fetch
from emu_the_menu.page_fetch import fetch_page
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.repository import ItemNotFoundError, StockRepository
from emu_the_menu.shelf_life import ShelfLife, ShelfLifeUnit
from emu_the_menu.stock import Location
from emu_the_menu.templating import TEMPLATES as _TEMPLATES
from emu_the_menu.units import IncompatibleUnitError, display_quantity, known_units
from emu_the_menu.web_disclaimer import create_router as create_disclaimer_router
from emu_the_menu.web_eaters import create_router as create_eaters_router
from emu_the_menu.web_eaters import export_eaters
from emu_the_menu.web_ingredients import create_router as create_ingredients_router
from emu_the_menu.web_recipes import create_router as create_recipes_router

# Pages the Settings overlay may send you back to after saving.
_RETURN_PAGES = ("/", "/eaters")
_BLANK_FORM = {
    "barcode": "",
    "auto_ingredient": "",
    "auto_quantity": "",
    "ingredient": "",
    "quantity": "",
    "unit": "g",
    "location": "pantry",
    "expires_on": "",
    "in_amount": "",
    "in_unit": "days",
    "no_expiry": "",
}


def _unit_warning(
    name: str, stored_unit: str, entered_unit: str, action: str, fields: dict
) -> dict:
    """A warning with a one-tap fix that re-sends the same form after switching the unit."""
    return {
        "message": f"{name} is stored in {stored_unit}, but you entered {entered_unit}.",
        "button": f"Store {name} in {entered_unit} instead",
        "action": action,
        "fields": {**fields, "switch_unit": "on"},
    }


def _forget_previous_lookup(form: dict) -> None:
    """Clear the name and quantity an earlier lookup filled in, unless the user changed them."""
    if form["ingredient"] == form["auto_ingredient"]:
        form["ingredient"] = ""
    if form["quantity"] == form["auto_quantity"]:
        form["quantity"] = ""
    form["auto_ingredient"] = form["auto_quantity"] = ""


def create_app(
    db_path: str | Path | None = None,
    today: Callable[[], date] = date.today,
    fetch_product: Fetch = http_fetch,
    backup_dir: str | Path | None = None,
    fetch_recipe_page: Callable[[str], str] = fetch_page,
) -> FastAPI:
    path = db_path or os.environ.get("EMU_DB", "emu.db")
    backup_dir = backup_dir or os.environ.get("EMU_BACKUP_DIR")
    app = FastAPI(title="Emu the Menu")
    app.state.db_path = path  # read by the shared templates, for the disclaimer banner
    app.include_router(create_eaters_router(path))
    app.include_router(create_ingredients_router(path, fetch_product))
    app.include_router(create_recipes_router(path, fetch_recipe_page))
    app.include_router(create_disclaimer_router(path, today))
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    def get_repo() -> Iterator[StockRepository]:
        # One connection per request: sqlite connections are not shared across threads.
        with StockRepository(path) as repo:
            yield repo

    def backup_status() -> dict:
        """What Settings says about the nightly backup, flagging an old or missing one."""
        if not backup_dir:
            return {"text": "Nightly backups are not set up here.", "stale": False}
        newest = latest_backup(backup_dir)
        if newest is None:
            return {"text": "No nightly backup yet. Check the Pi.", "stale": True}
        age = max((today() - newest).days, 0)
        if age > 2:
            return {"text": f"Last nightly backup was {age} days ago. Check the Pi.", "stale": True}
        when = "today" if age == 0 else "yesterday" if age == 1 else f"{age} days ago"
        return {"text": f"Last nightly backup: {when}.", "stale": False}

    def page(
        request: Request,
        repo: StockRepository,
        form: dict,
        error: str | None = None,
        warning: dict | None = None,
        notice: str | None = None,
    ):
        items = repo.list_items()
        current = today()
        days_left = [(i.expires_on - current).days for i in items if i.expires_on]
        summary = {
            "total": len(items),
            "soon": sum(1 for d in days_left if 0 <= d <= 3),
            "expired": sum(1 for d in days_left if d < 0),
        }
        return _TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "items": items,
                "today": current,
                "summary": summary,
                "units": known_units(),
                "suggestions": {
                    i.name: {
                        "unit": i.base_unit.value,
                        "last": i.last_entry
                        and {"quantity": i.last_entry.quantity, "unit": i.last_entry.unit},
                        "shelf_life": i.shelf_life
                        and {"amount": i.shelf_life.amount, "unit": i.shelf_life.unit.value},
                    }
                    for i in repo.ingredients()
                },
                "shelf_life_units": [u.value for u in ShelfLifeUnit],
                "locations": [loc.value for loc in Location],
                "form": form,
                "error": error,
                "warning": warning,
                "notice": notice,
            },
            status_code=400 if error else 409 if warning else 200,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, repo: StockRepository = Depends(get_repo)):
        return page(request, repo, _BLANK_FORM)

    def calorie_context(goal_form: dict | None = None) -> dict:
        """What the Calorie goals section of Settings shows: the current defaults of each type,
        or what was typed in a bad try."""
        with EaterRepository(path) as eaters:
            defaults = eaters.default_goals()
        return {
            "categories": list(Category),
            "default_goals": {c.value: kcal for c, kcal in defaults.items()},
            "builtin_goals": {c.value: kcal for c, kcal in DEFAULT_CALORIE_GOALS.items()},
            "goal_form": goal_form or {c.value: str(kcal) for c, kcal in defaults.items()},
            "goals_open": goal_form is not None,
        }

    def settings_page(
        request: Request,
        repo: StockRepository,
        error: str | None = None,
        goal_form: dict | None = None,
    ):
        return _TEMPLATES.TemplateResponse(
            request,
            "settings.html",
            {
                "countries": COUNTRIES,
                "country": repo.household_country(),
                "saved": request.query_params.get("saved") == "1",
                "error": error,
                "backup": backup_status(),
                **calorie_context(goal_form),
            },
            status_code=400 if error else 200,
        )

    @app.get("/settings/panel", response_class=HTMLResponse)
    def settings_panel(request: Request, next: str = "", repo: StockRepository = Depends(get_repo)):
        """The settings form and backup section alone, for the overlay on any page."""
        return _TEMPLATES.TemplateResponse(
            request,
            "_settings_panel.html",
            {
                "countries": COUNTRIES,
                "country": repo.household_country(),
                "backup": backup_status(),
                "next_url": next if next in _RETURN_PAGES else None,
                **calorie_context(),
            },
        )

    @app.post("/settings/calorie-goals")
    def save_calorie_goals(
        request: Request,
        goal_man: str = Form(""),
        goal_woman: str = Form(""),
        goal_teen: str = Form(""),
        goal_child: str = Form(""),
        next: str = Form(""),
        repo: StockRepository = Depends(get_repo),
    ):
        typed = {
            Category.MAN: goal_man,
            Category.WOMAN: goal_woman,
            Category.TEEN: goal_teen,
            Category.CHILD: goal_child,
        }
        try:
            with EaterRepository(path) as eaters:
                eaters.set_default_goals({c: parse_calorie_goal(t) for c, t in typed.items()})
        except ValueError as exc:
            goal_form = {c.value: text for c, text in typed.items()}
            return settings_page(request, repo, str(exc), goal_form)
        # Only known pages may be returned to, so this cannot redirect elsewhere.
        return RedirectResponse(
            f"{next}?saved=1" if next in _RETURN_PAGES else "/settings?saved=1", status_code=303
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, repo: StockRepository = Depends(get_repo)):
        return settings_page(request, repo)

    @app.post("/settings")
    def save_settings(
        request: Request,
        country: str = Form(""),
        next: str = Form(""),
        repo: StockRepository = Depends(get_repo),
    ):
        try:
            repo.set_household_country(country)
        except ValueError as exc:
            return settings_page(request, repo, str(exc))
        # Only known pages may be returned to, so this cannot redirect elsewhere.
        return RedirectResponse(
            f"{next}?saved=1" if next in _RETURN_PAGES else "/settings?saved=1",
            status_code=303,
        )

    def download(content: bytes | str, media_type: str, filename: str) -> Response:
        return Response(
            content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @app.get("/export.json")
    def export_json(repo: StockRepository = Depends(get_repo)):
        """A readable copy of the ingredients and stock."""
        data = {
            "app": "emu-the-menu",
            "format": 1,
            "exported_on": today().isoformat(),
            **repo.export_data(),
        }
        with EaterRepository(path) as eaters:
            data["default_calorie_goals"] = {
                c.value: kcal for c, kcal in eaters.default_goals().items()
            }
            data["eaters"] = export_eaters(eaters.list())
        with RecipeRepository(path) as recipes:
            data["recipes"] = recipes.export_data()
        body = json.dumps(data, indent=2, ensure_ascii=False)
        return download(body, "application/json", f"emu-{today().isoformat()}.json")

    @app.get("/export.db.gz")
    def export_database(repo: StockRepository = Depends(get_repo)):
        """A fresh copy of the whole database, restorable like a nightly backup.

        Opening the repository above creates the database on a first-ever request.
        """
        try:
            packed = snapshot_gz(path)
        except BackupError as exc:
            raise HTTPException(500, str(exc)) from exc
        return download(packed, "application/gzip", f"emu-{today().isoformat()}.db.gz")

    @app.post("/lookup")
    def look_up_barcode(
        request: Request,
        barcode: str = Form(""),
        auto_ingredient: str = Form(""),
        auto_quantity: str = Form(""),
        ingredient: str = Form(""),
        quantity: str = Form(""),
        unit: str = Form(""),
        location: str = Form(""),
        expires_on: str = Form(""),
        in_amount: str = Form(""),
        in_unit: str = Form("days"),
        no_expiry: str = Form(""),
        repo: StockRepository = Depends(get_repo),
    ):
        """Fill in the Add form from a barcode. Never changes Stock: the user confirms.

        The name and quantity a lookup fills in are remembered in `auto_*`, so the
        next lookup can replace them without ever replacing something the user typed.
        """
        form = {
            "barcode": barcode,
            "auto_ingredient": auto_ingredient,
            "auto_quantity": auto_quantity,
            "ingredient": ingredient,
            "quantity": quantity,
            "unit": unit,
            "location": location,
            "expires_on": expires_on,
            "in_amount": in_amount,
            "in_unit": in_unit,
            "no_expiry": no_expiry,
        }
        try:
            info = find_product(barcode, repo, fetch_product)
        except ValueError as exc:
            return page(request, repo, form, str(exc))
        except LookupUnavailableError:
            _forget_previous_lookup(form)
            notice = "Could not reach Open Food Facts. Enter the quantity by hand."
            return page(request, repo, form, notice=notice)
        _forget_previous_lookup(form)
        if info is None:
            return page(request, repo, form, notice="No product found for that barcode.")
        if info.name and not form["ingredient"].strip():
            form["ingredient"] = form["auto_ingredient"] = info.name
        found = f"Found {info.name or 'a product'}"
        if not info.quantity:
            notice = f"{found}, but it has no pack size listed. Enter the quantity by hand."
        elif form["quantity"].strip():
            notice = f"{found}: pack size {info.quantity:g} {info.unit}. Your quantity was kept."
        else:
            form["quantity"] = form["auto_quantity"] = f"{info.quantity:g}"
            form["unit"] = info.unit
            notice = f"{found}: pack size {info.quantity:g} {info.unit}."
        return page(request, repo, form, notice=f"{notice} Data from Open Food Facts.")

    @app.post("/items")
    def add_item(
        request: Request,
        barcode: str = Form(""),
        auto_ingredient: str = Form(""),
        auto_quantity: str = Form(""),
        ingredient: str = Form(""),
        quantity: str = Form(""),
        unit: str = Form(""),
        location: str = Form(""),
        expires_on: str = Form(""),
        in_amount: str = Form(""),
        in_unit: str = Form("days"),
        no_expiry: str = Form(""),
        switch_unit: str = Form(""),
        repo: StockRepository = Depends(get_repo),
    ):
        form = {
            "barcode": barcode,
            "auto_ingredient": auto_ingredient,
            "auto_quantity": auto_quantity,
            "ingredient": ingredient,
            "quantity": quantity,
            "unit": unit,
            "location": location,
            "expires_on": expires_on,
            "in_amount": in_amount,
            "in_unit": in_unit,
            "no_expiry": no_expiry,
        }
        try:
            if not ingredient.strip():
                raise ValueError("Ingredient name is required")
            expires, shelf_life = None, None
            if not no_expiry:
                if expires_on and in_amount.strip():
                    raise ValueError("Use either an expiry date or a number of days, not both")
                if expires_on:
                    try:
                        expires = date.fromisoformat(expires_on)
                    except ValueError:
                        raise ValueError("Pick the expiry date from the calendar") from None
                elif in_amount.strip():
                    shelf_life = ShelfLife.parse(in_amount, in_unit)
                    expires = shelf_life.expires_from(today())
            amount = float(quantity)
            if switch_unit:
                repo.change_base_unit(ingredient, unit)
            repo.add(ingredient, amount, unit, Location(location), expires, shelf_life)
        except IncompatibleUnitError:
            name = ingredient.strip().lower()
            stored = next((i for i in repo.ingredients() if i.name == name), None)
            return page(
                request,
                repo,
                form,
                warning=_unit_warning(name, stored.base_unit.value, unit, "/items", form)
                if stored
                else None,
            )
        except ValueError as exc:
            return page(request, repo, form, str(exc))
        return RedirectResponse("/", status_code=303)

    def find_item(repo: StockRepository, item_id: int):
        item = next((i for i in repo.list_items() if i.id == item_id), None)
        if item is None:
            raise HTTPException(404, "No such item")
        return item

    def item_form(item) -> dict:
        """The popup form's values for an item, in the unit people read."""
        quantity, unit = display_quantity(item.quantity, item.base_unit)
        return {
            "quantity": f"{quantity:g}",
            "unit": unit,
            "location": item.location.value,
            "expires_on": item.expires_on.isoformat() if item.expires_on else "",
            "no_expiry": "" if item.expires_on else "on",
        }

    def item_context(item, form: dict, error: str | None = None) -> dict:
        return {
            "item": item,
            "form": form,
            "error": error,
            "units": known_units(),
            "locations": [loc.value for loc in Location],
        }

    @app.get("/items/{item_id}/panel", response_class=HTMLResponse)
    def item_panel(request: Request, item_id: int, repo: StockRepository = Depends(get_repo)):
        """Just the adjust form, for the popup on the Stock page."""
        item = find_item(repo, item_id)
        return _TEMPLATES.TemplateResponse(
            request, "_item_panel.html", item_context(item, item_form(item))
        )

    @app.get("/items/{item_id}", response_class=HTMLResponse)
    def item_page(request: Request, item_id: int, repo: StockRepository = Depends(get_repo)):
        """The same form as a page of its own, for use without scripting."""
        item = find_item(repo, item_id)
        return _TEMPLATES.TemplateResponse(
            request, "item.html", item_context(item, item_form(item))
        )

    @app.post("/items/{item_id}")
    def update_item(
        request: Request,
        item_id: int,
        quantity: str = Form(""),
        unit: str = Form(""),
        location: str = Form(""),
        expires_on: str = Form(""),
        no_expiry: str = Form(""),
        switch_unit: str = Form(""),
        repo: StockRepository = Depends(get_repo),
    ):
        item = find_item(repo, item_id)
        form = {
            "quantity": quantity,
            "unit": unit,
            "location": location,
            "expires_on": expires_on,
            "no_expiry": no_expiry,
        }
        try:
            try:
                amount = float(quantity)
            except ValueError:
                raise ValueError("Enter the quantity as a number") from None
            try:
                where = Location(location)
            except ValueError:
                raise ValueError("Choose where it is kept") from None
            expires = None
            if expires_on and not no_expiry:
                try:
                    expires = date.fromisoformat(expires_on)
                except ValueError:
                    raise ValueError("Pick the expiry date from the calendar") from None
            if switch_unit:
                repo.change_base_unit(item.ingredient, unit)
            repo.update_item(item_id, amount, unit, where, expires)
        except ItemNotFoundError:
            raise HTTPException(404, "No such item") from None
        except IncompatibleUnitError:
            warning = _unit_warning(
                item.ingredient, item.base_unit.value, unit, f"/items/{item_id}", form
            )
            return page(request, repo, _BLANK_FORM, warning=warning)
        except ValueError as exc:
            return _TEMPLATES.TemplateResponse(
                request, "item.html", item_context(item, form, str(exc)), status_code=400
            )
        return RedirectResponse("/", status_code=303)

    @app.post("/items/{item_id}/quantity")
    def set_quantity(
        request: Request,
        item_id: int,
        quantity: str = Form(""),
        unit: str = Form(""),
        switch_unit: str = Form(""),
        repo: StockRepository = Depends(get_repo),
    ):
        item = next((i for i in repo.list_items() if i.id == item_id), None)
        if item is None:
            raise HTTPException(404, "No such item")
        try:
            amount = float(quantity)
            if switch_unit:
                repo.change_base_unit(item.ingredient, unit)
            repo.set_quantity(item_id, amount, unit)
        except ItemNotFoundError:
            raise HTTPException(404, "No such item") from None
        except IncompatibleUnitError:
            fields = {"quantity": quantity, "unit": unit}
            warning = _unit_warning(
                item.ingredient, item.base_unit.value, unit, f"/items/{item_id}/quantity", fields
            )
            return page(request, repo, _BLANK_FORM, warning=warning)
        except ValueError as exc:
            return page(request, repo, _BLANK_FORM, str(exc))
        return RedirectResponse("/", status_code=303)

    @app.post("/items/{item_id}/delete")
    def delete_item(item_id: int, repo: StockRepository = Depends(get_repo)):
        try:
            repo.delete(item_id)
        except ItemNotFoundError:
            raise HTTPException(404, "No such item") from None
        return RedirectResponse("/", status_code=303)

    return app
