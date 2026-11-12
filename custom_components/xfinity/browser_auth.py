"""Browser-based OAuth PKCE authorization for Xfinity without headless browsers."""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import time
from urllib.parse import parse_qs, urlparse

from aiohttp import ClientSession

from .const import REDIRECT_URI
from .login import (
    XfinityBrowserSessionExpiredError,
    XfinityInvalidCallbackError,
    _exchange,
    pkce_authorize_url,
)

DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS = 600.0


@dataclass(slots=True)
class BrowserAuthorization:
    """Manages an active browser-handoff OAuth authorization session."""

    url: str
    verifier: str
    activity: str
    state: str
    expires_at: float
    consumed: bool = False

    @classmethod
    def create(
        cls, timeout_seconds: float = DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS
    ) -> BrowserAuthorization:
        """Create a new PKCE authorization URL and session tracker."""
        url, verifier, activity, state = pkce_authorize_url()
        return cls(
            url=url,
            verifier=verifier,
            activity=activity,
            state=state,
            expires_at=time.time() + timeout_seconds,
        )

    def consume(self, callback_url: str) -> tuple[str, str, str]:
        """Validate and consume the callback URL, returning code, verifier, activity."""
        if self.consumed or time.time() >= self.expires_at:
            raise XfinityBrowserSessionExpiredError("Browser sign-in session expired")

        code = validate_and_extract_code(callback_url, self.state)
        self.consumed = True
        return code, self.verifier, self.activity


def validate_and_extract_code(callback_input: str, expected_state: str) -> str:
    """Validate and extract the authorization code from the complete callback URL."""
    if not isinstance(callback_input, str) or not callback_input.strip():
        raise XfinityInvalidCallbackError("Callback URL is empty")

    raw = callback_input.strip().strip('"').strip("'").strip()
    parsed = urlparse(raw)
    expected = urlparse(REDIRECT_URI)

    if parsed.scheme != expected.scheme:
        raise XfinityInvalidCallbackError(
            f"Callback URL must begin with {expected.scheme}://"
        )

    is_valid_location = parsed.netloc == expected.netloc or parsed.path in (
        f"//{expected.netloc}",
        f"//{expected.netloc}/",
        f"/{expected.netloc}",
    )
    if not is_valid_location or parsed.fragment:
        raise XfinityInvalidCallbackError("Callback URL destination is invalid")

    params = parse_qs(parsed.query)

    if "error" in params:
        error_desc = params.get("error_description", params["error"])
        raise XfinityInvalidCallbackError(f"OAuth error: {error_desc[0]}")

    states = params.get("state", [])
    if len(states) != 1 or not hmac.compare_digest(states[0], expected_state):
        raise XfinityInvalidCallbackError("OAuth state mismatch or missing")

    codes = params.get("code", [])
    if len(codes) != 1 or not codes[0].startswith("oi-"):
        raise XfinityInvalidCallbackError(
            "OAuth authorization code missing or malformed"
        )

    return codes[0]


async def async_complete_browser_auth(
    session: ClientSession,
    browser_auth: BrowserAuthorization,
    callback_url: str,
) -> dict:
    """Exchange authorization code from browser callback for tokens."""
    code, verifier, activity = browser_auth.consume(callback_url)
    return await _exchange(session, code, verifier, activity)
