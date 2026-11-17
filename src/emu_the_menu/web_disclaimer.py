"""The disclaimer page, and recording that it was read and accepted."""

from collections.abc import Callable
from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from emu_the_menu.disclaimer import DISCLAIMER_VERSION, RETURN_PAGES
from emu_the_menu.repository import StockRepository
from emu_the_menu.templating import TEMPLATES


def create_router(database: str, today: Callable[[], date]) -> APIRouter:
    router = APIRouter()

    def page(request: Request, next_url: str | None, error: str | None = None, popup: bool = False):
        with StockRepository(database) as repo:
            acceptance = repo.disclaimer_acceptance()
        return TEMPLATES.TemplateResponse(
            request,
            "_disclaimer_panel.html" if popup else "disclaimer.html",
            {
                "version": DISCLAIMER_VERSION,
                "acceptance": acceptance,
                "current": bool(acceptance) and acceptance[0] == DISCLAIMER_VERSION,
                "next_url": next_url if next_url in RETURN_PAGES else None,
                "error": error,
                "popup": popup,
            },
            status_code=400 if error else 200,
        )

    @router.get("/disclaimer", response_class=HTMLResponse)
    def disclaimer(request: Request, next: str = ""):
        return page(request, next)

    @router.get("/disclaimer/panel", response_class=HTMLResponse)
    def panel(request: Request):
        """The disclaimer without the page around it, for the pop-up."""
        return page(request, None, popup=True)

    @router.post("/disclaimer/accept")
    def accept(
        request: Request, understood: str = Form(""), next: str = Form(""), popup: str = Form("")
    ):
        if not understood:
            error = "Tick the box to confirm that you have read and understood the disclaimer."
            return page(request, next, error, popup=bool(popup))
        with StockRepository(database) as repo:
            repo.accept_disclaimer(DISCLAIMER_VERSION, today())
        if popup:  # stay in the pop-up, which now says it was accepted
            return page(request, None, popup=True)
        # Only known pages may be returned to, so this cannot redirect elsewhere.
        target = next if next in RETURN_PAGES else "/disclaimer?accepted=1"
        return RedirectResponse(target, status_code=303)

    return router
