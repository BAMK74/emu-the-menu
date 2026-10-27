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
- `src/emu_the_menu/backup.py`: nightly database backup (`python -m emu_the_menu.backup`, reading `EMU_DB` and `EMU_BACKUP_DIR`). It copies with SQLite's backup API, so it is safe while the app runs, checks the copy with `PRAGMA integrity_check`, writes to a temporary file first, gzips to `emu-YYYY-MM-DD.db.gz` and prunes. Retention is the last 14 days plus the first backup of each of the last 6 calendar months, and files not named like backups are never touched. Tests in `tests/test_backup.py` restore a backup and read it back.
- `src/emu_the_menu/openfoodfacts.py`: barcode lookup (`find_product`, `ProductInfo`, `http_fetch`), standard library only. Tests in `tests/test_openfoodfacts.py` use a fake fetcher and never touch the network.
- `src/emu_the_menu/countries.py`: the 49 countries a Household can pick, by ISO code.
- `src/emu_the_menu/shelf_life.py`: `ShelfLife` (N days, weeks or months) with month-end clamping.
- `src/emu_the_menu/web.py` and `templates/index.html`: FastAPI and Jinja Stock page (add, set quantity, delete, list by expiry) with plain POST-redirect-GET forms. Tests in `tests/test_web.py`.
- Project scaffold: `pyproject.toml` pinned to Python 3.13, `uv.lock`, `.gitignore`, `.gitattributes` (LF line endings) and `README.md`.
- `src/emu_the_menu/units.py` and `stock.py`: the pure stock rules, with docstrings that define the behaviour.
- `tests/test_units.py`, `tests/test_stock.py`, `tests/test_repository.py`, `tests/test_shelf_life.py`, `tests/test_openfoodfacts.py`, `tests/test_backup.py` and `tests/test_web.py`: 221 tests, all passing.

**Raspberry Pi** (`pi-host`, Tailscale IP 100.64.0.1, SSH user `pi-user`, logged in with Tailscale SSH):

