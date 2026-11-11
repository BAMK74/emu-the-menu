# Emu the Menu

A self-hosted household app. It keeps track of what is in the fridge, freezer and pantry, and suggests a week of meals that fits each person's calorie goal, allergies and diet, the weather, special occasions and what you already have. It works out the shopping list from what the meals need, less what is in stock.

It runs on your own computer or a small home server such as a Raspberry Pi, and you use it in a web browser on your phone or computer. Your data stays in one file on that machine. Nothing is sent anywhere except a barcode to Open Food Facts when you look one up, a request for a recipe page when you import one, your town to Open-Meteo for the weather if you set one, and your country to Nager.Date for public holidays.

## What it does

- **Stock**: add food by hand or by barcode, with where it is kept and when it expires.
- **Recipes**: type them in or import them from a recipe web page. Each ingredient is checked once for allergens and food groups, and every recipe that uses it follows.
- **Eaters**: the people you cook for, with a calorie goal, allergies, a diet and dislikes.
- **Menu**: a suggested plan for the days and meals you choose. A recipe is never suggested to someone it does not suit. Swap, reshuffle or add guests to any meal, mark meals cooked, and accept a menu to set aside the stock it needs.
- **Occasions**: birthdays, parties and public holidays, with extra guests and calories.
- **Seasons**: the colours and the banner change with the season, with Halloween and Christmas colours in their weeks.

## Run it on your own computer

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), a small tool that installs Python and the app's parts for you. Install it, then in a terminal:

```
git clone https://github.com/BAMK74/emu-the-menu.git
cd emu-the-menu
uv sync --no-dev
uv run uvicorn emu_the_menu.web:create_app --factory --port 8000
```

Open <http://127.0.0.1:8000> in your browser. Start with **Settings** (your country and, if you like, your town for the weather), then add your **Eaters**, then some **Recipes**.

Everything is kept in `emu.db` in that folder. To keep it somewhere else, set `EMU_DB` before starting, for example `EMU_DB=/path/to/emu.db`. **Download a backup** from Settings now and then.

## Run it on a home server

To reach it from your phone around the house, run it on a machine that stays on, such as a Raspberry Pi, and start it with `--host` set to that machine's address on your network instead of the default. Do not open it to the internet: it has no logins yet, so anyone who can reach it can change everything. A private network such as [Tailscale](https://tailscale.com) is a safe way to reach it from outside the house.

The `deploy/` folder has the systemd service files used for one such setup. They name that machine's user and address, so change those to yours.

## Important

Emu the Menu is a cooking aid, not a dietitian, a doctor or an allergy service. It only knows the allergens someone has ticked for each ingredient, and it cannot know what a product really contains. Always read the label. Calorie goals and portions are rough guides, not medical advice. The full disclaimer is in the app at `/disclaimer`, which asks to be read and accepted.

## For developers

Requires uv and Python 3.13 (uv installs it).

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

Domain words are in [GLOSSARY.md](GLOSSARY.md), decisions in [docs/adr/](docs/adr/), the product and design in [PRODUCT.md](PRODUCT.md) and [DESIGN.md](DESIGN.md), and the state of the work in [docs/HANDOFF.md](docs/HANDOFF.md).

## Licence

[PolyForm Noncommercial 1.0.0](LICENSE.md): you may use, change and share it, but not commercially. See [NOTICE.md](NOTICE.md). The produce photos are from Wikimedia Commons under their own free licences, listed in `src/emu_the_menu/static/photos/credits.html`.
