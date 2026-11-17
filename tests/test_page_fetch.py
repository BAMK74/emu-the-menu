import gzip

import pytest

from emu_the_menu.page_fetch import (
    PageUnavailableError,
    Response,
    UnsafeAddressError,
    check_public_url,
    fetch_page,
)


def resolver(mapping):
    def resolve(host, port):
        if host not in mapping:
            raise OSError("no such host")
        return mapping[host] if isinstance(mapping[host], list) else [mapping[host]]

    return resolve


PUBLIC = "93.184.216.34"
RESOLVE = resolver({"example.com": PUBLIC, "www.example.com": PUBLIC})

# --- which addresses may be fetched -----------------------------------------------------------


def test_a_public_site_is_allowed_and_its_checked_address_is_returned():
    target = check_public_url("https://example.com/soup?x=1", RESOLVE)
    assert (target.host, target.port, target.ip, target.https) == ("example.com", 443, PUBLIC, True)
    assert target.path == "/soup?x=1"


def test_http_uses_port_80_and_a_missing_path_is_a_slash():
    target = check_public_url("http://example.com", RESOLVE)
    assert (target.port, target.path, target.https) == (80, "/", False)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "example.com/soup",
        "https://",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
        "https://example.com:22/",
    ],
)
def test_only_plain_web_addresses_are_accepted(url):
    with pytest.raises(UnsafeAddressError):
        check_public_url(url, RESOLVE)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # this computer
        "10.1.2.3",
        "192.168.1.10",  # the home network
        "172.16.0.5",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # the tailnet (100.64.0.0/10)
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fd00::1",
        "::ffff:127.0.0.1",
    ],
)
def test_addresses_inside_the_home_or_tailnet_are_refused(address):
    with pytest.raises(UnsafeAddressError):
        check_public_url("https://evil.example/", resolver({"evil.example": address}))


def test_one_private_answer_among_public_ones_is_enough_to_refuse():
    both = resolver({"mixed.example": [PUBLIC, "192.168.1.1"]})
    with pytest.raises(UnsafeAddressError):
        check_public_url("https://mixed.example/", both)


def test_a_literal_private_address_is_refused_without_a_lookup():
    with pytest.raises(UnsafeAddressError):
        check_public_url("http://192.168.1.1/", resolver({}))
    with pytest.raises(UnsafeAddressError):
        check_public_url("http://[::1]/", resolver({}))


def test_a_name_that_does_not_resolve_is_reported():
    with pytest.raises(PageUnavailableError, match="find"):
        check_public_url("https://nowhere.example/", resolver({}))


# --- fetching ---------------------------------------------------------------------------------


def responses(*answers):
    """A stand-in for the network that gives the answers in order and records where it went."""
    calls = []
    queue = list(answers)

    def get(target):
        calls.append(target)
        return queue.pop(0)

    return get, calls


def ok(body="<html>hi</html>", **headers):
    data = body.encode() if isinstance(body, str) else body
    return Response(200, {"content-type": "text/html; charset=utf-8", **headers}, data)


def test_a_page_is_returned_as_text():
    get, calls = responses(ok("<p>Tomato</p>"))
    assert fetch_page("https://example.com/", RESOLVE, get) == "<p>Tomato</p>"
    assert calls[0].ip == PUBLIC


def test_the_charset_in_the_header_is_used():
    body = "<p>Crème</p>".encode("latin-1")
    get, _ = responses(Response(200, {"content-type": "text/html; charset=iso-8859-1"}, body))
    assert fetch_page("https://example.com/", RESOLVE, get) == "<p>Crème</p>"


def test_a_redirect_is_followed_and_the_new_address_is_checked_too():
    get, calls = responses(
        Response(301, {"location": "https://www.example.com/new"}, b""), ok("<p>ok</p>")
    )
    assert fetch_page("https://example.com/old", RESOLVE, get) == "<p>ok</p>"
    assert [c.host for c in calls] == ["example.com", "www.example.com"]
    assert calls[1].path == "/new"


def test_a_relative_redirect_stays_on_the_site():
    get, calls = responses(Response(302, {"location": "/moved"}, b""), ok())
    fetch_page("https://example.com/old", RESOLVE, get)
    assert (calls[1].host, calls[1].path) == ("example.com", "/moved")


def test_a_redirect_to_a_private_address_is_refused():
    resolve = resolver({"example.com": PUBLIC, "inside.example": "192.168.1.1"})
    get, calls = responses(Response(302, {"location": "http://inside.example/admin"}, b""))
    with pytest.raises(UnsafeAddressError):
        fetch_page("https://example.com/", resolve, get)
    assert len(calls) == 1


def test_a_redirect_loop_gives_up():
    loop = Response(302, {"location": "https://example.com/"}, b"")
    get, calls = responses(*[loop] * 10)
    with pytest.raises(PageUnavailableError, match="redirect"):
        fetch_page("https://example.com/", RESOLVE, get)
    assert len(calls) <= 6


@pytest.mark.parametrize("status", [403, 404, 500])
def test_an_error_answer_is_reported_with_its_number(status):
    get, _ = responses(Response(status, {}, b""))
    with pytest.raises(PageUnavailableError, match=str(status)):
        fetch_page("https://example.com/", RESOLVE, get)


def test_a_page_that_is_not_html_is_refused():
    get, _ = responses(Response(200, {"content-type": "application/pdf"}, b"%PDF"))
    with pytest.raises(PageUnavailableError, match="web page"):
        fetch_page("https://example.com/", RESOLVE, get)


def test_a_huge_page_is_refused():
    get, _ = responses(ok("x" * (3 * 1024 * 1024)))
    with pytest.raises(PageUnavailableError, match="big"):
        fetch_page("https://example.com/", RESOLVE, get)


def test_a_gzipped_page_is_unpacked():
    get, _ = responses(ok(gzip.compress(b"<p>zipped</p>"), **{"content-encoding": "gzip"}))
    assert fetch_page("https://example.com/", RESOLVE, get) == "<p>zipped</p>"


def test_a_gzip_bomb_is_refused():
    bomb = gzip.compress(b"0" * (50 * 1024 * 1024))
    get, _ = responses(ok(bomb, **{"content-encoding": "gzip"}))
    with pytest.raises(PageUnavailableError, match="big"):
        fetch_page("https://example.com/", RESOLVE, get)


def test_a_network_failure_is_reported():
    def get(target):
        raise OSError("connection reset")

    with pytest.raises(PageUnavailableError, match="reach"):
        fetch_page("https://example.com/", RESOLVE, get)


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_site_that_blocks_us_says_so(status):
    get, _ = responses(Response(status, {}, b""))
    with pytest.raises(PageUnavailableError, match="does not allow"):
        fetch_page("https://example.com/", RESOLVE, get)