- Debian 13 (trixie), aarch64, Python 3.13.5, 2 GB RAM, 29 GB microSD that still holds the OS.
- A Samsung 840 EVO 250 GB SSD on USB is formatted ext4 (label `emu`) and mounted at `/mnt/emu` through `/etc/fstab` by UUID, with `noatime,nofail`. Folders `/mnt/emu/db`, `/mnt/emu/uploads` and `/mnt/emu/backups` are owned by `pi-user`.
- `git` is installed, and `tesseract` is not. It also runs AdGuard Home (ports 53, 80, 443, 3000), Docker, NordVPN and Tailscale, which together use most of the 766 MB in use. Port 8000 is free.
- **Emu is deployed** and running as the systemd service `emu-the-menu` (the unit is saved in `deploy/emu-the-menu.service`). It runs as `pi-user` from `~/emu-the-menu`, with the database at `/mnt/emu/db/emu.db`, and starts at boot. It waits for the SSD mount (`RequiresMountsFor`) so data never lands on the SD card. It listens on `100.64.0.1:8000` only, so it is reachable from Tailscale devices and not from the home network. It uses about 61 MB. The Pi's database started empty.
- `uv` is installed for `pi-user` only, at `~/.local/bin/uv`, and the code was cloned over a read-only GitHub deploy key (`~/.ssh/emu_deploy_key`, host alias `github-emu`, titled "pi-host (read-only)" in the repo's deploy keys).
- **To update the Pi**, run from this PC: `ssh pi-user@100.64.0.1 "cd ~/emu-the-menu && git pull && ~/.local/bin/uv sync --no-dev && sudo systemctl restart emu-the-menu"`. Tailscale SSH may ask for a browser approval link first, which only Kevin can give. Logs are in `journalctl -u emu-the-menu`.
- **Export and backup status** (Settings, in the overlay and on `/settings`): **Download database backup** (`/export.db.gz`, a fresh consistent copy made with the same SQLite backup API, restorable like a nightly backup) and **Export as JSON** (`/export.json`, a readable copy of ingredients, stock and the household country, from `repo.export_data()`). Both send `Cache-Control: no-store` and a dated filename. This is the agreed way to keep a copy off the Pi. Settings also shows the last nightly backup (`EMU_BACKUP_DIR`, set in the Emu service), in red with "Check the Pi." when it is more than 2 days old or missing, so a failed nightly job is visible and not only in the journal.
- **Router check, 2026-10-27:** `deploy/check_router_upnp.py` asks the router read-only which ports it has opened through UPnP (run it on the Pi with `ssh pi-user@100.64.0.1 python3 - < deploy/check_router_upnp.py`). The router (`192.168.1.1`) forwards nothing for Emu, SSH, DNS or any other service. Its only mappings are Tailscale's own UDP ones (`tailscale-portmap`), which are normal. The router's own external address is `192.168.0.53`, a private address, so there are two routers in a chain, and the outer one cannot be queried from the home network.
- **Nightly backup:** `deploy/emu-backup.service` and `deploy/emu-backup.timer` run the backup at about 03:30 every night (`Persistent=true`, so a missed run happens when the Pi is back up) into `/mnt/emu/backups`, on the same SSD as the database. Check it with `systemctl list-timers emu-backup.timer` and `journalctl -u emu-backup`. A failed run only shows in the journal, with no alert. To restore, stop `emu-the-menu`, gunzip a backup over `/mnt/emu/db/emu.db`, make it `600` and owned by `pi-user`, and start the service.
- **Security check, 2026-10-27.** The Pi has no public IPv4 or IPv6 address of its own, so it is only exposed to the internet if the router forwards ports to it, which has not been checked (an outside test from this PC was inconclusive; check the router's port-forward and UPnP pages, and never forward port 53). It is behind what looks like two routers. Done: the Emu data folders are owner-only (`700`, database `600`) and the service has `UMask=0077`; SSH password login is off (`/etc/ssh/sshd_config.d/10-emu-no-password-login.conf`), and logins go through Tailscale SSH, which does not use that server; 33 updates were installed, including Docker, which caused a 6 second AdGuard DNS blip; and a separate nftables table (`deploy/emu-guard.nft`, loaded at boot by `deploy/emu-guard.service`, installed at `/etc/emu-guard.nft`) blocks VNC (5900), Glances (61208, 61209) and the unused AdGuard setup port 3000 on `eth0` and `wlan0`, so they answer only over Tailscale. AdGuard (53, 80, 443, 853) is deliberately open to the home network. Docker does not keep containers running during its own restarts (`live-restore` is off), so a Docker upgrade restarts AdGuard.
- Still open: the Pi has no host firewall beyond that table and its default policy is accept; the SD card and SSD are not encrypted; Emu has no login, so anything on the tailnet can read and change the data, and `an old phone` has been offline for weeks and could be removed from the tailnet; `pi-user` is in the `docker` group, which is root-equivalent; backups are on the same SSD as the database, so they do not survive losing that disk. Glances and AdGuard both ask for a password, and VNC asks for the Pi user's password.
- SSH from this PC needs no key because Tailscale checks the identity. This PC's `known_hosts` holds the Pi's ed25519 host key (`SHA256:(removed)`), which Kevin checked against the Pi itself.

**This PC:** `uv` is installed. `gh` (GitHub CLI) is installed and signed in as BAMK74.

## In progress

Nothing. Slice 1 is done, including SQLite storage and the Stock web forms, with `uv run pytest` passing all 221 tests on Python 3.13 and `ruff` clean. Claude wrote the implementations at Kevin's request.

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
- Quantities are stored in g and ml, and shown in the larger unit from 1000 up through `display_quantity` in `units.py` (1000 g reads 1 kg, 2500 ml reads 2.5 l, counts never change). The Set box on each item shows the same value and unit.
- Layout is a narrow centred card on phones and a roomier one from 640px, with dark mode.

`uv` is at `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe` and may not be on PATH in every shell.

## Next steps

1. **Live camera scanning.** Waiting on Kevin enabling HTTPS for the tailnet (Tailscale admin console, DNS page, Enable HTTPS), which he will do later. Enabling it publishes the tailnet and machine names in public certificate logs, which Kevin was told. Then serve the app with `tailscale serve`, add a live camera view with ZXing's `decodeFromVideoDevice` that only appears when `window.isSecureContext` is true, and test it on the phone. Scanning by photo already works over HTTP. Consider a built-in produce table for typical amounts (1 bunch of kale), since no reputable online source was found.
2. **Slice 2 (household).** Eaters, Categories, allergies, dislikes and calorie goals come first, because Menus are sized and filtered per person. Suggested: leave login and the Adult/Teen/Child permissions until later, since Tailscale already gates access, to reach Menus sooner.
3. **Later slices, in dependency order.** Recipes (ingredients, quantities, calories, weather tags), then Menu generation (hard rules for allergies, Shopping Allowance and Cooldown, then the weighted score, starting with calorie fit and expiring Stock), then Occasions and weather (needs a household town, not only the country), then the Shopping List, then OCR.
4. **Housekeeping.** Keep a copy off the Pi by downloading the database backup from Settings now and then, because a scheduled pull over Tailscale SSH would break silently when Tailscale asks for a fresh approval. Reboot the Pi once to confirm `/mnt/emu` and the `emu-the-menu` service come back on their own (it briefly takes down AdGuard Home, so choose a quiet moment). Set up the nightly backup, ideally copying off the Pi. Add the Scan term and the fridge-photo idea to the glossary and ADR 0003 if you want them recorded.

## Reference

Before building the Recipe and Stock slices, look at `Meal-matcher-scan` in the Codecademy DevProjects folder. It is a Lovable-generated prototype (TanStack, Supabase, Gemini) that photographs a fridge and suggests recipes. The ideas that carry over are the `ingredients` table shape (name, quantity, unit, category, freshness), the strict-JSON prompt with validation and a fallback parser, and the review-then-save flow. Its code does not carry over, because it depends on Supabase and the Lovable gateway.
