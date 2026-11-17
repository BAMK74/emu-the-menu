"""Build emu-sqlite.db with the SQLite-era app, and what that app reads back from it.

This made `emu-sqlite.db` and `emu-sqlite.expected.json` for `tests/test_import_sqlite.py`. It
imports the SQLite-era modules, so it only runs in a checkout of the last SQLite commit (4aec52a):

    git worktree add /tmp/emu-sqlite 4aec52a && cd /tmp/emu-sqlite && uv sync
    uv run python <this repo>/tests/data/make_emu_sqlite.py OUT.db OUT.json
"""

import dataclasses
import json
import sqlite3
import sys
from datetime import date
from enum import Enum
from pathlib import Path

from fastapi.testclient import TestClient

from emu_the_menu.allergens import Allergen
from emu_the_menu.eaters import Category, Diet, EaterRepository
from emu_the_menu.food_groups import FoodGroup
from emu_the_menu.holidays import Holiday
from emu_the_menu.look import LookChoice
from emu_the_menu.menu_repository import MenuRepository
from emu_the_menu.occasion_repository import OccasionRepository
from emu_the_menu.occasions import Source
from emu_the_menu.openfoodfacts import ProductInfo
from emu_the_menu.planner import Meal, Menu, Portion, Slot
from emu_the_menu.recipe_repository import RecipeRepository
from emu_the_menu.recipes import MealType, Weather
from emu_the_menu.repository import StockRepository
from emu_the_menu.shelf_life import ShelfLife, ShelfLifeUnit
from emu_the_menu.stock import Location, TakenLot
from emu_the_menu.weather import Limits, Place
from emu_the_menu.weather_store import WeatherRepository
from emu_the_menu.web import create_app

TODAY = date(2026, 11, 10)


