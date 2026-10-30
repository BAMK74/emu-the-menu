"""Fetching a web page that a person pasted in, without letting it reach anything private.

The Pi sits on a home network and a tailnet, so a pasted address must never be allowed to make it
call something there (the router, another device, Emu itself, a cloud metadata address). Every
address is therefore resolved here, refused unless it is a public one, and the connection is then
made to that exact checked address, so a site cannot answer differently the second time. Redirects
are followed by hand and checked the same way. Pages are size limited, and only plain http and
https on the usual ports are used.
"""

import http.client
import ipaddress
import socket
import ssl
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

_USER_AGENT = "EmuTheMenu/0.1 (household-app; https://github.com/BAMK74/emu-the-menu)"
_TIMEOUT_SECONDS = 10
_MAX_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 4
_PORTS = {"http": 80, "https": 443}


class UnsafeAddressError(ValueError):
    """The address is not a plain public web address."""


class PageUnavailableError(RuntimeError):
    """The page could not be fetched."""


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    ip: str  # the checked address to connect to
    https: bool
    path: str


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]  # names in lower case
    body: bytes


Resolve = Callable[[str, int], list[str]]
Get = Callable[[Target], Response]


def _resolve(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # is_global is False for private, loopback, link-local and shared (tailnet) ranges.
    return ip.is_global and not ip.is_multicast


def check_public_url(url: str, resolve: Resolve = _resolve) -> Target:
    """Check an address and return where to connect. Raises UnsafeAddressError if it is unsafe."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise UnsafeAddressError("That is not a web address") from None
    if parts.scheme not in _PORTS or not parts.hostname:
        raise UnsafeAddressError("Enter a web address that starts with http:// or https://")
    if parts.username or parts.password:
        raise UnsafeAddressError("Web addresses with a user name or password are not accepted")
    port = port or _PORTS[parts.scheme]
    if port not in _PORTS.values():
        raise UnsafeAddressError("Only the usual web ports are accepted")
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            addresses = resolve(host, port)
        except OSError:
            raise PageUnavailableError(f"Could not find {host}") from None
    if not addresses or not all(_is_public(a) for a in addresses):
        raise UnsafeAddressError("That address is not a public website")
    # Prefer IPv4: the Pi may have no IPv6 route.
    chosen = sorted(addresses, key=lambda a: ":" in a)[0]
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return Target(host, port, chosen, parts.scheme == "https", path)


class _Pinned(http.client.HTTPConnection):
    """Connects to a given, already checked address instead of looking the name up again."""

    def __init__(self, target: Target):
        super().__init__(target.host, target.port, timeout=_TIMEOUT_SECONDS)
        self._target = target

    def connect(self) -> None:
        sock = socket.create_connection((self._target.ip, self.port), self.timeout)
        if self._target.https:
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=self.host)
        self.sock = sock


def _http_get(target: Target) -> Response:
    connection = _Pinned(target)
    try:
        connection.request(
            "GET",
            target.path,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "gzip",
                "Connection": "close",
            },
        )
        answer = connection.getresponse()
        body = answer.read(_MAX_BYTES + 1)
        return Response(answer.status, {k.lower(): v for k, v in answer.getheaders()}, body)
    finally:
        connection.close()


def _unpack(response: Response) -> bytes:
    body = response.body
    if len(body) > _MAX_BYTES:
        raise PageUnavailableError("That page is too big")
    if response.headers.get("content-encoding", "").lower() == "gzip":
        try:
            unpacker = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = unpacker.decompress(body, _MAX_BYTES + 1)
        except zlib.error:
            raise PageUnavailableError("That page could not be read") from None
        if len(body) > _MAX_BYTES or unpacker.unconsumed_tail:
            raise PageUnavailableError("That page is too big")
    return body


def _charset(content_type: str) -> str:
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value.strip("\"'")
    return "utf-8"


def fetch_page(url: str, resolve: Resolve = _resolve, get: Get = _http_get) -> str:
    """The text of the web page at `url`.

    Raises UnsafeAddressError for an address that is not a public website (also after a redirect)
    and PageUnavailableError when the page cannot be fetched or is not a reasonable web page.
    """
    for _ in range(_MAX_REDIRECTS + 1):
        target = check_public_url(url, resolve)
        try:
            response = get(target)
        except (OSError, http.client.HTTPException):
            raise PageUnavailableError(f"Could not reach {target.host}") from None
        if response.status in (301, 302, 303, 307, 308) and response.headers.get("location"):
            url = urljoin(url, response.headers["location"])
            continue
        if response.status in (401, 403, 429):
            raise PageUnavailableError(
                f"That site does not allow Emu the Menu to read its pages (error {response.status})"
            )
        if response.status != 200:
            raise PageUnavailableError(f"The site answered with error {response.status}")
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower():
            raise PageUnavailableError("That address is not a web page")
        body = _unpack(response)
        return body.decode(_charset(content_type), errors="replace")
    raise PageUnavailableError("The site sent too many redirects")
