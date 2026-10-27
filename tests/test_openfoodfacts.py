import pytest

from emu_the_menu.openfoodfacts import (
    LookupUnavailableError,
    ProductInfo,
    find_product,
)
from emu_the_menu.repository import StockRepository

BARCODE = "3274080005003"


def found(**product):
    return {"status": 1, "product": {"code": BARCODE, "product_name": "Penne", **product}}


class FakeFetch:
    """Stands in for the network and counts how often it is called."""

    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def __call__(self, barcode):
        self.calls.append(barcode)
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def repo(tmp_path):
    with StockRepository(tmp_path / "emu.db") as repo:
        yield repo


def test_a_found_product_gives_name_and_pack_size(repo):
    fetch = FakeFetch(found(product_quantity=500, product_quantity_unit="g", quantity="500 g"))
    assert find_product(BARCODE, repo, fetch) == ProductInfo(BARCODE, "Penne", 500, "g")


def test_a_pack_size_in_millilitres(repo):
    fetch = FakeFetch(found(product_quantity=1500, product_quantity_unit="ml"))
    assert find_product(BARCODE, repo, fetch).unit == "ml"


def test_the_quantity_text_is_used_when_the_numbers_are_missing(repo):
    fetch = FakeFetch(found(quantity="1,5 l"))
    info = find_product(BARCODE, repo, fetch)
    assert (info.quantity, info.unit) == (1.5, "l")


@pytest.mark.parametrize("text", ["6 x 330 ml", "", "a lot", "0 g"])
def test_a_pack_size_that_cannot_be_read_is_left_blank(repo, text):
    info = find_product(BARCODE, repo, FakeFetch(found(quantity=text)))
    assert (info.name, info.quantity, info.unit) == ("Penne", None, None)


def test_a_unit_emu_does_not_know_is_left_blank(repo):
    fetch = FakeFetch(found(product_quantity=3, product_quantity_unit="furlong"))
    assert find_product(BARCODE, repo, fetch).quantity is None


def test_an_unknown_barcode_returns_none(repo):
    fetch = FakeFetch({"status": 0, "status_verbose": "product not found"})
    assert find_product(BARCODE, repo, fetch) is None


def test_a_product_without_a_name_is_still_found(repo):
    fetch = FakeFetch(
        {"status": 1, "product": {"product_quantity": 400, "product_quantity_unit": "g"}}
    )
    info = find_product(BARCODE, repo, fetch)
    assert (info.name, info.quantity) == (None, 400)


@pytest.mark.parametrize("bad", ["", "abc", "1234567", "123456789012345", "3274 080 005 00x"])
def test_a_bad_barcode_is_rejected_without_a_request(repo, bad):
    fetch = FakeFetch(found())
    with pytest.raises(ValueError, match="barcode"):
        find_product(bad, repo, fetch)
    assert fetch.calls == []


def test_spaces_inside_a_barcode_are_ignored(repo):
    fetch = FakeFetch(found(product_quantity=500, product_quantity_unit="g"))
    find_product(" 3274 0800 05003 ", repo, fetch)
    assert fetch.calls == [BARCODE]


@pytest.mark.parametrize("error", [OSError("offline"), TimeoutError(), ValueError("bad json")])
def test_a_network_failure_is_reported_as_unavailable(repo, error):
    with pytest.raises(LookupUnavailableError):
        find_product(BARCODE, repo, FakeFetch(error=error))


def test_a_found_product_is_fetched_once_and_then_cached(repo):
    fetch = FakeFetch(found(product_quantity=500, product_quantity_unit="g"))
    first = find_product(BARCODE, repo, fetch)
    second = find_product(BARCODE, repo, fetch)
    assert first == second
    assert len(fetch.calls) == 1


def test_the_cache_works_offline_and_survives_reopening(tmp_path):
    path = tmp_path / "emu.db"
    with StockRepository(path) as repo:
        find_product(
            BARCODE, repo, FakeFetch(found(product_quantity=500, product_quantity_unit="g"))
        )
    with StockRepository(path) as repo:
        offline = FakeFetch(error=OSError("offline"))
        assert find_product(BARCODE, repo, offline).quantity == 500
        assert offline.calls == []


def test_an_unknown_barcode_is_not_cached(repo):
    fetch = FakeFetch({"status": 0})
    find_product(BARCODE, repo, fetch)
    find_product(BARCODE, repo, fetch)
    assert len(fetch.calls) == 2


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        (566.9904625, 567),  # 20 oz converted to grams
        (4258.584, 4259),  # 144 fl oz converted to millilitres
        (415, 415),
        (10.4, 10),
        (1.5, 1.5),
        (0.254, 0.25),
        (9.999, 10),
    ],
)
def test_pack_sizes_are_rounded_to_a_sensible_precision(repo, quantity, expected):
    fetch = FakeFetch(found(product_quantity=quantity, product_quantity_unit="g"))
    assert find_product(BARCODE, repo, fetch).quantity == expected


def test_a_pack_size_that_rounds_to_nothing_is_left_blank(repo):
    fetch = FakeFetch(found(product_quantity=0.004, product_quantity_unit="g"))
    assert find_product(BARCODE, repo, fetch).quantity is None
