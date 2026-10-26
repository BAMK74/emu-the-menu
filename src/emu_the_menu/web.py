"""Web forms for Stock: add, adjust, delete, and a list sorted by expiry."""

import os
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from emu_the_menu.countries import COUNTRIES
from emu_the_menu.openfoodfacts import Fetch, LookupUnavailableError, find_product, http_fetch
from emu_the_menu.repository import ItemNotFoundError, StockRepository
from emu_the_menu.shelf_life import ShelfLife, ShelfLifeUnit
from emu_the_menu.stock import Location
from emu_the_menu.units import IncompatibleUnitError, known_units

_TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
_BLANK_FORM = {
    "barcode": "",
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


def create_app(
    db_path: str | Path | None = None,
    today: Callable[[], date] = date.today,
    fetch_product: Fetch = http_fetch,
) -> FastAPI:
    path = db_path or os.environ.get("EMU_DB", "emu.db")
    app = FastAPI(title="Emu the Menu")

    def get_repo() -> Iterator[StockRepository]:
        # One connection per request: sqlite connections are not shared across threads.
        with StockRepository(path) as repo:
            yield repo

    def page(
        request: Request,
        repo: StockRepository,
        form: dict,
        error: str | None = None,
        warning: dict | None = None,
        notice: str | None = None,
    ):
        return _TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "items": repo.list_items(),
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
                "countries": COUNTRIES,
                "country": repo.household_country(),
                "saved": request.query_params.get("saved") == "1",
            },
            status_code=400 if error else 409 if warning else 200,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, repo: StockRepository = Depends(get_repo)):
        return page(request, repo, _BLANK_FORM)

    def settings_page(request: Request, repo: StockRepository, error: str | None = None):
        return _TEMPLATES.TemplateResponse(
            request,
            "settings.html",
            {
                "countries": COUNTRIES,
                "country": repo.household_country(),
                "saved": request.query_params.get("saved") == "1",
                "error": error,
            },
            status_code=400 if error else 200,
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
        # Only the Stock page may be returned to, so this cannot redirect elsewhere.
        return RedirectResponse(
            "/?saved=1" if next == "/" else "/settings?saved=1", status_code=303
        )

    @app.post("/lookup")
    def look_up_barcode(
        request: Request,
        barcode: str = Form(""),
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
        """Fill in the Add form from a barcode. Never changes Stock: the user confirms."""
        form = {
            "barcode": barcode,
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
            notice = "Could not reach Open Food Facts. Enter the quantity by hand."
            return page(request, repo, form, notice=notice)
        if info is None:
            return page(request, repo, form, notice="No product found for that barcode.")
        if info.name and not ingredient.strip():
            form["ingredient"] = info.name
        found = f"Found {info.name or 'a product'}"
        if info.quantity:
            form["quantity"], form["unit"] = f"{info.quantity:g}", info.unit
            notice = f"{found}: pack size {info.quantity:g} {info.unit}."
        else:
            notice = f"{found}, but it has no pack size listed. Enter the quantity by hand."
        return page(request, repo, form, notice=f"{notice} Data from Open Food Facts.")

    @app.post("/items")
    def add_item(
        request: Request,
        barcode: str = Form(""),
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
