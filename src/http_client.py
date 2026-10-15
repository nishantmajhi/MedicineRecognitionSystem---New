"""
A minimal HTTP client built on Python's built-in ``urllib.request``.

Replaces the ``requests`` dependency. This project makes exactly one kind of
call -- a GET to the openFDA label endpoint, with a timeout, reading a JSON body
-- so the surface needed is small.

The exception hierarchy mirrors ``requests.exceptions`` so that call sites and
``except`` clauses written against it keep working::

    RequestException
    +-- Timeout
    +-- ConnectionError
    +-- HTTPError          (raised only by raise_for_status)

An important behavioural detail is preserved: urllib raises ``HTTPError`` for
4xx and 5xx responses, whereas ``requests`` returns them as ordinary Response
objects with the status code set. This module follows ``requests``, because
``FDAClient`` inspects ``status_code`` itself and expects 404 and 429 to arrive
as return values rather than exceptions.

Not supported: sessions, connection pooling, cookies, redirneg beyond urllib's
default redirect handling, streaming, or multipart uploads. The backend only
consumes an API; it does not need them.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

__all__ = [
    "Response",
    "RequestException",
    "Timeout",
    "ConnectionError",
    "HTTPError",
    "exceptions",
    "get",
    "quote",
]

DEFAULT_USER_AGENT = "MedicineRecognitionSystem/2.0 (+https://github.com/nishantmajhi)"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RequestException(IOError):
    """Base class for every failure raised by this module."""


class Timeout(RequestException):
    """The server did not respond within the timeout."""


class ConnectionError(RequestException):  # noqa: A001 - mirrors requests' name
    """The server could not be reached: DNS, refused connection, no route."""


class ConnectTimeout(ConnectionError, Timeout):
    """
    The connection itself timed out, before any response was possible.

    Deliberately both a :class:`ConnectionError` and a :class:`Timeout`, which is
    how ``requests`` models it, because it is genuinely both: the server could
    not be reached, and the reason was that time ran out.

    The distinction matters because whether an unreachable host times out or
    refuses depends on the network rather than on the caller. A firewall that
    drops packets produces a timeout; one that rejects them produces a refusal.
    With these as siblings a caller writing ``except ConnectionError`` to mean
    "could not reach the server" silently missed the dropped-packet case.
    """


class HTTPError(RequestException):
    """A 4xx or 5xx response, raised only by :meth:`Response.raise_for_status`."""

    def __init__(self, message: str, response: Optional["Response"] = None):
        super().__init__(message)
        self.response = response


class _ExceptionNamespace:
    """
    Mirrors ``requests.exceptions`` so ``except http_client.exceptions.Timeout``
    reads the same as the code it replaces.
    """

    RequestException = RequestException
    Timeout = Timeout
    ConnectionError = ConnectionError
    ConnectTimeout = ConnectTimeout
    HTTPError = HTTPError


exceptions = _ExceptionNamespace()


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

class Response:
    """An HTTP response. Mirrors the small part of ``requests.Response`` used."""

    def __init__(
        self,
        status_code: int,
        content: bytes,
        headers: Optional[Dict[str, str]] = None,
        url: str = "",
    ):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.url = url

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    @property
    def encoding(self) -> str:
        """Charset from the Content-Type header, defaulting to UTF-8."""
        content_type = self.headers.get("Content-Type", "")
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                return part.split("=", 1)[1].strip() or "utf-8"
        return "utf-8"

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding, errors="replace")

    def json(self) -> Any:
        """
        Parse the body as JSON.

        Raises:
            ValueError: If the body is not valid JSON. ``json.JSONDecodeError``
                subclasses ValueError, so existing handlers still catch it.
        """
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise HTTPError(
                f"{self.status_code} response for {self.url}", response=self
            )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Response [{self.status_code}]>"


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

def quote(value: str, safe: str = "") -> str:
    """Percent-encode a string for safe inclusion in a URL."""
    return urllib.parse.quote(value, safe=safe)


def get(
    url: str,
    timeout: Optional[float] = None,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    **kwargs: Any,
) -> Response:
    """
    Perform an HTTP GET.

    Args:
        url: The URL to fetch.
        timeout: Seconds to wait before giving up. Strongly recommended: with
            no timeout a hung server would block the worker thread forever.
        params: Optional query parameters, URL-encoded and appended.
        headers: Optional extra request headers.

    Returns:
        A :class:`Response`. 4xx and 5xx arrive here as return values, matching
        ``requests``, not as raised exceptions.

    Raises:
        Timeout: The request exceeded ``timeout``.
        ConnectionError: The host could not be reached.
        RequestException: Any other transport-level failure.
    """
    if params:
        separator = "&" if urllib.parse.urlparse(url).query else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(params)}"

    request_headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(url, headers=request_headers, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as raw:
            return Response(
                status_code=raw.status,
                content=raw.read(),
                headers=dict(raw.headers.items()),
                url=raw.url,
            )
    except urllib.error.HTTPError as error:
        # urllib treats 4xx/5xx as exceptions; requests does not. The body is
        # still readable here, and openFDA puts a useful error object in it.
        return Response(
            status_code=error.code,
            content=error.read(),
            headers=dict(error.headers.items()) if error.headers else {},
            url=url,
        )
    except socket.timeout as error:
        raise Timeout(f"Request to {url} timed out") from error
    except urllib.error.URLError as error:
        # URLError wraps the underlying cause. A connection that times out
        # surfaces this way, and is raised as ConnectTimeout so that callers
        # catching either ConnectionError or Timeout both see it.
        if isinstance(error.reason, socket.timeout):
            raise ConnectTimeout(f"Could not reach {url}: connection timed out") from error
        raise ConnectionError(f"Could not reach {url}: {error.reason}") from error
    except OSError as error:
        raise ConnectionError(f"Could not reach {url}: {error}") from error
