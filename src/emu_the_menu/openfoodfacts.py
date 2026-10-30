"""Barcode lookup on Open Food Facts (ADR 0004): a product's name and Pack size.

Only the barcode is sent. Found products are cached in the repository, so each
barcode is fetched once and cached ones work offline.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from emu_the_menu.units import UnknownUnitError, base_unit_for

_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}?fields={fields}"
_FIELDS = "code,product_name,quantity,product_quantity,product_quantity_unit,nutriments"
# Open Food Facts asks every app to identify itself with a custom User-Agent.
_USER_AGENT = "EmuTheMenu/0.1 (household-app; https://github.com/BAMK74/emu-the-menu)"
_TIMEOUT_SECONDS = 8
_BARCODE = re.compile(r"\d{8,14}")  # EAN-8, UPC-A, EAN-13 and GTIN-14
_QUANTITY_TEXT = re.compile(r"\s*(\d+(?:[.,]\d+)?)\s*([a-zA-Z]+)\s*")  # "500 g", "1,5 l"


class LookupUnavailableError(RuntimeError):
    """Open Food Facts could not be reached or gave an unreadable answer."""


@dataclass(frozen=True)
class ProductInfo:
    barcode: str
    name: str | None
    quantity: float | None  # the Pack size, in `unit`; None if the product has none we can read
    unit: str | None
    # Calories per 100 g, or per 100 ml for drinks, as the database lists them. None if not listed.
    kcal_per_100: float | None = None


class ProductCache(Protocol):
    def cached_product(self, barcode: str) -> ProductInfo | None: ...
    def cache_product(self, info: ProductInfo) -> None: ...


Fetch = Callable[[str], dict]


def http_fetch(barcode: str) -> dict:
    """Read one product from Open Food Facts. Raises OSError or ValueError on failure."""
    request = Request(
        _URL.format(barcode=barcode, fields=_FIELDS), headers={"User-Agent": _USER_AGENT}
    )
    try:
        with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 404:  # an unknown barcode, if the service answers that way
            return {"status": 0}
        raise


def find_product(
    barcode: str, cache: ProductCache, fetch: Fetch = http_fetch, need_energy: bool = False
) -> ProductInfo | None:
    """Look up a barcode, returning None if the product is unknown.

    Raises ValueError for text that is not a barcode and LookupUnavailableError
    when Open Food Facts cannot be reached and the barcode is not cached. With `need_energy`,
    a cached product that lists no calories is fetched again, in case it has some now.
    """
    barcode = re.sub(r"\s", "", barcode)
    if not _BARCODE.fullmatch(barcode):
        raise ValueError("A barcode is 8 to 14 digits")
    if (cached := cache.cached_product(barcode)) and not (
        need_energy and cached.kcal_per_100 is None
    ):
        return cached
    try:
        data = fetch(barcode)
    except (OSError, ValueError) as exc:
        raise LookupUnavailableError("Could not reach Open Food Facts") from exc
    if data.get("status") != 1:
        return None
    product = data.get("product") or {}
    quantity, unit = _pack_size(product)
    info = ProductInfo(
        barcode, product.get("product_name") or None, quantity, unit, _energy(product)
    )
    cache.cache_product(info)
    return info


def _energy(product: dict) -> float | None:
    """Calories per 100 g (or 100 ml) from the nutriments, or None if missing or not a number."""
    value = (product.get("nutriments") or {}).get("energy-kcal_100g")
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return round(float(value), 1)


def _round_pack_size(quantity: float) -> float:
    """Whole numbers from 10 up, two decimals below, so 20 oz reads 567 g and not 566.99 g."""
    return float(round(quantity)) if quantity >= 10 else round(quantity, 2)


def _pack_size(product: dict) -> tuple[float | None, str | None]:
    """The Pack size as (quantity, unit), from the numeric fields or else the text."""
    quantity, unit = product.get("product_quantity"), product.get("product_quantity_unit")
    if not isinstance(quantity, int | float):
        quantity, unit = None, None
    if quantity is None and (match := _QUANTITY_TEXT.fullmatch(product.get("quantity") or "")):
        quantity, unit = float(match.group(1).replace(",", ".")), match.group(2)
    quantity = _round_pack_size(quantity) if isinstance(quantity, int | float) else None
    if not quantity or quantity <= 0 or not unit:
        return None, None
    try:
        base_unit_for(unit)
    except UnknownUnitError:
        return None, None
    return float(quantity), unit.lower()
