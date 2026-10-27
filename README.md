# Emu the Menu

A self-hosted household app that tracks what is in the fridge, freezer and pantry, and suggests Menus that fit calorie goals, weather, occasions and what is already in stock. It runs on a Raspberry Pi and is reached over Tailscale.

Domain language is in [GLOSSARY.md](GLOSSARY.md). Decisions are in [docs/adr/](docs/adr/).

## Develop

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Status

Slice 1 is done: Stock (add, edit, delete, expiry and shelf life), a household country setting, and barcode lookup through Open Food Facts with photo scanning. Login, Recipes and Menu generation come later. See [docs/HANDOFF.md](docs/HANDOFF.md).

## Licence

[PolyForm Noncommercial 1.0.0](LICENSE.md). See [NOTICE.md](NOTICE.md).
