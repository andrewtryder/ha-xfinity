"""Tests for direct HTTP authentication session."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.xfinity.direct_auth import (
    HttpPage,
    XfinityDirectLoginSession,
    XfinityFormParser,
    parse_login_page,
    raise_for_challenge,
)
from custom_components.xfinity.login import (
    XfinityBotChallengeError,
    XfinityLoginError,
    XfinityLoginRejected,
    XfinityMfaRequired,
)


def test_form_parser_standard_inputs():
    html = """
    <html>
      <body>
        <form action="/login" method="post">
          <input type="hidden" name="flowStateId" value="state-token-123">
          <input type="hidden" name="flowStep" value="username">
          <input type="hidden" name="reqId" value="req-abc">
          <input type="text" name="user" value="">
        </form>
      </body>
    </html>
    """
    form = parse_login_page(html, "https://login.xfinity.com")
    assert form.action == "https://login.xfinity.com/login"
    assert form.fields["flowStateId"] == "state-token-123"
    assert form.fields["flowStep"] == "username"
    assert form.user_field == "user"
    assert form.password_field is None
    assert form.flow_step == "username"


def test_form_parser_prism_components():
    html = """
    <html>
      <body>
        <form action="https://login.xfinity.com/login" method="post">
          <input type="hidden" name="flowStateId" value="state-token-456">
          <prism-input-text prism-id="user" name="user" label="Xfinity ID"></prism-input-text>
          <prism-input-text prism-id="passwd" name="passwd" type="password"></prism-input-text>
        </form>
      </body>
    </html>
    """
    parser = XfinityFormParser("https://login.xfinity.com")
    parser.feed(html)
    form = parser.get_form()
    assert form.fields["flowStateId"] == "state-token-456"
    assert form.user_field == "user"
    assert form.password_field == "passwd"


def test_raise_for_challenge():
    # Normal page does not raise
    normal = HttpPage(
        url="https://login.xfinity.com/login",
        status=200,
        headers={"Content-Type": "text/html"},
        body="<html><body>Welcome</body></html>",
    )
    raise_for_challenge(normal)

    # Akamai 403 block
    akamai_page = HttpPage(
        url="https://login.xfinity.com/login",
        status=403,
        headers={"Server": "AkamaiGHost"},
        body="<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD></HTML>",
    )
    with pytest.raises(XfinityBotChallengeError, match="Akamai Bot Manager"):
        raise_for_challenge(akamai_page)

    # Edgesuite error reference
    edgesuite_page = HttpPage(
        url="https://login.xfinity.com/login",
        status=403,
        headers={},
        body="Reference #18.12345 https://errors.edgesuite.net/18.12345",
    )
    with pytest.raises(XfinityBotChallengeError, match="Akamai Bot Manager"):
        raise_for_challenge(edgesuite_page)

    # Cloudflare challenge
    cf_page = HttpPage(
        url="https://login.xfinity.com/login",
        status=403,
        headers={},
        body="<html><title>Just a moment...</title>Verify you are human</html>",
    )
    with pytest.raises(XfinityBotChallengeError, match="anti-bot challenge"):
        raise_for_challenge(cf_page)


class _MockResponse:
    def __init__(self, status: int, headers: dict[str, str], body: str, url: str):
        self.status = status
        self.headers = headers
        self._body = body
        self.url = url

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _MockSession:
    def __init__(self, responses: list[_MockResponse]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self._responses:
            raise RuntimeError("Ran out of mocked responses")
        return self._responses.pop(0)


async def test_direct_auth_success():
    login_html = """
    <form action="/login" method="post">
      <input type="hidden" name="flowStateId" value="tok1">
      <input type="hidden" name="flowStep" value="username">
      <input type="text" name="user" value="">
    </form>
    """
    password_html = """
    <form action="/login" method="post">
      <input type="hidden" name="flowStateId" value="tok2">
      <input type="hidden" name="flowStep" value="password">
      <input type="password" name="passwd" value="">
    </form>
    """

    session = _MockSession(
        [
            # Step 1: GET authorize URL -> 302 to oauth.xfinity.com
            _MockResponse(
                302,
                {"Location": "https://oauth.xfinity.com/oauth/authorize"},
                "",
                "https://xerxes-sub.xerxessecure.com/xerxes-ctrl/oauth/authorize",
            ),
            # Follow 1 -> 302 to login.xfinity.com/login
            _MockResponse(
                302,
                {"Location": "https://login.xfinity.com/login"},
                "",
                "https://oauth.xfinity.com/oauth/authorize",
            ),
            # Follow 2 -> 200 with login form
            _MockResponse(
                200,
                {"Content-Type": "text/html"},
                login_html,
                "https://login.xfinity.com/login",
            ),
            # Step 2: POST username -> 200 with password form
            _MockResponse(
                200,
                {"Content-Type": "text/html"},
                password_html,
                "https://login.xfinity.com/login",
            ),
            # Step 3: POST password -> 302 to callback URL
            _MockResponse(
                302,
                {
                    "Location": "xfinitydigitalhome://auth?code=oi-testauthcode12345&state=DYNAMIC"
                },
                "",
                "https://login.xfinity.com/login",
            ),
        ]
    )

    direct_session = XfinityDirectLoginSession(session)  # type: ignore[arg-type]

    tokens_payload = {
        "access_token": "acc",
        "refresh_token": "ref",
        "id_token": "id",
        "expires_in": 3600,
    }

    with patch(
        "custom_components.xfinity.direct_auth._exchange",
        AsyncMock(return_value=tokens_payload),
    ) as mock_exchange:
        # Patch the state to match the static mock location state
        with patch(
            "custom_components.xfinity.direct_auth.pkce_authorize_url",
            return_value=(
                "https://xerxes-sub.xerxessecure.com/xerxes-ctrl/oauth/authorize?state=DYNAMIC",
                "verifier123",
                "activity123",
                "DYNAMIC",
            ),
        ):
            tokens = await direct_session.async_start("myuser", "mypass")
            assert tokens == tokens_payload
            mock_exchange.assert_awaited_once_with(
                session, "oi-testauthcode12345", "verifier123", "activity123"
            )


async def test_direct_auth_blocked_by_akamai():
    login_html = """
    <form action="/login" method="post">
      <input type="hidden" name="flowStateId" value="tok1">
      <input type="text" name="user" value="">
    </form>
    """
    session = _MockSession(
        [
            # GET authorize -> 200 with login form
            _MockResponse(
                200,
                {"Content-Type": "text/html"},
                login_html,
                "https://login.xfinity.com/login",
            ),
            # POST username -> 403 Akamai Block
            _MockResponse(
                403,
                {"Server": "AkamaiGHost"},
                "<HTML><TITLE>Access Denied</TITLE></HTML>",
                "https://login.xfinity.com/login",
            ),
        ]
    )
    direct_session = XfinityDirectLoginSession(session)  # type: ignore[arg-type]

    with pytest.raises(XfinityBotChallengeError, match="Akamai Bot Manager"):
        await direct_session.async_start("myuser", "mypass")


async def test_direct_auth_invalid_credentials():
    login_html = """
    <form action="/login" method="post">
      <input type="hidden" name="flowStateId" value="tok1">
      <input type="text" name="user" value="">
    </form>
    """
    error_html = """
    <html><body>The Xfinity ID or password you entered was incorrect. Please try again.</body></html>
    """
    session = _MockSession(
        [
            _MockResponse(
                200,
                {"Content-Type": "text/html"},
                login_html,
                "https://login.xfinity.com/login",
            ),
            _MockResponse(
                200,
                {"Content-Type": "text/html"},
                error_html,
                "https://login.xfinity.com/login",
            ),
        ]
    )
    direct_session = XfinityDirectLoginSession(session)  # type: ignore[arg-type]
    with pytest.raises(XfinityLoginRejected, match="Invalid username or password"):
        await direct_session.async_start("myuser", "wrongpass")


async def test_direct_auth_mfa_required():
    login_html = """
    <form action="/login" method="post">
      <input type="hidden" name="flowStateId" value="tok1">
      <input type="text" name="user" value="">
    </form>
    """
    mfa_html = """
    <html><body><h2>Two-step verification</h2><p>Enter the 6-digit code sent to your phone</p></body></html>
    """
    session = _MockSession(
        [
            _MockResponse(
                200,
                {"Content-Type": "text/html"},
                login_html,
                "https://login.xfinity.com/login",
            ),
            _MockResponse(
                200,
                {"Content-Type": "text/html"},
                mfa_html,
                "https://login.xfinity.com/login",
            ),
        ]
    )
    direct_session = XfinityDirectLoginSession(session)  # type: ignore[arg-type]
    with pytest.raises(XfinityMfaRequired, match="Two-step verification"):
        await direct_session.async_start("myuser", "mypass")


async def test_direct_auth_too_many_redirects():
    infinite_redirects = [
        _MockResponse(
            302,
            {"Location": f"https://login.xfinity.com/step{i}"},
            "",
            f"https://login.xfinity.com/step{i - 1}",
        )
        for i in range(15)
    ]
    session = _MockSession(infinite_redirects)
    direct_session = XfinityDirectLoginSession(session)  # type: ignore[arg-type]
    with pytest.raises(XfinityLoginError, match="Too many redirects"):
        await direct_session.async_start("myuser", "mypass")
