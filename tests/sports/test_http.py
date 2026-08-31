import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from retro_roster_patcher.core.errors import ApiError
from retro_roster_patcher.sports import _http

# Comfortably longer than the timeout the timeout test passes, short enough not to
# drag. The handler swallows the resulting disconnect, so nothing depends on this
# sleep finishing before the suite does.
SLOW_RESPONSE_SECONDS = 0.5


def test_get_json_parses_the_transport_response():
    def transport(url, headers, timeout):
        assert url == "https://example.test/teams"
        assert headers == {}
        assert timeout == _http.DEFAULT_TIMEOUT
        return json.dumps({"teams": [1, 2]}).encode()

    assert _http.get_json("https://example.test/teams", transport=transport) == {"teams": [1, 2]}


def test_params_are_appended_as_a_query_string():
    seen = {}

    def transport(url, headers, timeout):
        seen["url"] = url
        return b"{}"

    _http.get_json(
        "https://example.test/players",
        params={"team": 33, "season": 2025},
        transport=transport,
    )
    assert seen["url"] == "https://example.test/players?team=33&season=2025"


def test_none_valued_params_are_dropped():
    seen = {}

    def transport(url, headers, timeout):
        seen["url"] = url
        return b"{}"

    _http.get_json("https://example.test/p", params={"a": 1, "b": None}, transport=transport)
    assert seen["url"] == "https://example.test/p?a=1"


def test_params_join_a_url_that_already_has_a_query_string():
    seen = {}

    def transport(url, headers, timeout):
        seen["url"] = url
        return b"{}"

    _http.get_json("https://example.test/p?v=1", params={"a": 2}, transport=transport)
    assert seen["url"] == "https://example.test/p?v=1&a=2"


def test_a_list_valued_param_repeats_the_key():
    seen = {}

    def transport(url, headers, timeout):
        seen["url"] = url
        return b"{}"

    _http.get_json("https://example.test/p", params={"ids": [1, 2]}, transport=transport)
    assert seen["url"] == "https://example.test/p?ids=1&ids=2"


def test_headers_are_forwarded():
    seen = {}

    def transport(url, headers, timeout):
        seen["headers"] = headers
        return b"{}"

    _http.get_json(
        "https://example.test/p", headers={"x-apisports-key": "abc"}, transport=transport
    )
    assert seen["headers"] == {"x-apisports-key": "abc"}


def test_a_transport_failure_becomes_an_api_error():
    def transport(url, headers, timeout):
        raise OSError("connection reset")

    with pytest.raises(ApiError, match="connection reset"):
        _http.get_json("https://example.test/p", transport=transport)


def test_malformed_json_becomes_an_api_error():
    def transport(url, headers, timeout):
        return b"<html>503</html>"

    with pytest.raises(ApiError, match="Malformed JSON"):
        _http.get_json("https://example.test/p", transport=transport)


def test_a_malformed_body_is_quoted_in_the_error():
    def transport(url, headers, timeout):
        return b"<html>captive portal</html>"

    with pytest.raises(ApiError) as excinfo:
        _http.get_json("https://example.test/p", transport=transport)
    message = str(excinfo.value)
    assert "27 bytes" in message
    assert "captive portal" in message


def test_a_huge_body_is_truncated_in_the_error():
    def transport(url, headers, timeout):
        return b"x" * 100_000

    with pytest.raises(ApiError) as excinfo:
        _http.get_json("https://example.test/p", transport=transport)
    assert len(str(excinfo.value)) < 500


def test_an_already_parsed_body_is_truncated_in_the_error():
    # Where a replay transport returning `json.load(f)` instead of raw bytes lands.
    def transport(url, headers, timeout):
        return {"players": [{"name": "x" * 100} for _ in range(500)]}

    with pytest.raises(ApiError) as excinfo:
        _http.get_json("https://example.test/p", transport=transport)
    message = str(excinfo.value)
    assert "a dict:" in message
    assert len(message) < 500


def test_an_empty_body_is_reported_as_empty():
    def transport(url, headers, timeout):
        return b""

    with pytest.raises(ApiError, match="0 bytes"):
        _http.get_json("https://example.test/p", transport=transport)


def test_the_default_transport_is_urllib_based():
    assert _http.default_transport.__name__ == "_urllib_transport"


# --- the real urllib transport, against a loopback server (never the network) ---


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802  (stdlib-mandated name)
        if self.path.startswith("/slow"):
            time.sleep(SLOW_RESPONSE_SECONDS)
        if self.path.startswith("/boom"):
            status = 503
            body = b'{"error": "upstream blew up"}'
        else:
            status = 200
            # Echo what arrived so tests can assert on the request we really sent.
            body = json.dumps(
                {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}}
            ).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Expected: the timeout test hangs up mid-`/slow`. Left to propagate,
            # socketserver dumps a traceback to stderr from this daemon thread,
            # and pytest staples it to whichever test is running at the time.
            self.close_connection = True

    def log_message(self, *args):
        """Silence the default stderr access log."""


@pytest.fixture(scope="module")
def server_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    # A tight poll interval keeps `shutdown()` from costing the default half second.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.daemon = True
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_real_transport_fetches_and_parses(server_url):
    assert _http.get_json(f"{server_url}/echo")["path"] == "/echo"


def test_the_real_transport_sends_caller_headers(server_url):
    payload = _http.get_json(f"{server_url}/echo", headers={"x-apisports-key": "abc"})
    assert payload["headers"]["x-apisports-key"] == "abc"


def test_the_real_transport_sends_the_default_user_agent(server_url):
    payload = _http.get_json(f"{server_url}/echo")
    assert payload["headers"]["user-agent"] == _http.DEFAULT_USER_AGENT
    assert "Python-urllib" not in payload["headers"]["user-agent"]


def test_a_caller_can_override_the_default_user_agent(server_url):
    payload = _http.get_json(f"{server_url}/echo", headers={"User-Agent": "custom/1"})
    assert payload["headers"]["user-agent"] == "custom/1"


def test_a_caller_can_override_the_default_user_agent_case_insensitively(server_url):
    payload = _http.get_json(f"{server_url}/echo", headers={"user-agent": "custom/2"})
    assert payload["headers"]["user-agent"] == "custom/2"


def test_an_http_error_status_becomes_an_api_error_carrying_the_body(server_url):
    with pytest.raises(ApiError) as excinfo:
        _http.get_json(f"{server_url}/boom")
    message = str(excinfo.value)
    assert "503" in message
    assert "upstream blew up" in message


def test_the_timeout_is_honoured(server_url):
    started = time.monotonic()
    # Pinned to the message the read-timeout path produces. A bare `raises` would
    # also be satisfied by connection-refused if the fixture server were broken,
    # which is fast enough to pass the elapsed-time bound without a timeout.
    with pytest.raises(ApiError, match="failed: timed out"):
        _http.get_json(f"{server_url}/slow", timeout=0.05)
    assert time.monotonic() - started < SLOW_RESPONSE_SECONDS
