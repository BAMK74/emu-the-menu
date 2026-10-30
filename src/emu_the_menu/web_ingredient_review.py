"""Reviewing many Ingredients at once, instead of opening them one by one.

Every Ingredient nobody has reviewed is unknown, and a person has to say what is in it. To make a
long list quick, the page sorts it into three kinds:

- plain-looking ones (a single plant food such as garlic or salt, see `ingredients.judge`) as one
  group of ticked boxes: look it over, untick anything doubtful, confirm the lot in one click;
- groups that share a likely answer (everything the name suggests is dairy and milk, everything
  that is poultry, and so on), each with its own button;
- the rest as compact rows with the ticks the name suggests, a reason it needs a look, and a
  "Looks right" box. Only the rows ticked as right are saved.

The page can be limited to one Recipe's Ingredients, which is all that is needed to make that
Recipe checked. Nothing is ever saved by itself: an Ingredient only counts as reviewed when a
person confirms it.
"""

import math
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from emu_the_menu.allergens import Allergen, parse_allergens
from emu_the_menu.food_groups import FoodGroup, parse_food_groups
from emu_the_menu.ingredients import PLAIN, judge, parse_kcal, suggest_tags
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.repository import Ingredient, StockRepository
from emu_the_menu.templating import TEMPLATES

PAGE_SIZE = 20
MIN_GROUP = 2  # a "group" of one is just a row


def _whole(text: str, default: int = 0) -> int:
    return int(text) if text.strip().isdecimal() else default


def _ids(values) -> list[int]:
    return list(dict.fromkeys(int(v) for v in values if str(v).strip().isdecimal()))


