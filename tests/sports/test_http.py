import json

import pytest

from retro_roster_patcher.core.errors import ApiError
from retro_roster_patcher.sports import _http


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


def test_the_default_transport_is_urllib_based():
    assert _http.default_transport.__name__ == "_urllib_transport"
