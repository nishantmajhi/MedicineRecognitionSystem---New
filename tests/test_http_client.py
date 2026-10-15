"""
Tests for src.http_client, the project's urllib-based HTTP client.

Most tests run against a real ``http.server`` bound to localhost. Mocking
urllib would test almost nothing here: the module's job is to translate real
socket and protocol behaviour into a ``requests``-shaped API, and the
translation is the part that can be wrong.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src import http_client


class _Handler(BaseHTTPRequestHandler):
    """Serves canned responses keyed by path."""

    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        if self.path.startswith("/ok"):
            self._send(200, json.dumps({"results": [{"purpose": ["pain reliever"]}]}))
        elif self.path.startswith("/notfound"):
            self._send(404, json.dumps({"error": {"code": "NOT_FOUND"}}))
        elif self.path.startswith("/ratelimited"):
            self._send(429, json.dumps({"error": {"code": "TOO_MANY_REQUESTS"}}))
        elif self.path.startswith("/servererror"):
            self._send(500, "upstream exploded", content_type="text/plain")
        elif self.path.startswith("/notjson"):
            self._send(200, "<html>not json</html>", content_type="text/html")
        elif self.path.startswith("/echo"):
            self._send(200, json.dumps({"path": self.path}))
        elif self.path.startswith("/latin"):
            body = "caf\u00e9".encode("latin-1")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=latin-1")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send(404, "{}")

    def _send(self, status, body, content_type="application/json"):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        """Silence the default stderr access log."""


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


class TestSuccessfulRequests:
    def test_returns_200(self, server):
        response = http_client.get(f"{server}/ok", timeout=5)
        assert response.status_code == 200
        assert response.ok

    def test_parses_json(self, server):
        payload = http_client.get(f"{server}/ok", timeout=5).json()
        assert payload["results"][0]["purpose"] == ["pain reliever"]

    def test_exposes_text_and_content(self, server):
        response = http_client.get(f"{server}/ok", timeout=5)
        assert isinstance(response.content, bytes)
        assert "pain reliever" in response.text

    def test_exposes_headers(self, server):
        response = http_client.get(f"{server}/ok", timeout=5)
        assert "application/json" in response.headers["Content-Type"]

    def test_query_parameters_are_encoded(self, server):
        response = http_client.get(
            f"{server}/echo", params={"search": 'generic_name:"ibuprofen"'}, timeout=5
        )
        assert "ibuprofen" in response.json()["path"]

    def test_params_append_to_an_existing_query_string(self, server):
        response = http_client.get(f"{server}/echo?a=1", params={"b": "2"}, timeout=5)
        path = response.json()["path"]
        assert "a=1" in path and "b=2" in path

    def test_honours_the_response_charset(self, server):
        """A non-UTF-8 body must not come back as replacement characters."""
        response = http_client.get(f"{server}/latin", timeout=5)
        assert response.encoding == "latin-1"
        assert response.text == "caf\u00e9"

    def test_defaults_to_utf8_without_a_charset(self, server):
        response = http_client.get(f"{server}/servererror", timeout=5)
        assert response.encoding == "utf-8"


class TestErrorStatusCodes:
    """
    4xx and 5xx must be returned, not raised.

    urllib raises HTTPError for these; requests does not. FDAClient inspects
    ``status_code`` itself, so this module follows requests.
    """

    @pytest.mark.parametrize(
        "path, expected",
        [("/notfound", 404), ("/ratelimited", 429), ("/servererror", 500)],
    )
    def test_error_statuses_are_returned(self, server, path, expected):
        response = http_client.get(f"{server}{path}", timeout=5)
        assert response.status_code == expected
        assert not response.ok

    def test_error_body_is_still_readable(self, server):
        """openFDA puts a useful error object in the 404 body."""
        response = http_client.get(f"{server}/notfound", timeout=5)
        assert response.json()["error"]["code"] == "NOT_FOUND"

    def test_raise_for_status_raises_on_error(self, server):
        response = http_client.get(f"{server}/notfound", timeout=5)
        with pytest.raises(http_client.HTTPError):
            response.raise_for_status()

    def test_raise_for_status_is_silent_on_success(self, server):
        http_client.get(f"{server}/ok", timeout=5).raise_for_status()

    def test_invalid_json_raises_value_error(self, server):
        """json.JSONDecodeError subclasses ValueError, so callers still catch it."""
        response = http_client.get(f"{server}/notjson", timeout=5)
        with pytest.raises(ValueError):
            response.json()


class TestTransportFailures:
    def test_unreachable_host_raises_connection_error(self):
        with pytest.raises(http_client.ConnectionError):
            http_client.get("http://127.0.0.1:1/nothing", timeout=2)

    def test_unresolvable_name_raises_connection_error(self):
        with pytest.raises(http_client.ConnectionError):
            http_client.get("http://no-such-host.invalid/x", timeout=5)

    def test_failures_share_a_base_class(self):
        """Lets callers write one except clause, as with requests."""
        assert issubclass(http_client.Timeout, http_client.RequestException)
        assert issubclass(http_client.ConnectionError, http_client.RequestException)
        assert issubclass(http_client.HTTPError, http_client.RequestException)

    def test_exceptions_namespace_mirrors_requests(self):
        assert http_client.exceptions.Timeout is http_client.Timeout
        assert http_client.exceptions.RequestException is http_client.RequestException


class TestQuoting:
    def test_spaces_are_encoded(self):
        """Generic names such as 'dextromethorphan hydrobromide' contain spaces."""
        assert http_client.quote("dextromethorphan hydrobromide") == (
            "dextromethorphan%20hydrobromide"
        )

    def test_plain_names_are_unchanged(self):
        assert http_client.quote("ibuprofen") == "ibuprofen"

    def test_special_characters_are_encoded(self):
        encoded = http_client.quote('a"b&c')
        assert '"' not in encoded and "&" not in encoded


class TestResponseObject:
    def test_ok_reflects_the_status_code(self):
        assert http_client.Response(200, b"").ok
        assert http_client.Response(299, b"").ok
        assert not http_client.Response(400, b"").ok
        assert not http_client.Response(500, b"").ok

    def test_repr_shows_the_status(self):
        assert "404" in repr(http_client.Response(404, b""))