def create_router(db_path: str | Path) -> APIRouter:
    router = APIRouter()

    def get_repo() -> Iterator[StockRepository]:
        with StockRepository(db_path) as repo:
            yield repo

    def unchecked(repo: StockRepository, recipe_id: int = 0):
        """Unreviewed Ingredients with how many Recipes use each, most used first, and the
        Recipe asked for (None if there is none or the number is unknown)."""
        used: dict[int, int] = {}
        chosen = None
        with RecipeRepository(db_path) as recipes:
            for recipe in recipes.recipes():
                for line in recipe.lines:
                    used[line.ingredient.id] = used.get(line.ingredient.id, 0) + 1
                if recipe.id == recipe_id:
                    chosen = recipe
        found = [(i, used.get(i.id, 0)) for i in repo.ingredients() if not i.reviewed]
        if chosen is not None:
            wanted = {line.ingredient.id for line in chosen.lines}
            found = [(i, n) for i, n in found if i.id in wanted]
        found.sort(key=lambda pair: (-pair[1], pair[0].name))
        return found, chosen

    def page_of(
        request: Request,
        repo: StockRepository,
        show: str,
        page: int,
        recipe_id: int = 0,
        done: int = 0,
        error: str | None = None,
        posted=None,
        status: int = 200,
    ):
        everything, chosen = unchecked(repo, recipe_id)
        plain = [(i, n) for i, n in everything if judge(i.name).kind == PLAIN]
        hinted: dict[tuple, list[tuple[Ingredient, int]]] = {}
        loose: list[tuple[Ingredient, int]] = []
        for ingredient, used in everything:
            if judge(ingredient.name).kind == PLAIN:
                continue
            hint = suggest_tags(ingredient.name)
            if hint.food_groups or hint.allergens:
                key = (
                    tuple(g.value for g in hint.food_groups),
                    tuple(a.value for a in hint.allergens),
                )
                hinted.setdefault(key, []).append((ingredient, used))
            else:
                loose.append((ingredient, used))
        groups, look = [], list(loose)
        for (food_groups, allergens), members in hinted.items():
            if len(members) < MIN_GROUP:
                look.extend(members)
                continue
            labels = [FoodGroup(g).short_label.lower() for g in food_groups]
            labels += [
                Allergen(a).short_label.lower()
                for a in allergens
                if Allergen(a).short_label.lower() not in labels
            ]
            groups.append(
                {
                    "food_groups": food_groups,
                    "allergens": allergens,
                    "labels": labels,
                    "members": members,
                    "uses": sum(n for _, n in members),
                }
            )
        groups.sort(key=lambda g: (-g["uses"], g["labels"]))
        look.sort(key=lambda pair: (-pair[1], pair[0].name))
        in_groups = sum(len(g["members"]) for g in groups)
        pages = max(1, math.ceil(len(look) / PAGE_SIZE))
        page = min(max(1, page), pages)
        confirmed = set(_ids(posted.getlist("confirm"))) if posted is not None else set()
        ticked_group = set(_ids(posted.getlist("grp"))) if posted is not None else None
        ticked_plain = (
            set(_ids(posted.getlist("plain"))) if posted is not None else {i.id for i, _ in plain}
        )
        rows = []
        for ingredient, used in look[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]:
            hint = suggest_tags(ingredient.name)
            kcal, tags_fg, tags_al = (
                "",
                [g.value for g in hint.food_groups],
                [a.value for a in hint.allergens],
            )
            if posted is not None and f"kcal_{ingredient.id}" in posted:
                kcal = posted.get(f"kcal_{ingredient.id}", "")
                tags_fg = posted.getlist(f"fg_{ingredient.id}")
                tags_al = posted.getlist(f"al_{ingredient.id}")
            rows.append(
                {
                    "ingredient": ingredient,
                    "used": used,
                    "reason": judge(ingredient.name).reason,
                    "kcal": kcal,
                    "groups": tags_fg,
                    "allergens": tags_al,
                    "confirmed": ingredient.id in confirmed,
                }
            )
        return TEMPLATES.TemplateResponse(
            request,
            "ingredient_review.html",
            {
                "left": len(everything),
                "plain_count": len(plain),
                "group_count": in_groups,
                "look_count": in_groups + len(look),
                "show": show,
                "recipe": chosen,
                "plain": [] if show == "risky" else plain,
                "ticked_plain": ticked_plain,
                "ticked_group": ticked_group,
                "groups": groups,
                "rows": rows,
                "page": page,
                "pages": pages,
                "done": done,
                "error": error,
                "food_group_choices": list(FoodGroup),
                "allergen_choices": list(Allergen),
            },
            status_code=status,
        )

    @router.get("/ingredients/review", response_class=HTMLResponse)
    def review(
        request: Request,
        show: str = "",
        page: str = "",
        recipe: str = "",
        done: str = "",
        repo: StockRepository = Depends(get_repo),
    ):
        return page_of(
            request,
            repo,
            "risky" if show == "risky" else "",
            _whole(page, 1) or 1,
            _whole(recipe),
            _whole(done),
        )

    @router.post("/ingredients/review")
    async def save(request: Request):
        form = await request.form()
        # Opened here, not injected: a connection must stay in the thread that made it.
        with StockRepository(db_path) as repo:
            return store(request, repo, form)

    def store(request: Request, repo: StockRepository, form):
        show = "risky" if form.get("show") == "risky" else ""
        page = _whole(str(form.get("page", "")), 1) or 1
        recipe_id = _whole(str(form.get("recipe", "")))
        waiting = {i.id: i for i, _ in unchecked(repo)[0]}
        # Plain-looking ones are only accepted if they still look plain, so a made-up request
        # cannot mark something with a hint of an allergen as having none.
        plain_ids = [
            i
            for i in _ids(form.getlist("plain"))
            if i in waiting and judge(waiting[i].name).kind == PLAIN
        ]
        saves: list[tuple[int, float | None, tuple, tuple]] = [(i, None, (), ()) for i in plain_ids]
        try:
            try:
                group_tags = (
                    parse_food_groups(form.getlist("grp_fg")),
                    parse_allergens(form.getlist("grp_al")),
                )
            except ValueError as exc:
                raise ValueError(f"The group: {exc}") from None
            saves += [(i, None, *group_tags) for i in _ids(form.getlist("grp")) if i in waiting]
            for ingredient_id in _ids(form.getlist("confirm")):
                ingredient = waiting.get(ingredient_id)
                if ingredient is None:
                    continue
                try:
                    saves.append(
                        (
                            ingredient_id,
                            parse_kcal(
                                str(form.get(f"kcal_{ingredient_id}", "")), ingredient.base_unit
                            ),
                            parse_food_groups(form.getlist(f"fg_{ingredient_id}")),
                            parse_allergens(form.getlist(f"al_{ingredient_id}")),
                        )
                    )
                except ValueError as exc:
                    raise ValueError(f"{ingredient.name}: {exc}") from None
        except ValueError as exc:
            return page_of(
                request, repo, show, page, recipe_id, error=str(exc), posted=form, status=400
            )
        for ingredient_id, kcal, groups, allergens in saves:
            repo.set_ingredient_details(ingredient_id, kcal, groups, allergens)
        query = []
        if show:
            query.append(f"show={show}")
        if recipe_id:
            query.append(f"recipe={recipe_id}")
        if saves:
            query.append(f"done={len(saves)}")
        return RedirectResponse(
            "/ingredients/review" + (f"?{'&'.join(query)}" if query else ""), status_code=303
        )

    return router
