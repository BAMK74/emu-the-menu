"""The Ingredients page: calories, food groups and allergens for every Ingredient.

Each Ingredient is a card. Clicking it opens a popup to edit it, fetched when it opens so it always
shows the Ingredient as it is now. Without scripting the card links to the same form as a page.
"""

from collections.abc import Iterator

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.datastructures import FormData

from emu_the_menu.allergens import Allergen, parse_allergens
from emu_the_menu.food_groups import FoodGroup, parse_food_groups
from emu_the_menu.ingredients import kcal_basis, parse_kcal, suggest_tags
from emu_the_menu.openfoodfacts import Fetch, LookupUnavailableError, find_product, http_fetch
from emu_the_menu.posted import posted_form
from emu_the_menu.repository import Ingredient, StockRepository
from emu_the_menu.templating import TEMPLATES
from emu_the_menu.units import BaseUnit, IncompatibleUnitError


def create_router(database: str, fetch_product: Fetch = http_fetch) -> APIRouter:
    router = APIRouter()

    def get_repo() -> Iterator[StockRepository]:
        with StockRepository(database) as repo:
            yield repo

    def find(repo: StockRepository, ingredient_id: int) -> Ingredient:
        ingredient = repo.get_ingredient(ingredient_id)
        if ingredient is None:
            raise HTTPException(404, "No such ingredient")
        return ingredient

    def form_for(ingredient: Ingredient) -> tuple[dict, bool]:
        """The form's values, and whether some were ticked only because of the name.

        Suggestions are for an Ingredient nobody has reviewed yet. Once a person has saved its
        details, even an empty choice is theirs and is never overridden.
        """
        groups = [g.value for g in ingredient.food_groups]
        allergens = [a.value for a in ingredient.allergens]
        suggested = False
        if not ingredient.reviewed:
            suggestion = suggest_tags(ingredient.name)
            if suggestion.food_groups or suggestion.allergens:
                groups = [g.value for g in suggestion.food_groups]
                allergens = [a.value for a in suggestion.allergens]
                suggested = True
        kcal = "" if ingredient.kcal is None else f"{ingredient.kcal:g}"
        return {
            "name": ingredient.name,
            "kcal": kcal,
            "food_groups": groups,
            "allergens": allergens,
        }, suggested

    def context(ingredient: Ingredient, form: dict, suggested: bool, error: str | None = None):
        return {
            "ingredient": ingredient,
            "form": form,
            "suggested": suggested,
            "error": error,
            "food_group_choices": list(FoodGroup),
            "allergen_choices": list(Allergen),
        }

    @router.get("/ingredients", response_class=HTMLResponse)
    def ingredients(request: Request, show: str = "", repo: StockRepository = Depends(get_repo)):
        found = repo.ingredients()
        summary = {
            "total": len(found),
            "no_calories": sum(1 for i in found if i.kcal is None),
            "unchecked": sum(1 for i in found if not i.reviewed),
        }
        # The overview chips filter the list. Anything unrecognised shows everything.
        if show == "no-calories":
            shown = [i for i in found if i.kcal is None]
        elif show == "unchecked":
            shown = [i for i in found if not i.reviewed]
        else:
            show, shown = "", found
        return TEMPLATES.TemplateResponse(
            request,
            "ingredients.html",
            {
                "ingredients": shown,
                "summary": summary,
                "show": show,
                "tidy_plan": repo.tidy_plan(),
                "split_plan": repo.split_plan(),
            },
        )

    @router.post("/ingredients/tidy")
    def tidy(request: Request, form: FormData = Depends(posted_form)):
        """Merge ingredients that are one food written two ways and give each a plain name.

        With `selected` only the changes ticked in `apply` are made, each under the name typed for
        it in `target_<key>`. Without it every proposed change is made. A change that cannot be
        made is skipped and the others still happen.
        """
        with StockRepository(database) as repo:
            if not form.get("selected"):
                repo.tidy()
            else:
                # The Save beside one change (`only`) applies just that change.
                only = str(form.get("only", "")).strip()
                chosen = {only} if only else {str(v) for v in form.getlist("apply")}
                for members, target in repo.tidy_plan():
                    key = str(members[0].id)
                    if key not in chosen:
                        continue
                    typed = str(form.get(f"target_{key}", "")).strip() or target
                    try:
                        repo.tidy_group([m.id for m in members], typed)
                    except (ValueError, IncompatibleUnitError):
                        continue
        return RedirectResponse("/ingredients", status_code=303)

    @router.get("/ingredients/{ingredient_id}/panel", response_class=HTMLResponse)
    def panel(request: Request, ingredient_id: int, repo: StockRepository = Depends(get_repo)):
        ingredient = find(repo, ingredient_id)
        form, suggested = form_for(ingredient)
        return TEMPLATES.TemplateResponse(
            request, "_ingredient_panel.html", context(ingredient, form, suggested)
        )

    @router.get("/ingredients/{ingredient_id}", response_class=HTMLResponse)
    def page(request: Request, ingredient_id: int, repo: StockRepository = Depends(get_repo)):
        ingredient = find(repo, ingredient_id)
        form, suggested = form_for(ingredient)
        return TEMPLATES.TemplateResponse(
            request, "ingredient.html", context(ingredient, form, suggested)
        )

    @router.post("/ingredients/{ingredient_id}/lookup")
    def look_up_calories(
        ingredient_id: int, barcode: str = Form(""), repo: StockRepository = Depends(get_repo)
    ):
        """Calories from a barcode, as JSON for the popup to fill in. Never saves anything."""
        ingredient = find(repo, ingredient_id)

        def answer(message: str, status: int = 200, **extra):
            return JSONResponse(
                {"ok": status == 200 and "kcal" in extra, **extra, "message": message},
                status_code=status,
            )

        if ingredient.base_unit is BaseUnit.COUNT:
            return answer(
                "Calories for counted ingredients are per piece, and a barcode gives them"
                " per 100 g, so enter this one by hand.",
                400,
            )
        try:
            info = find_product(barcode, repo, fetch_product, need_energy=True)
        except LookupUnavailableError:
            return answer("Could not reach Open Food Facts. Enter the calories by hand.", 503)
        except ValueError as exc:
            return answer(str(exc), 400)
        if info is None:
            return answer("No product found for that barcode.", 404)
        name = info.name or "That product"
        if info.kcal_per_100 is None:
            return answer(f"{name} lists no calories on Open Food Facts. Enter them by hand.")
        basis = kcal_basis(ingredient.base_unit)
        return answer(
            f"{name}: {info.kcal_per_100:g} kcal {basis}. Data from Open Food Facts.",
            kcal=info.kcal_per_100,
        )

    @router.post("/ingredients/{ingredient_id}")
    def save(
        request: Request,
        ingredient_id: int,
        name: str = Form(""),
        kcal: str = Form(""),
        food_groups: list[str] = Form([]),
        allergens: list[str] = Form([]),
        repo: StockRepository = Depends(get_repo),
    ):
        ingredient = find(repo, ingredient_id)
        form = {
            "name": name or ingredient.name,
            "kcal": kcal,
            "food_groups": food_groups,
            "allergens": allergens,
        }
        try:
            renaming = bool(name.strip()) and " ".join(name.split()).lower() != ingredient.name
            if renaming:
                repo.check_rename(ingredient_id, name)  # before anything is saved
            repo.set_ingredient_details(
                ingredient_id,
                parse_kcal(kcal, ingredient.base_unit),
                parse_food_groups(food_groups),
                parse_allergens(allergens),
            )
            if renaming:
                repo.rename_ingredient(ingredient_id, name)
        except (ValueError, IncompatibleUnitError) as exc:
            return TEMPLATES.TemplateResponse(
                request,
                "ingredient.html",
                context(ingredient, form, False, str(exc)),
                status_code=400,
            )
        return RedirectResponse("/ingredients", status_code=303)

    return router
