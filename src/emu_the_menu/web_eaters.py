"""Web forms for Eaters: add, edit and remove the people the Household cooks for."""

from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from emu_the_menu.eaters import (
    CALORIE_ADJUSTMENTS,
    DEFAULT_CALORIE_GOALS,
    Category,
    Diet,
    Eater,
    EaterNotFoundError,
    EaterRepository,
    clean_name,
    parse_calorie_adjustment,
    parse_calorie_goal,
    parse_diet,
    parse_terms,
)
from emu_the_menu.templating import TEMPLATES

_BLANK_FORM = {
    "name": "",
    "category": Category.WOMAN.value,
    "calorie_adjustment": "0",
    "allergies": "",
    "dislikes": "",
    "diet": Diet.OMNIVORE.value,
}


def _read_form(
    name: str,
    category: str,
    calorie_adjustment: str,
    allergies: str,
    dislikes: str,
    diet: str,
) -> tuple[str, Category, int, tuple[str, ...], tuple[str, ...], Diet]:
    """Check what the form sent. Raises ValueError with a message the page can show."""
    try:
        parsed_category = Category(category)
    except ValueError:
        raise ValueError("Choose a category") from None
    return (
        clean_name(name),
        parsed_category,
        parse_calorie_adjustment(calorie_adjustment),
        parse_terms(allergies),
        parse_terms(dislikes),
        parse_diet(diet),
    )


def create_router(db_path: str | Path) -> APIRouter:
    router = APIRouter()

    def get_repo() -> Iterator[EaterRepository]:
        with EaterRepository(db_path) as repo:
            yield repo

    def page(
        request: Request,
        repo: EaterRepository,
        form: dict | None = None,
        goal_form: dict | None = None,
        error: str | None = None,
        defaults_open: bool = False,
    ):
        defaults = repo.default_goals()
        return TEMPLATES.TemplateResponse(
            request,
            "eaters.html",
            {
                "eaters": repo.list(),
                "categories": list(Category),
                "diets": list(Diet),
                "adjustments": CALORIE_ADJUSTMENTS,
                "form": form or _BLANK_FORM,
                "default_goals": {c.value: kcal for c, kcal in defaults.items()},
                "builtin_goals": {c.value: kcal for c, kcal in DEFAULT_CALORIE_GOALS.items()},
                # What the defaults form shows: the current goals, or what was typed in a bad try.
                "goal_form": goal_form or {c.value: str(kcal) for c, kcal in defaults.items()},
                "error": error,
                "defaults_open": defaults_open,
            },
            status_code=400 if error else 200,
        )

    @router.get("/eaters", response_class=HTMLResponse)
    def eaters(request: Request, repo: EaterRepository = Depends(get_repo)):
        return page(request, repo)

    # Registered before "/eaters/{eater_id}", which would otherwise try to read "defaults" as an id.
    @router.post("/eaters/defaults")
    def save_default_goals(
        request: Request,
        goal_man: str = Form(""),
        goal_woman: str = Form(""),
        goal_teen: str = Form(""),
        goal_child: str = Form(""),
        repo: EaterRepository = Depends(get_repo),
    ):
        typed = {
            Category.MAN: goal_man,
            Category.WOMAN: goal_woman,
            Category.TEEN: goal_teen,
            Category.CHILD: goal_child,
        }
        try:
            repo.set_default_goals({c: parse_calorie_goal(text) for c, text in typed.items()})
        except ValueError as exc:
            goal_form = {c.value: text for c, text in typed.items()}
            return page(request, repo, goal_form=goal_form, error=str(exc), defaults_open=True)
        return RedirectResponse("/eaters", status_code=303)

    @router.post("/eaters")
    def add_eater(
        request: Request,
        name: str = Form(""),
        category: str = Form(""),
        calorie_adjustment: str = Form("0"),
        allergies: str = Form(""),
        dislikes: str = Form(""),
        diet: str = Form("omnivore"),
        repo: EaterRepository = Depends(get_repo),
    ):
        form = {
            "name": name,
            "category": category,
            "calorie_adjustment": calorie_adjustment,
            "allergies": allergies,
            "dislikes": dislikes,
            "diet": diet,
        }
        try:
            repo.add(*_read_form(name, category, calorie_adjustment, allergies, dislikes, diet))
        except ValueError as exc:
            return page(request, repo, form, error=str(exc))
        return RedirectResponse("/eaters", status_code=303)

    @router.post("/eaters/{eater_id}")
    def update_eater(
        request: Request,
        eater_id: int,
        name: str = Form(""),
        category: str = Form(""),
        calorie_adjustment: str = Form("0"),
        allergies: str = Form(""),
        dislikes: str = Form(""),
        diet: str = Form("omnivore"),
        repo: EaterRepository = Depends(get_repo),
    ):
        if repo.get(eater_id) is None:
            raise HTTPException(404, "No such eater")
        try:
            repo.update(
                eater_id,
                *_read_form(name, category, calorie_adjustment, allergies, dislikes, diet),
            )
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
            "calorie_adjustment": eater.calorie_adjustment,
            "calorie_goal_is_custom": eater.has_custom_goal,
            "allergies": list(eater.allergies),
            "dislikes": list(eater.dislikes),
            "diet": eater.diet.value,
        }
        for eater in eaters
    ]
