"""Tests for browser-based OAuth PKCE authorization."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.xfinity.browser_auth import (
    BrowserAuthorization,
    async_complete_browser_auth,
    validate_and_extract_code,
)
from custom_components.xfinity.login import (
    XfinityBrowserSessionExpiredError,
    XfinityInvalidCallbackError,
    XfinityLoginRejected,
)


def test_create_browser_authorization():
    auth = BrowserAuthorization.create(timeout_seconds=300)
    assert auth.url.startswith("https://xerxes-sub.xerxessecure.com")
    assert "code_challenge=" in auth.url
    assert "state=" in auth.url
    assert auth.verifier
    assert auth.activity
    assert auth.state
    assert not auth.consumed
    assert auth.expires_at > time.time()


def test_consume_valid_callback():
    auth = BrowserAuthorization.create(timeout_seconds=300)
    callback = f"xfinitydigitalhome://auth?code=oi-1234567890abcdef&state={auth.state}"
    code, verifier, activity = auth.consume(callback)
    assert code == "oi-1234567890abcdef"
    assert verifier == auth.verifier
    assert activity == auth.activity
    assert auth.consumed

    # Consuming a second time raises session expired (one-time use)
    with pytest.raises(XfinityBrowserSessionExpiredError):
        auth.consume(callback)


def test_consume_expired_session():
    auth = BrowserAuthorization(
        url="https://example.com",
        verifier="v",
        activity="a",
        state="s",
        expires_at=time.time() - 1,
    )
    with pytest.raises(XfinityBrowserSessionExpiredError):
        auth.consume("xfinitydigitalhome://auth?code=oi-123&state=s")


def test_validate_and_extract_code_valid():
    code = validate_and_extract_code(
        "xfinitydigitalhome://auth?code=oi-abcdef123456&state=mystate", "mystate"
    )
    assert code == "oi-abcdef123456"

    # Leading/trailing whitespace trimmed
    code2 = validate_and_extract_code(
        "  xfinitydigitalhome://auth?code=oi-abcdef123456&state=mystate  ", "mystate"
    )
    assert code2 == "oi-abcdef123456"

    # Quoted string
    code3 = validate_and_extract_code(
        '"xfinitydigitalhome://auth?code=oi-abcdef123456&state=mystate"', "mystate"
    )
    assert code3 == "oi-abcdef123456"

    # Trailing slash
    code_slash = validate_and_extract_code(
        "xfinitydigitalhome://auth/?code=oi-abcdef123456&state=mystate", "mystate"
    )
    assert code_slash == "oi-abcdef123456"


def test_validate_and_extract_code_invalid_cases():
    # Empty
    with pytest.raises(XfinityInvalidCallbackError):
        validate_and_extract_code("", "state")

    # Non-string
    with pytest.raises(XfinityInvalidCallbackError):
        validate_and_extract_code(None, "state")  # type: ignore[arg-type]

    # Raw code paste without complete callback URL is rejected
    with pytest.raises(XfinityInvalidCallbackError):
        validate_and_extract_code("oi-abcdef1234567890", "mystate")

    # Query string only without scheme is rejected
    with pytest.raises(XfinityInvalidCallbackError):
        validate_and_extract_code("?code=oi-abcdef123456&state=mystate", "mystate")

    # Bad scheme
    with pytest.raises(XfinityInvalidCallbackError):
        validate_and_extract_code("https://auth?code=oi-123&state=s", "s")

    # Bad host
    with pytest.raises(XfinityInvalidCallbackError):
        validate_and_extract_code(
            "xfinitydigitalhome://notauth?code=oi-123&state=s", "s"
        )

    # Has fragment
    with pytest.raises(XfinityInvalidCallbackError):
        validate_and_extract_code(
            "xfinitydigitalhome://auth#fragment?code=oi-123&state=s", "s"
        )

    # Error parameter
    with pytest.raises(XfinityInvalidCallbackError, match="access_denied"):
        validate_and_extract_code(
            "xfinitydigitalhome://auth?error=access_denied&state=s", "s"
        )

    # State mismatch
    with pytest.raises(XfinityInvalidCallbackError, match="mismatch"):
        validate_and_extract_code(
            "xfinitydigitalhome://auth?code=oi-123&state=wrong", "expected"
        )

    # Missing state
    with pytest.raises(XfinityInvalidCallbackError):
        validate_and_extract_code("xfinitydigitalhome://auth?code=oi-123", "expected")

    # Missing code
    with pytest.raises(XfinityInvalidCallbackError):
        validate_and_extract_code("xfinitydigitalhome://auth?state=s", "s")

    # Bad code prefix
    with pytest.raises(XfinityInvalidCallbackError, match="code missing or malformed"):
        validate_and_extract_code(
            "xfinitydigitalhome://auth?code=badprefix&state=s", "s"
        )


async def test_async_complete_browser_auth():
    auth = BrowserAuthorization.create(timeout_seconds=300)
    callback = f"xfinitydigitalhome://auth?code=oi-1234567890abcdef&state={auth.state}"

    tokens_payload = {
        "access_token": "mock_access",
        "refresh_token": "mock_refresh",
        "id_token": "mock_id",
        "expires_in": 3600,
    }

    mock_session = AsyncMock()
    with patch(
        "custom_components.xfinity.browser_auth._exchange",
        AsyncMock(return_value=tokens_payload),
    ) as mock_exchange:
        result = await async_complete_browser_auth(mock_session, auth, callback)
        assert result == tokens_payload
        mock_exchange.assert_awaited_once_with(
            mock_session, "oi-1234567890abcdef", auth.verifier, auth.activity
        )


async def test_async_complete_browser_auth_rejected():
    auth = BrowserAuthorization.create(timeout_seconds=300)
    callback = f"xfinitydigitalhome://auth?code=oi-1234567890abcdef&state={auth.state}"

    mock_session = AsyncMock()
    with patch(
        "custom_components.xfinity.browser_auth._exchange",
        AsyncMock(side_effect=XfinityLoginRejected("rejected")),
    ):
        with pytest.raises(XfinityLoginRejected):
            await async_complete_browser_auth(mock_session, auth, callback)
