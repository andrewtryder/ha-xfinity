"""Sign in through Browserless and exchange the authorization code."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from urllib.parse import parse_qs, urlencode, urlparse
import uuid

from .const import AUTH_TOKEN_URL, AUTH_URL, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, TOKEN_REDIRECT_URI

BROWSERLESS_URL = "https://production-sfo.browserless.io/stealth/bql"
_LOGIN = """
mutation Login($url: String!, $user: String!, $pass: String!) {
  goto(url: $url, waitUntil: domContentLoaded) { status }
  waitUser: waitForSelector(selector: "#user", timeout: 30000) { time }
  typeUser: type(selector: "#user", text: $user) { time }
  clickUser: click(selector: "#sign_in", visible: true) { time }
  waitPass: waitForSelector(selector: "input[type=password]", timeout: 30000) { time }
  typePass: type(selector: "input[type=password]", text: $pass) { time }
  clickPass: click(selector: "#sign_in", visible: true) { time }
  callback: waitForRequest(url: "*xfinitydigitalhome*", timeout: 45000) { url time }
}
"""


class XfinityLoginError(Exception):
    """Browserless sign-in failed."""


class XfinityLoginRejected(XfinityLoginError):
    """Xfinity did not accept the username or password."""


class XfinityMfaRequired(XfinityLoginError):
    """Xfinity asked for a verification code this integration cannot enter."""


class BrowserlessError(XfinityLoginError):
    """Browserless rejected the key or could not run the browser."""


def pkce_authorize_url() -> tuple[str, str, str, str]:
    """Return the authorize URL, PKCE verifier, activity id, and state."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    activity = str(uuid.uuid4())
    state = secrets.token_urlsafe(24)
    query = urlencode({
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "response_type": "code",
        "prompt": "select_account",
        "state": state,
        "scope": "profile",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "activity_id": activity,
        "active_x1_account_count": "true",
        "rm_hint": "true",
        "partner_id": "comcast",
        "mso_partner_hint": "true",
    })
    return f"{AUTH_URL}?{query}", verifier, activity, state


def code_from_callback(url: str, state: str) -> str:
    """Return the authorization code when the callback matches this attempt."""
    parsed = urlparse(url)
    if (parsed.scheme, parsed.netloc) != ("xfinitydigitalhome", "auth"):
        raise XfinityLoginError("callback rejected")
    params = parse_qs(parsed.query)
    if params.get("state") != [state] or not params.get("code"):
        raise XfinityLoginError("callback rejected")
    code = params["code"][0]
    if not code.startswith("oi-"):
        raise XfinityLoginError("callback rejected")
    return code


def _failed_step(errors: list, data: dict) -> XfinityLoginError:
    reached_password = bool(data.get("typePass"))
    if reached_password:
        return XfinityMfaRequired("verification required")
    if data.get("waitUser") and not data.get("waitPass"):
        return XfinityLoginRejected("sign-in rejected")
    return BrowserlessError("browserless login did not finish")


async def async_browserless_login(session, username: str, password: str, api_key: str) -> dict:
    """Sign in with Browserless and return the token response. Raises on failure."""
    import aiohttp

    authorize_url, verifier, activity, state = pkce_authorize_url()
    try:
        async with session.post(
            BROWSERLESS_URL,
            params={
                "token": api_key,
                "proxy": "residential",
                "proxyCountry": "us",
                "humanlike": "true",
                "timeout": "110000",
            },
            json={
                "query": _LOGIN,
                "operationName": "Login",
                "variables": {"url": authorize_url, "user": username, "pass": password},
            },
            timeout=aiohttp.ClientTimeout(total=120),
        ) as response:
            if response.status == 401:
                raise BrowserlessError("browserless rejected the api key")
            if response.status == 429:
                raise BrowserlessError("browserless rate limited")
            if response.status >= 400:
                raise BrowserlessError(f"browserless returned HTTP {response.status}")
            payload = await response.json(content_type=None)
    except aiohttp.ClientError as err:
        raise BrowserlessError("browserless request failed") from err
    except TimeoutError as err:
        raise BrowserlessError("browserless request failed") from err
    if not isinstance(payload, dict):
        raise BrowserlessError("browserless response was not an object")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    callback = ((data.get("callback") or {}) if isinstance(data.get("callback"), dict) else {}).get("url")
    if payload.get("errors") or not isinstance(callback, str):
        raise _failed_step(payload.get("errors") or [], data) from None
    code = code_from_callback(callback, state)
    return await _exchange(session, code, verifier, activity)


async def _exchange(session, code: str, verifier: str, activity: str) -> dict:
    import aiohttp

    try:
        async with session.post(
            AUTH_TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "active_x1_account_count": "true",
                "partner_id": "comcast",
                "mso_partner_hint": "true",
                "scope": "profile",
                "rm_hint": "true",
                "grant_type": "authorization_code",
                "code": code,
                "activity_id": activity,
                "redirect_uri": TOKEN_REDIRECT_URI,
                "code_verifier": verifier,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; SM-G991B)",
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            if response.status in (400, 401):
                raise XfinityLoginRejected("token exchange rejected")
            if response.status < 200 or response.status >= 300:
                raise BrowserlessError(f"token endpoint returned HTTP {response.status}")
            payload = await response.json(content_type=None)
    except aiohttp.ClientError as err:
        raise BrowserlessError("token exchange failed") from err
    except TimeoutError as err:
        raise BrowserlessError("token exchange failed") from err
    if not isinstance(payload, dict) or not payload.get("refresh_token") or not payload.get("access_token"):
        raise XfinityLoginRejected("token exchange rejected")
    return payload
