# Emu the Menu

A self-hosted household app. It keeps track of what is in the fridge, freezer and pantry, and suggests a week of meals that fits each person's calorie goal, allergies and diet, the weather, special occasions and what you already have. It works out the shopping list from what the meals need, less what is in stock.

It runs on your own computer or a server you control, and you use it in a web browser on your phone or computer. Your data stays in a Postgres database you run. Nothing is sent anywhere except a barcode to Open Food Facts when you look one up, a request for a recipe page when you import one, your town to Open-Meteo for the weather if you set one, and your country to Nager.Date for public holidays.

## What it does

- **Stock**: add food by hand or by barcode, with where it is kept and when it expires.
- **Recipes**: type them in or import them from a recipe web page. Each ingredient is checked once for allergens and food groups, and every recipe that uses it follows.
- **Eaters**: the people you cook for, with a calorie goal, allergies, a diet and dislikes.
- **Menu**: a suggested plan for the days and meals you choose. A recipe is never suggested to someone it does not suit. Swap, reshuffle or add guests to any meal, mark meals cooked, and accept a menu to set aside the stock it needs.
- **Occasions**: birthdays, parties and public holidays, with extra guests and calories.
- **Seasons**: the colours and the banner change with the season, with Halloween and Christmas colours in their weeks.

## Run it on your own computer

You need two things:

- [uv](https://docs.astral.sh/uv/getting-started/installation/), a small tool that installs Python and the app's parts for you.
- A Postgres database, version 18. The simplest way is [Docker](https://docs.docker.com/get-docker/): this repo's `compose.yaml` starts one with `docker compose up -d`. On a Mac, `brew install postgresql@18` works too (see [For developers](#for-developers)).

Then, in a terminal:

```
git clone https://github.com/BAMK74/emu-the-menu.git
cd emu-the-menu
docker compose up -d
uv sync --no-dev
export EMU_DATABASE_URL=postgresql://emu:emu@localhost:5432/emu
uv run python -m emu_the_menu.accounts create-adult "Your name"
uv run uvicorn emu_the_menu.web:create_app --factory --port 8000
```

`create-adult` asks for a password (at least 10 characters) and makes you the first **Adult**: the first account, and the only one made this way. Open <http://127.0.0.1:8000> in your browser and sign in with your name and that password. Start with **Settings** (your country and, if you like, your town for the weather), then add your **Eaters**, then some **Recipes**.

### Accounts

Everyone who uses emu signs in, with their name on the Eaters page and a password. An **Adult** can change everything and gives the others their accounts under **Settings, Accounts**. A **Teen** can see everything, mark meals cooked, and change their own dislikes and diet. Children have no account: an Adult looks after them. If nobody can sign in any more, set a new password from the command line: `uv run python -m emu_the_menu.accounts reset-password "Your name"`.

Emu keeps everything in the database that `EMU_DATABASE_URL` points to, and creates its tables there on the first start. **Export as JSON** in Settings gives you a readable copy of what you entered.

### Bringing over an emu.db from before

Older versions kept everything in a SQLite file, `emu.db`. To move it into Postgres, point `EMU_DATABASE_URL` at an empty database and run:

```
uv run python -m emu_the_menu.import_sqlite /path/to/emu.db
```

It copies everything (stock, ingredients, eaters, recipes, menus, occasions and settings), prints how many rows each table had before and after, and refuses to touch a database that already holds data, accounts included. So import first, then run `create-adult` with the name of someone already in the imported data. The SQLite file is only read.

## Run it in a container

The image runs as user 10001 on port 8080, needs no special permissions and writes nothing, so it runs read-only. Point it at a Postgres:

```
docker build -t emu .
docker run --user 10001 --read-only --tmpfs /tmp --cap-drop ALL -p 8080:8080 \
  -e EMU_DATABASE_URL=postgresql://emu:emu@host.docker.internal:5432/emu emu
```

`GET /healthz` answers `ok` when the app is up, without touching the database. Every push to `main` publishes the image for arm64 and amd64 as `ghcr.io/bamk74/emu-the-menu:main-<unix time>-<short sha>`. `scripts/smoke-container.sh IMAGE` checks an image the way the Server runs it. To import an old `emu.db` into a running container, send it through standard input, since the container cannot be written to:

```
docker exec -i <container> python -c "import sys; open('/tmp/emu.db','wb').write(sys.stdin.buffer.read())" < emu.db
docker exec <container> python -m emu_the_menu.import_sqlite /tmp/emu.db
```

Then make the first Adult, in the container too: `docker exec -it <container> python -m emu_the_menu.accounts create-adult "Your name"`.

## Run it on a home server

Emu is moving to the Traquenard Server, where it will run alongside the household's other projects. This repo no longer carries a home-server setup.

Do not open Emu to the internet. It has no logins yet, so anyone who can reach it can change everything. Run it on your own computer as above, or on a private network only you and your household can reach.

## Important

Emu the Menu is a cooking aid, not a dietitian, a doctor or an allergy service. It only knows the allergens someone has ticked for each ingredient, and it cannot know what a product really contains. Always read the label. Calorie goals and portions are rough guides, not medical advice. The full disclaimer is in the app at `/disclaimer`, which asks to be read and accepted.

## For developers

Requires uv and Python 3.13 (uv installs it), and a Postgres 18 for the tests. The tests use the database in `EMU_TEST_DATABASE_URL` (by default `postgresql://emu:emu@localhost:5432/emu_test`), which they **drop and create again on every run**, so never point it at real data. The role needs permission to create databases.

With Docker, `docker compose up -d` gives you exactly that. Without it, on a Mac:

```
brew install postgresql@18
brew services start postgresql@18
/opt/homebrew/opt/postgresql@18/bin/psql -d postgres -c "CREATE ROLE emu LOGIN PASSWORD 'emu' CREATEDB"
/opt/homebrew/opt/postgresql@18/bin/createdb -O emu emu
```

Then:

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

GitHub Actions (`.github/workflows/ci.yml`) runs the same against a `postgres:18` service on every push to `main` and every pull request, then builds the image on arm64 and amd64 and smoke-checks it; on `main` it also publishes it. `scripts/measure_load.py` puts a household's load on a running container and reports its peak memory and CPU.

The schema is built from the numbered files in `src/emu_the_menu/migrations/`, applied in order on start. To change it, add the next file (such as `002_meal_notes.sql`) and never edit one that has shipped.

Domain words are in [GLOSSARY.md](GLOSSARY.md), decisions in [docs/adr/](docs/adr/), the product and design in [PRODUCT.md](PRODUCT.md) and [DESIGN.md](DESIGN.md), and the state of the work in [docs/HANDOFF.md](docs/HANDOFF.md).

## Licence

[PolyForm Noncommercial 1.0.0](LICENSE.md): you may use, change and share it, but not commercially. See [NOTICE.md](NOTICE.md). The produce photos are from Wikimedia Commons under their own free licences, listed in `src/emu_the_menu/static/photos/credits.html`.
