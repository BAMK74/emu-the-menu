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

- `LICENSE.md`, `NOTICE.md` (copyright line says BAMK74, so check that is the name you want), `GLOSSARY.md` with 14 terms.
- ADR 0001: Python, FastAPI, SQLite and HTMX on a Pi 5.
- ADR 0002: PolyForm Noncommercial licence.
- ADR 0003: swappable OCR provider, starting with local Tesseract.
- ADR 0004: Open Food Facts for barcode lookup.
- `src/emu_the_menu/repository.py`: `StockRepository`, SQLite storage for Ingredients and Stock Items (add, use, set quantity, delete, list by expiry). Tests in `tests/test_repository.py`.
- `src/emu_the_menu/static/zxing.min.js`: the ZXing barcode reader (Apache-2.0, version 0.21.3, unmodified), served at `/static`. `THIRD_PARTY.md` and `third_party/zxing-LICENSE.txt` carry the notices Apache-2.0 requires.
- `src/emu_the_menu/openfoodfacts.py`: barcode lookup (`find_product`, `ProductInfo`, `http_fetch`), standard library only. Tests in `tests/test_openfoodfacts.py` use a fake fetcher and never touch the network.
- `src/emu_the_menu/countries.py`: the 49 countries a Household can pick, by ISO code.
- `src/emu_the_menu/shelf_life.py`: `ShelfLife` (N days, weeks or months) with month-end clamping.
- `src/emu_the_menu/web.py` and `templates/index.html`: FastAPI and Jinja Stock page (add, set quantity, delete, list by expiry) with plain POST-redirect-GET forms. Tests in `tests/test_web.py`.
- Project scaffold: `pyproject.toml` pinned to Python 3.13, `uv.lock`, `.gitignore`, `.gitattributes` (LF line endings) and `README.md`.
- `src/emu_the_menu/units.py` and `stock.py`: the pure stock rules, with docstrings that define the behaviour.
- `tests/test_units.py`, `tests/test_stock.py`, `tests/test_repository.py`, `tests/test_shelf_life.py`, `tests/test_openfoodfacts.py` and `tests/test_web.py`: 175 tests, all passing.

**Raspberry Pi** (`pi-host`, Tailscale IP 100.64.0.1, SSH user `pi-user`, key login works):

- Debian 13 (trixie), aarch64, Python 3.13.5, 2 GB RAM, 29 GB microSD that still holds the OS.
- A Samsung 840 EVO 250 GB SSD on USB is formatted ext4 (label `emu`) and mounted at `/mnt/emu` through `/etc/fstab` by UUID, with `noatime,nofail`. Folders `/mnt/emu/db`, `/mnt/emu/uploads` and `/mnt/emu/backups` are owned by `pi-user`.
- `git` is installed. `uv` and `tesseract` are not.

**This PC:** `uv` is installed. `gh` (GitHub CLI) is installed and signed in as BAMK74.

## In progress

Nothing. Slice 1 is done, including SQLite storage and the Stock web forms, with `uv run pytest` passing all 175 tests on Python 3.13 and `ruff` clean. Claude wrote the implementations at Kevin's request.

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
- Barcode lookup: an optional Barcode field and a Look up button on the Add form post to `/lookup`, which fills in quantity and unit (and the Ingredient name only if empty) and shows a notice crediting Open Food Facts. It never adds Stock. The Look up button has `formnovalidate`, and a hidden first submit button keeps Enter as "Add to stock". Found products are cached in `product_cache`, so repeats are instant and work offline, and unknown barcodes are not cached. A bad barcode is a 400 error, and an unknown barcode or an unreachable service is a plain notice. The household country is deliberately not sent, because a barcode identifies a product worldwide (see ADR 0004). `create_app` takes a `fetch_product` argument so tests can fake the network. Product names from Open Food Facts are brand-style ("isabelle" for water), so the user should check the Ingredient name.
- Scan by photo: the Scan button opens the phone's camera app (`<input type=file capture=environment>`, which works over plain HTTP), shrinks the photo to 1600px, reads it with ZXing restricted to EAN-13, EAN-8, UPC-A and UPC-E, fills the Barcode box and runs the lookup. The photo is read in the browser and never uploaded. The reading code was checked in Node against generated barcodes, and Kevin confirmed on his phone that scanning works. The automated tests cannot run the camera or the page script.
- Scanning a second product: a lookup remembers what it filled in (hidden `auto_ingredient` and `auto_quantity`) and replaces exactly those on the next scan. A name or quantity the user typed or edited is kept. An unknown barcode, an unreachable service or a product with no pack size clears the leftovers. A malformed barcode changes nothing.
- Pack sizes are rounded: whole numbers from 10 up and two decimals below, so 20 oz reads 567 g and not 566.99 g.
- Open Food Facts coverage varies. Kevin found that some products are not on the site, which is expected, and the form says "No product found" so the details can be typed by hand.
- Layout is a narrow centred card on phones and a roomier one from 640px, with dark mode.

`uv` is at `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe` and may not be on PATH in every shell.

## Next steps

1. **Live camera scanning.** Waiting on Kevin enabling HTTPS for the tailnet (Tailscale admin console, DNS page, Enable HTTPS), which he will do later. Enabling it publishes the tailnet and machine names in public certificate logs, which Kevin was told. Then serve the app with `tailscale serve`, add a live camera view with ZXing's `decodeFromVideoDevice` that only appears when `window.isSecureContext` is true, and test it on the phone. Scanning by photo already works over HTTP. Consider a built-in produce table for typical amounts (1 bunch of kale), since no reputable online source was found.
2. **Deploy to the Pi.** Install `uv` there (it installs without root), pull from GitHub, and run as a systemd service. Find out what is already using about 900 MB of RAM first.
3. **Slice 2 (household).** Household login, Eaters, Categories and the Adult/Teen/Child permissions.
4. **Later slices.** Recipes, then Menu generation, Occasions and weather, then the Shopping List, then barcode scanning and OCR.
5. **Housekeeping.** Reboot the Pi once to confirm `/mnt/emu` mounts on its own. Set up the nightly backup, ideally copying off the Pi. Add the Scan term and the fridge-photo idea to the glossary and ADR 0003 if you want them recorded.

## Reference

Before building the Recipe and Stock slices, look at `Meal-matcher-scan` in the Codecademy DevProjects folder. It is a Lovable-generated prototype (TanStack, Supabase, Gemini) that photographs a fridge and suggests recipes. The ideas that carry over are the `ingredients` table shape (name, quantity, unit, category, freshness), the strict-JSON prompt with validation and a fallback parser, and the review-then-save flow. Its code does not carry over, because it depends on Supabase and the Lovable gateway.
