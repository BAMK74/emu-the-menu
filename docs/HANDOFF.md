# Handoff

Last updated: 2026-10-26. Read this first when picking the project up again. Domain language is in [GLOSSARY.md](../GLOSSARY.md) and decisions are in [adr/](adr/).

## What this is

Emu the Menu is a self-hosted household app. It tracks what is in the fridge, freezer and pantry, and suggests Menus that fit calorie goals, weather, Occasions and available Stock. It runs on a Raspberry Pi and is reached over Tailscale. Private repo: https://github.com/BAMK74/emu-the-menu, licensed PolyForm Noncommercial 1.0.0.

## Done

**Design** (settled in a design interview, summarised here):

- Menu is a date range of suggested Meals. Emu generates it automatically, and you can reshuffle, interrupt or modify any Meal. Generated Meals are unreserved suggestions.
- Accepting a Meal reserves its ingredients. A "cooked" confirmation then deducts them from Stock.
- Hard rules filter Recipes first: allergies, Shopping Allowance and Cooldown. A weighted score then ranks the rest by calorie fit, weather tags, expiring Stock and Occasion fit. Weights are settings, not code.
- Weather is tag-based: Recipes carry warm, cold or any. Weather and holiday region come from one Household location setting, and both are fetched online.
- Occasions are imported holidays plus manual entries. They carry a date, a headcount, tags and an optional relaxed calorie goal.
- Each Eater has a Category (Man, Woman, Teen or Child) that sets a default calorie goal, which can be overridden. Eaters also have allergies (hard exclusions) and dislikes (soft penalties).
- Accounts: Adults and Teens get accounts, and Children are profiles managed by an adult. Adults edit shared data, and others view, mark Meals cooked and edit their own preferences. Tailscale is the outer gate.
- Stock is added through barcode scans (Open Food Facts, mapping cached), receipt and label OCR that you confirm, and manual forms. Calories per Ingredient are looked up and cached, with a manual override.
- A photo-of-fridge scan is a possible later feature behind the same provider interface as OCR. It is not recorded in the glossary or ADRs yet.
- Backups: a nightly SQLite copy plus a manual JSON export.

**Repo** (branch `main`, pushed):

- `LICENSE.md`, `NOTICE.md` (copyright line says BAMK74, so check that is the name you want), `GLOSSARY.md` with 13 terms.
- ADR 0001: Python, FastAPI, SQLite and HTMX on a Pi 5.
- ADR 0002: PolyForm Noncommercial licence.
- ADR 0003: swappable OCR provider, starting with local Tesseract.
- `src/emu_the_menu/repository.py`: `StockRepository`, SQLite storage for Ingredients and Stock Items (add, use, set quantity, delete, list by expiry). Tests in `tests/test_repository.py`.
- `src/emu_the_menu/countries.py`: the 49 countries a Household can pick, by ISO code.
- `src/emu_the_menu/shelf_life.py`: `ShelfLife` (N days, weeks or months) with month-end clamping.
- `src/emu_the_menu/web.py` and `templates/index.html`: FastAPI and Jinja Stock page (add, set quantity, delete, list by expiry) with plain POST-redirect-GET forms. Tests in `tests/test_web.py`.
- Project scaffold: `pyproject.toml` pinned to Python 3.13, `uv.lock`, `.gitignore`, `.gitattributes` (LF line endings) and `README.md`.
- `src/emu_the_menu/units.py` and `stock.py`: the pure stock rules, with docstrings that define the behaviour.
- `tests/test_units.py`, `tests/test_stock.py`, `tests/test_repository.py`, `tests/test_shelf_life.py` and `tests/test_web.py`: 125 tests, all passing.

**Raspberry Pi** (`pi-host`, Tailscale IP 100.64.0.1, SSH user `pi-user`, key login works):

- Debian 13 (trixie), aarch64, Python 3.13.5, 2 GB RAM, 29 GB microSD that still holds the OS.
- A Samsung 840 EVO 250 GB SSD on USB is formatted ext4 (label `emu`) and mounted at `/mnt/emu` through `/etc/fstab` by UUID, with `noatime,nofail`. Folders `/mnt/emu/db`, `/mnt/emu/uploads` and `/mnt/emu/backups` are owned by `pi-user`.
- `git` is installed. `uv` and `tesseract` are not.

**This PC:** `uv` is installed. `gh` (GitHub CLI) is installed and signed in as BAMK74.

## In progress

Nothing. Slice 1 is done, including SQLite storage and the Stock web forms, with `uv run pytest` passing all 125 tests on Python 3.13 and `ruff` clean. Claude wrote the implementations at Kevin's request.

