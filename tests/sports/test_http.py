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
BLACKHOLE_SECONDS = 1.5


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
    # The exact tail, not a bound on the whole message: 200 is the snippet width, so
    # equality fails on a wider snippet, a narrower one, and a snippet taken from the
    # wrong end, none of which a `< 500` would have noticed. The full length is
    # deliberately not pinned — the head of the message is `json`'s own "Expecting
    # value: line 1 column 1 (char 0)", wording CPython is free to reword.
    tail = "(body was 100000 bytes starting b'" + "x" * 200 + "')"
    assert str(excinfo.value)[-len(tail) :] == tail


def test_an_already_parsed_body_is_truncated_in_the_error():
    # Where a replay transport returning `json.load(f)` instead of raw bytes lands.
    body = {"players": [{"name": "x" * 100} for _ in range(500)]}

    def transport(url, headers, timeout):
        return body

    with pytest.raises(ApiError) as excinfo:
        _http.get_json("https://example.test/p", transport=transport)
    message = str(excinfo.value)
    assert "a dict:" in message
    # `repr(body)` is ~19kB, so this pins both that it is truncated to 200 characters
    # and that the ellipsis marking the truncation is there. Computed from the test's
    # own input rather than from `_http._BODY_SNIPPET`, so widening the constant is a
    # failure here instead of a silently-agreeing pair.
    tail = "(body was a dict: " + repr(body)[:200] + "...)"
    assert message[-len(tail) :] == tail


def test_an_empty_body_is_reported_as_empty():
    def transport(url, headers, timeout):
        return b""

    with pytest.raises(ApiError, match="0 bytes"):
        _http.get_json("https://example.test/p", transport=transport)


# `tests/conftest.py` arms an autouse guard over the whole suite that swaps
# `_http.default_transport` for one that raises `TransportLeak`. This file is the
# one that tests the transport itself, so it opts back out by marker: this test
# reads the attribute, and the loopback group below drives the real urllib code
# path. Neither reaches the network — the server is bound to 127.0.0.1.
@pytest.mark.allow_default_transport
def test_the_default_transport_is_the_pooled_one():
    assert _http.default_transport.__name__ == "_pooled_transport"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.served = 0  # requests answered on this connection so far

    def do_GET(self):  # noqa: N802  (stdlib-mandated name)
        self.served += 1
        if self.path.startswith("/blackhole") and self.served > 1:
            # A reused connection whose path went dead: no answer, no FIN.
            time.sleep(BLACKHOLE_SECONDS)
            self.close_connection = True
            return
        if self.path.startswith("/slow"):
            time.sleep(SLOW_RESPONSE_SECONDS)
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "/echo")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/hangup"):
            self.close_connection = True  # no response at all
            return
        if self.path.startswith("/boom"):
            status = 503
            body = b'{"error": "upstream blew up"}'
        else:
            status = 200
            # Echo what arrived so tests can assert on the request we really sent.
            body = json.dumps(
                {
                    "port": self.client_address[1],
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                }
            ).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if self.path.startswith("/once"):
                # Answered as keep-alive, then dropped -- what a server-side idle
                # timeout does to a pooled connection.
                self.close_connection = True
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


@pytest.mark.allow_default_transport
def test_the_real_transport_fetches_and_parses(server_url):
    assert _http.get_json(f"{server_url}/echo")["path"] == "/echo"


@pytest.mark.allow_default_transport
def test_the_real_transport_sends_caller_headers(server_url):
    payload = _http.get_json(f"{server_url}/echo", headers={"x-apisports-key": "abc"})
    assert payload["headers"]["x-apisports-key"] == "abc"


@pytest.mark.allow_default_transport
def test_the_real_transport_sends_the_default_user_agent(server_url):
    payload = _http.get_json(f"{server_url}/echo")
    assert payload["headers"]["user-agent"] == _http.DEFAULT_USER_AGENT
    assert "Python-urllib" not in payload["headers"]["user-agent"]


@pytest.mark.allow_default_transport
def test_a_caller_can_override_the_default_user_agent(server_url):
    payload = _http.get_json(f"{server_url}/echo", headers={"User-Agent": "custom/1"})
    assert payload["headers"]["user-agent"] == "custom/1"


@pytest.mark.allow_default_transport
def test_a_caller_can_override_the_default_user_agent_case_insensitively(server_url):
    payload = _http.get_json(f"{server_url}/echo", headers={"user-agent": "custom/2"})
    assert payload["headers"]["user-agent"] == "custom/2"


@pytest.mark.allow_default_transport
def test_an_http_error_status_becomes_an_api_error_carrying_the_body(server_url):
    with pytest.raises(ApiError) as excinfo:
        _http.get_json(f"{server_url}/boom")
    message = str(excinfo.value)
    assert "503" in message
    assert "upstream blew up" in message