def seed(path: Path) -> None:
    with StockRepository(path) as stock:
        stock.add("flour", 1.5, "kg", Location.PANTRY, date(2027, 3, 1))
        stock.add(
            "milk", 2, "l", Location.FRIDGE, date(2026, 11, 14), ShelfLife(7, ShelfLifeUnit.DAY)
        )
        stock.add("milk", 500, "ml", Location.FRIDGE, date(2026, 11, 20))
        stock.add("eggs", 6, "count", Location.FRIDGE, date(2026, 11, 25))
        stock.add("Chicken Thighs", 800, "g", Location.FREEZER)
        stock.add("crème fraîche", 200, "ml", Location.FRIDGE, date(2026, 11, 12))
        stock.add("rice", 2, "kg", Location.PANTRY)
        stock.add("olive oil", 750, "ml", Location.PANTRY)
        stock.add("basil", 1, "bunch", Location.FRIDGE, date(2026, 11, 11))
        ids = {i.name: i.id for i in stock.ingredients()}
        stock.set_ingredient_details(ids["flour"], 364, [], [Allergen.GLUTEN])
        stock.set_ingredient_details(ids["milk"], 64, [FoodGroup.DAIRY], [Allergen.MILK])
        stock.set_ingredient_details(ids["eggs"], 72, [FoodGroup.EGGS], [Allergen.EGGS])
        stock.set_ingredient_details(ids["chicken thighs"], 177, [FoodGroup.POULTRY], [])
        stock.set_calories(ids["rice"], 130)
        stock.cache_product(ProductInfo("3274080005003", "Penne", 500.0, "g", 357.0))
        stock.cache_product(ProductInfo("5000000000001", None, None, None))
        stock.set_household_country("NZ")
        stock.accept_disclaimer("2026-10-27", date(2026, 10, 28))
        stock.set_look_choice(LookChoice("autumn", False))

    with RecipeRepository(path) as recipes:
        recipes.add(
            "Chicken and Rice",
            4,
            [MealType.DINNER, MealType.LUNCH],
            Weather.ANY,
            ["weeknight", "check-quantities"],
            [
                ("chicken thighs", 600, "g"),
                ("rice", 300, "g"),
                ("water or vegetable broth", 500, "ml"),
                ("olive oil", 1, "tbsp"),
            ],
            "https://example.com/chicken-rice",
        )
        recipes.add(
            "Pancakes",
            2,
            [MealType.BREAKFAST],
            Weather.COLD,
            ["sweet"],
            [("flour", 125, "g"), ("milk", 300, "ml"), ("eggs", 1, "count")],
        )
        recipes.add(
            "Basil Omelette",
            1,
            [MealType.BREAKFAST, MealType.LUNCH],
            Weather.WARM,
            [],
            [("eggs", 3, "count"), ("basil", 1, "bunch"), ("butter or olive oil", 1, "tsp")],
        )
        by_name = {r.name: r for r in recipes.recipes()}

    with EaterRepository(path) as eaters:
        kevin = eaters.add("Alex", Category.MAN, 200, ["kiwi"], ["coriander"], Diet.OMNIVORE)
        mia = eaters.add(
            "Mia",
            Category.WOMAN,
            -100,
            [],
            ["mushrooms", "olives"],
            Diet.VEGETARIAN,
            [Allergen.MILK, Allergen.SESAME],
        )
        kid = eaters.add("Sam", Category.CHILD, 0, [], [], Diet.LOW_MEAT)
        eaters.set_default_goals({Category.CHILD: 1500})
        table = [kevin, mia, kid]

    with OccasionRepository(path) as occasions:
        occasions.add("Mia's birthday", date(2026, 11, 13), 8, 20, ["party", "cake"])
        occasions.add("Quiet Sunday", date(2026, 11, 15))
        occasions.add_holidays(
            [
                Holiday(date(2026, 12, 25), "Christmas Day"),
                Holiday(date(2026, 12, 26), "Boxing Day"),
            ]
        )
        boxing = next(o for o in occasions.all() if o.name == "Boxing Day")
        assert boxing.source is Source.HOLIDAY

    with WeatherRepository(path) as weather:
        weather.set_place(Place("Wellington", "Wellington", "NZ", -41.2866, 174.7756))
        weather.set_unit("F")
        weather.set_limits(Limits(21.5, 9.0))
    db = sqlite3.connect(path)
    with db:
        db.executemany(
            "INSERT INTO weather_days (day, high, low) VALUES (?, ?, ?)",
            [("2026-11-10", 18.5, 9.25), ("2026-11-11", 22.0, 12.0), ("2026-11-12", 8.5, 3.0)],
        )
        db.execute(
            "INSERT INTO settings (key, value)"
            " VALUES ('weather_fetched', '2026-11-10T06:00:00+00:00')"
        )
    db.close()

    def portions(recipe_servings: float):
        return tuple(Portion(e, recipe_servings, None) for e in table)

    chicken, pancakes, omelette = (
        by_name["Chicken and Rice"],
        by_name["Pancakes"],
        by_name["Basil Omelette"],
    )
    with MenuRepository(path) as menus:
        first = menus.save(
            "This week",
            date(2026, 11, 10),
            3,
            Menu(
                (
                    Meal(date(2026, 11, 10), Slot.BREAKFAST, pancakes, portions(1.0)),
                    Meal(
                        date(2026, 11, 10),
                        Slot.DINNER,
                        chicken,
                        portions(1.5),
                        guests=2,
                        guest_servings=2.0,
                    ),
                    Meal(
                        date(2026, 11, 11),
                        Slot.BREAKFAST,
                        None,
                        (),
                        reason="Nothing suits everyone",
                    ),
                    Meal(date(2026, 11, 11), Slot.LUNCH, omelette, portions(1.0)),
                    Meal(date(2026, 11, 12), Slot.DINNER, chicken, portions(1.0)),
                ),
                (),
            ),
            table,
        )
        second = menus.save(
            "",
            date(2026, 11, 17),
            2,
            Menu((Meal(date(2026, 11, 17), Slot.LUNCH, omelette, portions(0.5)),), ()),
            table,
        )
        menus.save("Draft", date(2026, 11, 24), 1, Menu((), ()), table)
        menus.accept(first, date(2026, 11, 9))
        menus.accept(second, date(2026, 11, 10))
        meals = menus.get(first).meals
        menus.set_cooked(
            first,
            meals[0].id,
            date(2026, 11, 10),
            "eggs",
            [
                TakenLot("flour", 125.0, "g", Location.PANTRY, date(2027, 3, 1)),
                TakenLot("milk", 300.0, "ml", Location.FRIDGE, date(2026, 11, 14)),
                TakenLot("eggs", 1.0, "count", Location.FRIDGE, None),
            ],
        )
        menus.set_guests(first, meals[1].id, 3, 3.0)

    with RecipeRepository(path) as recipes:
        recipes.delete(by_name["Basil Omelette"].id)  # leaves a Meal whose recipe was deleted


def plain(value):
    """Dataclasses, enums and dates as JSON-friendly values."""
    if dataclasses.is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    return value


def read_back(path: Path) -> dict:
    client = TestClient(create_app(path, today=lambda: TODAY))
    export = client.get("/export.json").json()
    with MenuRepository(path) as menus, StockRepository(path) as stock:
        with WeatherRepository(path) as weather, OccasionRepository(path) as occasions:
            return {
                "export": export,
                "menus": plain(menus.menus()),
                "last_served": plain(menus.last_served()),
                "taken": plain(
                    [menus.taken(m.id, meal.id) for m in menus.menus() for meal in m.meals]
                ),
                "stock": plain(stock.list_items()),
                "ingredients": plain(stock.ingredients()),
                "look": plain(stock.look_choice()),
                "disclaimer": plain(stock.disclaimer_acceptance()),
                "products": plain(
                    [stock.cached_product(b) for b in ("3274080005003", "5000000000001")]
                ),
                "place": plain(weather.place()),
                "unit": weather.unit(),
                "limits": plain(weather.limits()),
                "weather_days": plain(weather.days(date(2026, 11, 1))),
                "occasions": plain(occasions.all()),
            }


if __name__ == "__main__":
    out_db, out_json = Path(sys.argv[1]), Path(sys.argv[2])
    out_db.unlink(missing_ok=True)
    seed(out_db)
    out_json.write_text(json.dumps(read_back(out_db), indent=1, ensure_ascii=False) + "\n")
    print(
        {
            t: n
            for t, n in (
                (t, sqlite3.connect(out_db).execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                for (t,) in sqlite3.connect(out_db).execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                )
            )
        }
    )
