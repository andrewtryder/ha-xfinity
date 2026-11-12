"""Browserless login transport."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from custom_components.xfinity.login import (
    BrowserlessError,
    XfinityLoginError,
    XfinityLoginRejected,
    XfinityMfaRequired,
    XfinityTokenExchangeError,
    async_browserless_login,
    code_from_callback,
)


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(
        self,
        browserless_status=200,
        browserless_payload=None,
        token_status=200,
        token_payload=None,
    ):
        self.browserless_status = browserless_status
        self.browserless_payload = browserless_payload
        self.token_status = token_status
        self.token_payload = token_payload
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(url)
        if "bql" in url:
            if self.browserless_payload is None:
                authorize = kwargs["json"]["variables"]["url"]
                state = parse_qs(urlparse(authorize).query)["state"][0]
                callback = (
                    f"xfinitydigitalhome://auth?code={'oi-' + 'a' * 32}&state={state}"
                )
                payload = {
                    "data": {
                        "goto": {"status": 200},
                        "waitUser": {"time": 1},
                        "typeUser": {"time": 1},
                        "clickUser": {"time": 1},
                        "waitPass": {"time": 1},
                        "typePass": {"time": 1},
                        "clickPass": {"time": 1},
                        "callback": {"url": callback, "time": 1},
                    }
                }
            else:
                payload = self.browserless_payload
            return _Response(self.browserless_status, payload)
        if self.token_payload is not None:
            return _Response(self.token_status, self.token_payload)
        return _Response(
            self.token_status,
            {
                "access_token": "access",
                "id_token": "identity",
                "refresh_token": "rotated-refresh-token",
                "expires_in": 3600,
            },
        )


async def test_login_returns_a_refresh_token():
    result = await async_browserless_login(
        _Session(), "user@example.com", "secret", "k" * 20
    )
    assert result["refresh_token"] == "rotated-refresh-token"
    assert result["access_token"] == "access"


async def test_bad_browserless_key():
    with pytest.raises(BrowserlessError):
        await async_browserless_login(
            _Session(browserless_status=401, browserless_payload={}),
            "user@example.com",
            "secret",
            "k" * 20,
        )


async def test_browserless_rate_limit():
    with pytest.raises(BrowserlessError):
        await async_browserless_login(
            _Session(browserless_status=429, browserless_payload={}),
            "user@example.com",
            "secret",
            "k" * 20,
        )


async def test_password_rejected_before_password_field():
    payload = {"errors": [{"message": "timeout"}], "data": {"waitUser": {"time": 1}}}
    with pytest.raises(XfinityLoginRejected):
        await async_browserless_login(
            _Session(browserless_payload=payload),
            "user@example.com",
            "secret",
            "k" * 20,
        )


async def test_mfa_when_password_was_submitted():
    payload = {"errors": [{"message": "timeout"}], "data": {"typePass": {"time": 1}}}
    with pytest.raises(XfinityMfaRequired):
        await async_browserless_login(
            _Session(browserless_payload=payload),
            "user@example.com",
            "secret",
            "k" * 20,
        )


def test_callback_state_must_match():
    with pytest.raises(XfinityLoginError):
        code_from_callback(
            "xfinitydigitalhome://auth?code=oi-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&state=other",
            "expected",
        )


def test_code_from_callback_rejections():
    # Bad scheme or host
    with pytest.raises(XfinityLoginError):
        code_from_callback("https://auth.xfinity.com?code=oi-12345&state=s", "s")
    # Missing code param
    with pytest.raises(XfinityLoginError):
        code_from_callback("xfinitydigitalhome://auth?state=s", "s")
    # Missing oi- prefix
    with pytest.raises(XfinityLoginError):
        code_from_callback("xfinitydigitalhome://auth?code=badprefix&state=s", "s")


async def test_browserless_generic_http_error():
    with pytest.raises(BrowserlessError):
        await async_browserless_login(
            _Session(browserless_status=500, browserless_payload={}),
            "user@example.com",
            "secret",
            "k" * 20,
        )


async def test_browserless_non_dict_payload():
    with pytest.raises(BrowserlessError):
        await async_browserless_login(
            _Session(browserless_payload="not a dict"),
            "user@example.com",
            "secret",
            "k" * 20,
        )


async def test_browserless_login_did_not_finish():
    payload = {"errors": [{"message": "unknown"}], "data": {}}
    with pytest.raises(BrowserlessError, match="browserless login did not finish"):
        await async_browserless_login(
            _Session(browserless_payload=payload),
            "user@example.com",
            "secret",
            "k" * 20,
        )


async def test_exchange_errors():
    # 400 rejected
    with pytest.raises(XfinityLoginRejected):
        await async_browserless_login(
            _Session(token_status=400), "user@example.com", "secret", "k" * 20
        )

    # 500 server error
    with pytest.raises(XfinityTokenExchangeError):
        await async_browserless_login(
            _Session(token_status=500), "user@example.com", "secret", "k" * 20
        )

    # Missing refresh_token in response payload
    with pytest.raises(XfinityLoginRejected):
        await async_browserless_login(
            _Session(token_payload={"access_token": "a"}),
            "user@example.com",
            "secret",
            "k" * 20,
        )
