"""Direct HTTP authentication session for Xfinity OAuth using aiohttp.

Attempts direct HTTP login following the PKCE authorization flow, parsing login
forms, preserving hidden state fields (flowStateId, flowId, etc.), and detecting
Akamai Bot Manager or other challenge blocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final, Mapping
from urllib.parse import urljoin, urlparse

from aiohttp import ClientSession, ClientTimeout

from .const import REDIRECT_URI
from .login import (
    XfinityBotChallengeError,
    XfinityLoginError,
    XfinityLoginRejected,
    XfinityMfaRequired,
    _exchange,
    code_from_callback,
    pkce_authorize_url,
)

BROWSER_USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MAX_REDIRECT_HOPS: Final = 12


@dataclass(frozen=True, slots=True)
class HttpPage:
    """Represents an HTTP response in the sign-in flow."""

    url: str
    status: int
    headers: Mapping[str, str]
    body: str


@dataclass(frozen=True, slots=True)
class ParsedLoginForm:
    """Represents a parsed Xfinity sign-in form."""

    action: str
    fields: dict[str, str]
    user_field: str | None
    password_field: str | None
    flow_step: str | None


class XfinityFormParser(HTMLParser):
    """HTML parser to extract hidden fields, CSRF tokens, and credentials fields."""

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.action: str | None = None
        self.fields: dict[str, str] = {}
        self.user_field: str | None = None
        self.password_field: str | None = None
        self.flow_step: str | None = None
        self._in_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        tag_lower = tag.lower()

        if tag_lower == "form" and not self._in_form:
            self._in_form = True
            raw_action = attributes.get("action") or ""
            self.action = urljoin(self.page_url, raw_action)

        elif self._in_form:
            name = attributes.get("name") or attributes.get("prism-id")
            val = attributes.get("value") or ""

            if tag_lower == "input":
                field_type = (attributes.get("type") or "text").casefold()
                if field_type == "hidden" and name:
                    self.fields[name] = val
                    if name == "flowStep":
                        self.flow_step = val
                elif field_type in ("text", "email") or name in ("user", "username"):
                    if not self.user_field and name:
                        self.user_field = name
                elif field_type == "password" or name in ("passwd", "password"):
                    if not self.password_field and name:
                        self.password_field = name

            elif "prism-input" in tag_lower:
                # Comcast Prism UI custom web components
                if name in ("user", "username") or attributes.get("prism-id") == "user":
                    self.user_field = name or "user"
                elif (
                    name in ("passwd", "password")
                    or attributes.get("prism-id") == "passwd"
                    or attributes.get("type") == "password"
                ):
                    self.password_field = name or "passwd"

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form":
            self._in_form = False

    def get_form(self) -> ParsedLoginForm:
        return ParsedLoginForm(
            action=self.action or self.page_url,
            fields=self.fields,
            user_field=self.user_field,
            password_field=self.password_field,
            flow_step=self.flow_step or self.fields.get("flowStep"),
        )


def parse_login_page(body: str, url: str) -> ParsedLoginForm:
    """Parse the login page body for forms and tokens."""
    parser = XfinityFormParser(url)
    parser.feed(body)
    parser.close()
    return parser.get_form()


def raise_for_challenge(page: HttpPage) -> None:
    """Raise XfinityBotChallengeError if Akamai Bot Manager or Cloudflare challenges the request."""
    server = page.headers.get("Server", "")
    content = page.body.lower()

    if page.status == 403 and (
        "akamaighost" in server.lower()
        or "access denied" in content
        or "errors.edgesuite.net" in content
    ):
        raise XfinityBotChallengeError(
            f"Direct sign-in was blocked by Akamai Bot Manager (HTTP 403 on {urlparse(page.url).netloc})"
        )

    if any(
        marker in content
        for marker in (
            "just a moment...",
            "verify you are human",
            "attention required! | cloudflare",
        )
    ):
        raise XfinityBotChallengeError(
            "Direct sign-in was blocked by an interactive anti-bot challenge"
        )


class XfinityDirectLoginSession:
    """Manages an automated direct HTTP sign-in attempt."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._verifier: str | None = None
        self._activity: str | None = None
        self._state: str | None = None
        self._authorize_url: str | None = None

    async def async_start(self, username: str, password: str) -> dict:
        """Run the direct HTTP sign-in flow. Returns tokens or raises an error."""
        (
            self._authorize_url,
            self._verifier,
            self._activity,
            self._state,
        ) = pkce_authorize_url()

        # Step 1: Request authorize URL
        page = await self._request_page("GET", self._authorize_url)
        code, page = await self._follow_redirects(page)
        if code is not None:
            return await self._exchange_code(code)

        raise_for_challenge(page)

        # Step 2: Parse and submit username
        form = parse_login_page(page.body, page.url)
        post_data = dict(form.fields)
        user_key = form.user_field or "user"
        post_data[user_key] = username

        headers = _browser_headers(referer=page.url, is_post=True)
        submitted_user = await self._request_page(
            "POST", form.action, data=post_data, headers=headers
        )
        code, result = await self._follow_redirects(submitted_user)
        if code is not None:
            return await self._exchange_code(code)

        raise_for_challenge(result)

        # Check for rejection or MFA
        if (
            "the xfinity id or password you entered was incorrect"
            in result.body.lower()
        ):
            raise XfinityLoginRejected("Invalid username or password")

        if (
            "two-step verification" in result.body.lower()
            or "enter the 6-digit code" in result.body.lower()
        ):
            raise XfinityMfaRequired("Two-step verification code required")

        # Step 3: Parse and submit password
        pwd_form = parse_login_page(result.body, result.url)
        pwd_post_data = dict(pwd_form.fields)
        pwd_key = pwd_form.password_field or "passwd"
        pwd_post_data[pwd_key] = password

        pwd_headers = _browser_headers(referer=result.url, is_post=True)
        submitted_pwd = await self._request_page(
            "POST", pwd_form.action, data=pwd_post_data, headers=pwd_headers
        )
        code, final_page = await self._follow_redirects(submitted_pwd)
        if code is not None:
            return await self._exchange_code(code)

        raise_for_challenge(final_page)

        if (
            "the xfinity id or password you entered was incorrect"
            in final_page.body.lower()
        ):
            raise XfinityLoginRejected("Invalid username or password")

        if (
            "two-step verification" in final_page.body.lower()
            or "enter the 6-digit code" in final_page.body.lower()
        ):
            raise XfinityMfaRequired("Two-step verification code required")

        raise XfinityLoginError("Direct authentication did not reach completion")

    async def _follow_redirects(self, page: HttpPage) -> tuple[str | None, HttpPage]:
        current = page
        for _ in range(MAX_REDIRECT_HOPS):
            loc = current.headers.get("Location")
            if not loc:
                return None, current
            target = urljoin(current.url, loc)
            if target.startswith(REDIRECT_URI):
                if self._state is None:
                    raise XfinityLoginError("OAuth state missing")
                code = code_from_callback(target, self._state)
                return code, current
            current = await self._request_page(
                "GET", target, headers=_browser_headers(referer=current.url)
            )
        raise XfinityLoginError("Too many redirects during direct authentication")

    async def _request_page(
        self,
        method: str,
        url: str,
        *,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpPage:
        req_headers = headers or _browser_headers()
        async with self._session.request(
            method,
            url,
            data=data,
            headers=req_headers,
            allow_redirects=False,
            timeout=ClientTimeout(total=25),
        ) as resp:
            body = await resp.text()
            resp_headers = {k: v for k, v in resp.headers.items()}
            return HttpPage(
                url=str(resp.url),
                status=resp.status,
                headers=resp_headers,
                body=body,
            )

    async def _exchange_code(self, code: str) -> dict:
        if self._verifier is None or self._activity is None:
            raise XfinityLoginError("PKCE verifier or activity ID missing")
        return await _exchange(self._session, code, self._verifier, self._activity)


def _browser_headers(
    *, referer: str | None = None, is_post: bool = False
) -> dict[str, str]:
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-site" if referer else "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        headers["Referer"] = referer
    if is_post:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Sec-Fetch-Site"] = "same-origin"
    return headers