@pytest.mark.allow_default_transport
def test_the_timeout_is_honoured(server_url):
    started = time.monotonic()
    # Pinned to the message the read-timeout path produces. A bare `raises` would
    # also be satisfied by connection-refused if the fixture server were broken,
    # which is fast enough to pass the elapsed-time bound without a timeout.
    with pytest.raises(ApiError, match="failed: timed out"):
        _http.get_json(f"{server_url}/slow", timeout=0.05)
    # The one bound in this suite that is not a weakened equality. Elapsed wall time
    # has no exact value to pin, and the claim being made *is* an inequality: the
    # client gave up before the handler's `sleep(SLOW_RESPONSE_SECONDS)` returned,
    # i.e. `timeout=` ended the request rather than the server ending it. Pinning
    # elapsed time to anything narrower would fail on a loaded CI box, and the
    # `match=` above is what stops the other way a fast failure could happen —
    # connection-refused — from satisfying this test.
    assert time.monotonic() - started < SLOW_RESPONSE_SECONDS


@pytest.mark.allow_default_transport
def test_the_pooled_transport_reuses_one_connection_for_sequential_requests(server_url):
    ports = {_http.get_json(f"{server_url}/echo")["port"] for _ in range(5)}
    assert len(ports) == 1


@pytest.mark.allow_default_transport
def test_a_redirect_is_followed_through_the_urllib_fallback(server_url):
    assert _http.get_json(f"{server_url}/redirect")["path"] == "/echo"


@pytest.fixture
def urllib_calls(monkeypatch):
    """Count the requests the pooled transport hands to the urllib fallback."""
    calls = []
    real = _http._urllib_transport

    def counting(*args):
        calls.append(args[0])
        return real(*args)

    monkeypatch.setattr(_http, "_urllib_transport", counting)
    return calls


@pytest.fixture(autouse=True)
def _empty_pool():
    _http._idle.clear()
    yield
    _http._idle.clear()


@pytest.mark.allow_default_transport
def test_a_dropped_connection_falls_back_to_urllib_instead_of_failing(server_url, urllib_calls):
    # `/hangup` answers nothing on either transport, so it fails -- but only after
    # the fallback has had its go, and the next request still works.
    with pytest.raises(ApiError):
        _http.get_json(f"{server_url}/hangup")
    assert len(urllib_calls) == 1
    assert _http.get_json(f"{server_url}/echo")["path"] == "/echo"


@pytest.mark.allow_default_transport
def test_a_pooled_connection_the_server_dropped_is_retried_on_a_fresh_one(server_url, urllib_calls):
    first = _http.get_json(f"{server_url}/once")["port"]
    # The server has closed that socket; the pool does not know yet.
    second = _http.get_json(f"{server_url}/echo")
    assert second["path"] == "/echo"
    assert second["port"] != first
    assert len(urllib_calls) == 1


@pytest.mark.allow_default_transport
def test_a_timeout_on_a_reused_connection_is_retried_on_a_fresh_one(server_url, urllib_calls):
    # The path went dead after the first answer: the server holds the next request
    # without replying. A fresh connection answers, so the call must succeed.
    assert _http.get_json(f"{server_url}/blackhole", timeout=0.5)["path"] == "/blackhole"
    started = time.monotonic()
    assert _http.get_json(f"{server_url}/blackhole", timeout=0.5)["path"] == "/blackhole"
    assert len(urllib_calls) == 1
    assert time.monotonic() - started < BLACKHOLE_SECONDS


@pytest.mark.allow_default_transport
def test_a_connection_idle_too_long_is_not_reused(server_url, urllib_calls, monkeypatch):
    first = _http.get_json(f"{server_url}/echo")["port"]
    monkeypatch.setattr(_http, "_MAX_IDLE_SECONDS", 0.0)
    second = _http.get_json(f"{server_url}/echo")["port"]
    assert second != first
    assert urllib_calls == []  # discarded up front, not discovered by failing


@pytest.mark.allow_default_transport
def test_a_request_that_cannot_be_sent_does_not_leak_its_connection(server_url):
    with pytest.raises(ApiError):
        _http.get_json(f"{server_url}/echo", headers={"X-Bad": "a\nb"})
    assert all(not conns for conns in _http._idle.values())


def test_https_connections_get_an_explicit_context_from_the_default_factory(monkeypatch):
    import ssl

    ctx = ssl.create_default_context()
    monkeypatch.setattr(_http.ssl, "_create_default_https_context", lambda: ctx)
    conn, reused = _http._checkout(("https", "example.test", None), 5.0)
    assert reused is False
    assert conn._context is ctx


@pytest.mark.allow_default_transport
def test_a_configured_proxy_bypasses_the_pool(server_url, monkeypatch):
    calls = []
    monkeypatch.setattr(_http, "_urllib_transport", lambda *a: calls.append(a) or b"{}")
    monkeypatch.setattr(_http.urllib.request, "getproxies", lambda: {"http": "http://proxy.test"})
    monkeypatch.setattr(_http.urllib.request, "proxy_bypass", lambda host: False)
    _http.get_json(f"{server_url}/echo")
    assert len(calls) == 1


@pytest.mark.allow_default_transport
def test_the_pool_is_safe_under_concurrent_use(server_url):
    from retro_roster_patcher.core.concurrency import parallel_map

    paths = parallel_map(lambda i: _http.get_json(f"{server_url}/p{i}")["path"], range(40))
    assert paths == [f"/p{i}" for i in range(40)]