Choices to know about: 1 tsp = 5 ml, 1 tbsp = 15 ml, 1 cup = 240 ml. `reduce_stock` uses the earliest expiry first, with no-expiry items last. Ingredient names are stored lowercased and stripped. Quantities are floats, and amounts below 1e-9 count as zero. Mixing unit families for one Ingredient raises `IncompatibleUnitError`, in both `add_stock` and `reduce_stock`.

Storage: `StockItem` has an optional `id`, which is `None` until stored. `StockRepository` runs the pure stock rules inside one transaction and writes back only the changed rows. The first add of an Ingredient fixes its base unit from the unit typed, so there is no separate create-Ingredient step. A dedicated form could replace that later. The repository takes a database path, and nothing points at `/mnt/emu/db` yet.

Web: `create_app(db_path, today)` in `web.py` reads `EMU_DB` (default `emu.db`). Run locally with `uv run uvicorn emu_the_menu.web:create_app --factory`, optionally with `--host <tailscale ip>` to try it on a phone. Each request opens its own repository. There is no HTMX, no login and no "cooked" action yet, and quantity changes are a manual set.

Add form, as tried on a phone:
- Units include `bunch` and `head`, which count as whole items.
- Expiry is one row: a calendar date, or "in N days/weeks/months", or a "No expiry" checkbox. They are alternatives, and the server rejects a date plus a number. Entering "in N ..." remembers that shelf life for the Ingredient (`ingredients.shelf_life_*`), and picking the Ingredient later pre-fills it. A calendar date or "No expiry" never overwrites it. Older databases are upgraded on open.
- The Ingredient box suggests every Ingredient ever added and pre-selects its unit.
- A unit-family clash (for example chicken first entered in ml, then added in g) is a 409 warning with a one-tap fix, "Store chicken in g instead". The fix calls `repo.change_base_unit`, which keeps existing numbers (500 ml becomes 500 g) because the families cannot convert. The Set form on an item row does the same. Other bad input is a red 400 error.
- Each Ingredient also remembers the quantity and unit last typed (`ingredients.last_quantity` and `last_unit`). Picking it pre-fills the unit, and the quantity if the box is empty. `change_base_unit` forgets it. This is the "typical amount" suggestion for now, and it comes from the household's own history, not the internet.
- Household location: `settings` table (key and value) and `repo.household_country()` and `set_household_country()`, by ISO code. Settings open as a `<dialog>` overlay on the Stock page and the link falls back to `/settings` without scripting. Saving from the overlay posts `next=/` and returns to `/?saved=1`, and only that value is honoured, so there is no open redirect. Pages share `templates/base.html`, and the form is `_settings_form.html`. The country is not used by anything yet.
- Layout is a narrow centred card on phones and a roomier one from 640px, with dark mode.

`uv` is at `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe` and may not be on PATH in every shell.

## Next steps

1. **Pack size from a barcode (Open Food Facts).** The first feature that calls the internet, so write an ADR for it first. Add a barcode field to the Add form and look the product up with the Household country, mapping its quantity ("500 g") to a quantity and unit. Cache results as the design says, and fail soft when offline. A camera scan needs HTTPS, which Tailscale can provide, so it is a later step. No reputable online source for typical amounts (1 bunch of kale) was found, so that stays as the remembered last quantity, with a built-in produce table as an option.
2. **Deploy to the Pi.** Install `uv` there (it installs without root), pull from GitHub, and run as a systemd service. Find out what is already using about 900 MB of RAM first.
3. **Slice 2 (household).** Household login, Eaters, Categories and the Adult/Teen/Child permissions.
4. **Later slices.** Recipes, then Menu generation, Occasions and weather, then the Shopping List, then barcode scanning and OCR.
5. **Housekeeping.** Reboot the Pi once to confirm `/mnt/emu` mounts on its own. Set up the nightly backup, ideally copying off the Pi. Add the Scan term and the fridge-photo idea to the glossary and ADR 0003 if you want them recorded.

## Reference

Before building the Recipe and Stock slices, look at `Meal-matcher-scan` in the Codecademy DevProjects folder. It is a Lovable-generated prototype (TanStack, Supabase, Gemini) that photographs a fridge and suggests recipes. The ideas that carry over are the `ingredients` table shape (name, quantity, unit, category, freshness), the strict-JSON prompt with validation and a fallback parser, and the review-then-save flow. Its code does not carry over, because it depends on Supabase and the Lovable gateway.
