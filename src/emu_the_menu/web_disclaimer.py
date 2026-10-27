"""The disclaimer page, and recording that it was read and accepted."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from emu_the_menu.disclaimer import DISCLAIMER_VERSION, RETURN_PAGES
from emu_the_menu.repository import StockRepository
from emu_the_menu.templating import TEMPLATES


def create_router(db_path: str | Path, today: Callable[[], date]) -> APIRouter:
    router = APIRouter()

    def page(request: Request, next_url: str | None, error: str | None = None):
        with StockRepository(db_path) as repo:
            acceptance = repo.disclaimer_acceptance()
        return TEMPLATES.TemplateResponse(
            request,
            "disclaimer.html",
            {
                "version": DISCLAIMER_VERSION,
                "acceptance": acceptance,
                "current": bool(acceptance) and acceptance[0] == DISCLAIMER_VERSION,
                "next_url": next_url if next_url in RETURN_PAGES else None,
                "error": error,
            },
            status_code=400 if error else 200,
        )

    @router.get("/disclaimer", response_class=HTMLResponse)
    def disclaimer(request: Request, next: str = ""):
        return page(request, next)

    @router.post("/disclaimer/accept")
    def accept(request: Request, understood: str = Form(""), next: str = Form("")):
        if not understood:
            error = "Tick the box to confirm that you have read and understood the disclaimer."
            return page(request, next, error)
        with StockRepository(db_path) as repo:
            repo.accept_disclaimer(DISCLAIMER_VERSION, today())
        # Only known pages may be returned to, so this cannot redirect elsewhere.
        target = next if next in RETURN_PAGES else "/disclaimer?accepted=1"
        return RedirectResponse(target, status_code=303)

    return router
