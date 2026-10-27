"""Web forms for Eaters: add, edit and remove the people the Household cooks for."""

from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from emu_the_menu.eaters import (
    Category,
    Eater,
    EaterNotFoundError,
    EaterRepository,
    clean_name,
    parse_calorie_goal,
    parse_terms,
)
from emu_the_menu.templating import TEMPLATES

_BLANK_FORM = {
    "name": "",
    "category": Category.WOMAN.value,
    "calorie_goal": "",
    "allergies": "",
    "dislikes": "",
}


def _read_form(
    name: str, category: str, calorie_goal: str, allergies: str, dislikes: str
) -> tuple[str, Category, int | None, tuple[str, ...], tuple[str, ...]]:
    """Check what the form sent. Raises ValueError with a message the page can show."""
    try:
        parsed_category = Category(category)
    except ValueError:
        raise ValueError("Choose a category") from None
    return (
        clean_name(name),
        parsed_category,
        parse_calorie_goal(calorie_goal),
        parse_terms(allergies),
        parse_terms(dislikes),
    )


def create_router(db_path: str | Path) -> APIRouter:
    router = APIRouter()

    def get_repo() -> Iterator[EaterRepository]:
        with EaterRepository(db_path) as repo:
            yield repo

    def page(
        request: Request, repo: EaterRepository, form: dict | None = None, error: str | None = None
    ):
        return TEMPLATES.TemplateResponse(
            request,
            "eaters.html",
            {
                "eaters": repo.list(),
                "categories": list(Category),
                "form": form or _BLANK_FORM,
                "error": error,
            },
            status_code=400 if error else 200,
        )

    @router.get("/eaters", response_class=HTMLResponse)
    def eaters(request: Request, repo: EaterRepository = Depends(get_repo)):
        return page(request, repo)

    @router.post("/eaters")
    def add_eater(
        request: Request,
        name: str = Form(""),
        category: str = Form(""),
        calorie_goal: str = Form(""),
        allergies: str = Form(""),
        dislikes: str = Form(""),
        repo: EaterRepository = Depends(get_repo),
    ):
        form = {
            "name": name,
            "category": category,
            "calorie_goal": calorie_goal,
            "allergies": allergies,
            "dislikes": dislikes,
        }
        try:
            repo.add(*_read_form(name, category, calorie_goal, allergies, dislikes))
        except ValueError as exc:
            return page(request, repo, form, str(exc))
        return RedirectResponse("/eaters", status_code=303)

    @router.post("/eaters/{eater_id}")
    def update_eater(
        request: Request,
        eater_id: int,
        name: str = Form(""),
        category: str = Form(""),
        calorie_goal: str = Form(""),
        allergies: str = Form(""),
        dislikes: str = Form(""),
        repo: EaterRepository = Depends(get_repo),
    ):
        if repo.get(eater_id) is None:
            raise HTTPException(404, "No such eater")
        try:
            repo.update(eater_id, *_read_form(name, category, calorie_goal, allergies, dislikes))
        except ValueError as exc:
            return page(request, repo, error=str(exc))
        except EaterNotFoundError:
            raise HTTPException(404, "No such eater") from None
        return RedirectResponse("/eaters", status_code=303)

    @router.post("/eaters/{eater_id}/delete")
    def delete_eater(eater_id: int, repo: EaterRepository = Depends(get_repo)):
        try:
            repo.delete(eater_id)
        except EaterNotFoundError:
            raise HTTPException(404, "No such eater") from None
        return RedirectResponse("/eaters", status_code=303)

    return router


def export_eaters(eaters: list[Eater]) -> list[dict]:
    """Eaters as plain data for the JSON export."""
    return [
        {
            "name": eater.name,
            "category": eater.category.value,
            "calorie_goal": eater.calorie_goal,
            "calorie_goal_is_custom": eater.has_custom_goal,
            "allergies": list(eater.allergies),
            "dislikes": list(eater.dislikes),
        }
        for eater in eaters
    ]
