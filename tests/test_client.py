"""HTTP client behavior that does not need Home Assistant."""

from __future__ import annotations

import json
from datetime import date

import pytest

from custom_components.xfinity.api import (
    XfinityAuthError,
    XfinityClient,
    XfinityClientRejected,
    XfinityConnectionError,
    XfinityError,
)
from custom_components.xfinity.const import AUTH_TOKEN_URL, GRAPHQL_URL

TOKEN = {
    "access_token": "access",
    "id_token": "identity",
    "expires_in": 3600,
    "refresh_token": "rotated-refresh-token",
}
USAGE = {"data": {"accountByServiceAccountId": {"internet": {"usage": {"monthlyUsage": [{
    "startDate": "2026-10-01",
    "endDate": "2026-10-31",
    "policy": "unlimited",
    "currentUsage": {"value": 10, "unit": "GB"},
}]}}}}}


class _Response:
    def __init__(self, status, payload=None, headers=None, raw=None):
        self.status = status
        self.headers = headers or {}
        if raw is not None:
            self._body = raw
        elif payload is None:
            self._body = b""
        else:
            self._body = json.dumps(payload).encode()

    async def read(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(responses, callback=None):
    return XfinityClient(_Session(responses), "21 Main Street", "refresh-token-value", on_tokens_rotated=callback)


async def test_refresh_persists_rotation_before_returning():
    seen = []
    client = _client([_Response(200, TOKEN)], seen.append)
    await client.async_refresh()
    assert seen == ["rotated-refresh-token"]
    assert client.access_token == "access"


async def test_graphql_401_retries_once():
    client = _client([
        _Response(200, TOKEN),
        _Response(401, {"error": "expired"}),
        _Response(200, TOKEN),
        _Response(200, USAGE),
    ])
    result = await client.async_fetch_usage(date(2026, 10, 18))
    assert result["used_gb"] == 10
    assert [url for _, url in client._session.calls].count(GRAPHQL_URL) == 2


async def test_graphql_403_is_not_an_auth_failure():
    client = _client([_Response(200, TOKEN), _Response(403, {"message": "blocked"})])
    with pytest.raises(XfinityError) as error:
        await client.async_fetch_usage(date(2026, 10, 18))
    assert not isinstance(error.value, XfinityAuthError)


async def test_timeout_is_a_connection_error():
    client = _client([TimeoutError()])
    with pytest.raises(XfinityConnectionError):
        await client.async_fetch_outage()


async def test_invalid_client_does_not_rotate():
    seen = []
    client = _client([_Response(401, {"error": "invalid_client"})], seen.append)
    with pytest.raises(XfinityClientRejected):
        await client.async_refresh()
    assert seen == []


async def test_invalid_json_is_a_connection_error():
    client = _client([_Response(200, raw=b"<html>")])
    with pytest.raises(XfinityConnectionError):
        await client.async_refresh()


def test_urls():
    assert AUTH_TOKEN_URL.startswith("https://")
    assert GRAPHQL_URL.startswith("https://")
