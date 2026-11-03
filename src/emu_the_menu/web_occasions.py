"""The Occasions page (`/occasions`): add, change and remove Occasions, and import public holidays
for the Household's country."""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from emu_the_menu.holidays import HolidaysUnavailableError, fetch_holidays, http_get_json
from emu_the_menu.occasion_repository import OccasionNotFoundError, OccasionRepository
from emu_the_menu.occasions import MAX_EXTRA_PERCENT, MAX_HEADCOUNT, read_form
from emu_the_menu.repository import StockRepository
from emu_the_menu.templating import TEMPLATES
from emu_the_menu.web_menu import day_label


def create_router(db_path: str | Path, today=date.today, get_json=http_get_json) -> APIRouter:
    router = APIRouter()

    def page(
        request: Request,
        form: dict | None = None,
        error: str | None = None,
        notice: str | None = None,
        status: int = 200,
    ):
        with StockRepository(db_path) as stock:
            country = stock.household_country()
        with OccasionRepository(db_path) as repo:
            upcoming = repo.from_day(today())
        year = today().year
        return TEMPLATES.TemplateResponse(
            request,
            "occasions.html",
            {
                "occasions": upcoming,
                "form": form or {"name": "", "day": "", "headcount": "", "extra": "", "tags": ""},
                "error": error,
                "notice": notice,
                "country": country,
                "years": (year, year + 1),
                "label": day_label,
                "max_headcount": MAX_HEADCOUNT,
                "max_extra": MAX_EXTRA_PERCENT,
            },
            status_code=status,
        )

    @router.get("/occasions", response_class=HTMLResponse)
    def occasions(request: Request, imported: int | None = None):
        notice = None
        if imported is not None:
            notice = (
                f"Added {imported} public holiday{'' if imported == 1 else 's'}."
                if imported
                else "Nothing new: those holidays are already here."
            )
        return page(request, notice=notice)

    @router.post("/occasions")
    def add(
        request: Request,
        name: str = Form(""),
        day: str = Form(""),
        headcount: str = Form(""),
        extra: str = Form(""),
        tags: str = Form(""),
    ):
        try:
            fields = read_form(name, day, headcount, extra, tags)
        except ValueError as exc:
            typed = {"name": name, "day": day, "headcount": headcount, "extra": extra, "tags": tags}
            return page(request, typed, str(exc), status=400)
        with OccasionRepository(db_path) as repo:
            repo.add(*fields)
        return RedirectResponse("/occasions", status_code=303)

    @router.post("/occasions/holidays")
    def import_holidays(request: Request, year: str = Form("")):
        with StockRepository(db_path) as stock:
            country = stock.household_country()
        try:
            wanted = int(year)
            if wanted not in (today().year, today().year + 1):
                raise ValueError("Choose this year or next year")
            if not country:
                raise ValueError("Set the country in Settings first")
            found = fetch_holidays(wanted, country, get_json)
        except HolidaysUnavailableError as exc:
            return page(request, error=f"{exc}. Try again later.", status=400)
        except ValueError as exc:
            return page(request, error=str(exc), status=400)
        with OccasionRepository(db_path) as repo:
            added = repo.add_holidays(found)
        return RedirectResponse(f"/occasions?imported={added}", status_code=303)

    @router.post("/occasions/{occasion_id}")
    def update(
        request: Request,
        occasion_id: int,
        name: str = Form(""),
        day: str = Form(""),
        headcount: str = Form(""),
        extra: str = Form(""),
        tags: str = Form(""),
    ):
        try:
            fields = read_form(name, day, headcount, extra, tags)
            with OccasionRepository(db_path) as repo:
                repo.update(occasion_id, *fields)
        except ValueError as exc:
            return page(request, error=str(exc), status=400)
        except OccasionNotFoundError:
            raise HTTPException(404, "No such occasion") from None
        return RedirectResponse("/occasions", status_code=303)

    @router.post("/occasions/{occasion_id}/delete")
    def delete(occasion_id: int):
        try:
            with OccasionRepository(db_path) as repo:
                repo.delete(occasion_id)
        except OccasionNotFoundError:
            raise HTTPException(404, "No such occasion") from None
        return RedirectResponse("/occasions", status_code=303)

    return router
