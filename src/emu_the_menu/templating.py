"""The Jinja templates shared by every page."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from emu_the_menu.units import display_quantity

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
TEMPLATES.env.globals["display_quantity"] = display_quantity
