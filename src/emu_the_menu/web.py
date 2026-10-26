"""Web forms for Stock: add, adjust, delete, and a list sorted by expiry."""

import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from emu_the_menu.repository import ItemNotFoundError, StockRepository
from emu_the_menu.stock import Location
from emu_the_menu.units import known_units

_TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
_BLANK_FORM = {
    "ingredient": "",
    "quantity": "",
    "unit": "g",
    "location": "pantry",
    "expires_on": "",
}


def create_app(db_path: str | Path | None = None) -> FastAPI:
    path = db_path or os.environ.get("EMU_DB", "emu.db")
    app = FastAPI(title="Emu the Menu")

    def get_repo() -> Iterator[StockRepository]:
        # One connection per request: sqlite connections are not shared across threads.
        with StockRepository(path) as repo:
            yield repo

    def page(request: Request, repo: StockRepository, form: dict, error: str | None = None):
        return _TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "items": repo.list_items(),
                "units": known_units(),
                "locations": [loc.value for loc in Location],
                "form": form,
                "error": error,
            },
            status_code=400 if error else 200,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, repo: StockRepository = Depends(get_repo)):
        return page(request, repo, _BLANK_FORM)

    @app.post("/items")
    def add_item(
        request: Request,
        ingredient: str = Form(""),
        quantity: str = Form(""),
        unit: str = Form(""),
        location: str = Form(""),
        expires_on: str = Form(""),
        repo: StockRepository = Depends(get_repo),
    ):
        form = {
            "ingredient": ingredient,
            "quantity": quantity,
            "unit": unit,
            "location": location,
            "expires_on": expires_on,
        }
        try:
            if not ingredient.strip():
                raise ValueError("Ingredient name is required")
            repo.add(
                ingredient,
                float(quantity),
                unit,
                Location(location),
                date.fromisoformat(expires_on) if expires_on else None,
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
        repo: StockRepository = Depends(get_repo),
    ):
        try:
            repo.set_quantity(item_id, float(quantity), unit)
        except ItemNotFoundError:
            raise HTTPException(404, "No such item") from None
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
