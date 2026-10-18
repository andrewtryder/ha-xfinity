#!/usr/bin/env python3
"""Unofficial Xfinity API command-line probe.

Requires: pip install requests

Run:
    python xfinity_api_probe.py
    python xfinity_api_probe.py --outage-only
    python xfinity_api_probe.py --usage-only
    python xfinity_api_probe.py --manual-login

Based on publicly observed, undocumented endpoints. No live API guarantees.
Never share token files, authorization codes, or unredacted output publicly.
"""

from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import math
import tempfile
import warnings
from getpass import GetPassWarning
from getpass import getpass
from html.parser import HTMLParser
from http.cookiejar import MozillaCookieJar
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse, urljoin
import uuid
import webbrowser

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run: python -m pip install requests")

# ======================== EDIT THESE SETTINGS ==============================
# Plain street address, including city/state/ZIP. Do not URL-encode it.
SERVICE_ADDRESS = os.getenv("XFINITY_SERVICE_ADDRESS", "21 Jeannes Way, Sandown, NH 03873")

# Optional: Obtain a valid Android OAuth client secret from an authorized
# integration/client configuration. This is NOT your Xfinity account password.
# Xfinity's undocumented token endpoint may reject exchange without it.
ANDROID_OAUTH_CLIENT_SECRET = os.getenv(
    "XFINITY_ANDROID_APPLICATION_CLIENT_SECRET",
    "f40e1d73c8ed4b4c82e2fd48ebc23f7a",
)

# Optional: use CURRENT tokens from an already authenticated session to test
# account usage without going through the OAuth flow below. These are sensitive!
ACCOUNT_ACCESS_TOKEN = os.getenv("XFINITY_ACCESS_TOKEN", "")
ACCOUNT_ID_TOKEN = os.getenv("XFINITY_ID_TOKEN", "")

# Optional: set to False to avoid writing tokens to disk. Cached tokens are
# written with owner-only permissions (0600) where supported.
CACHE_TOKENS = True
TOKEN_FILE = Path.home() / ".config" / "xfinity_api_probe" / "oauth_token.json"
# ============================================================================

OUTAGE_SESSION_URL = "https://api.sc.xfinity.com/outagedata/session"
OUTAGE_STATUS_URL = "https://api.sc.xfinity.com/outage/consolidated/lob/v3"
AUTH_URL = "https://xerxes-sub.xerxessecure.com/xerxes-ctrl/oauth/authorize"
AUTH_TOKEN_URL = "https://xerxes-sub.xerxessecure.com/xerxes-ctrl/oauth/token"
GRAPHQL_URL = "https://gw.api.dh.comcast.com/galileo/graphql"
CLIENT_ID = "xfinity-android-application"
REDIRECT_URI = "xfinitydigitalhome://auth"
# Xfinity's reference Android client sends a URL-encoded redirect_uri literal
# in the token request; preserve this only in that request.
TOKEN_REDIRECT_URI = "xfinitydigitalhome%3A%2F%2Fauth"

COMMON_WEB_HEADERS = {
    "Origin": "https://www.xfinity.com",
    "Referer": "https://www.xfinity.com/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
}
MOBILE_HEADERS = {
    "User-Agent": "Digital Home / Samsung SM-G991B / Android 14",
    "client": "digital-home-android",
    "client-detail": "MOBILE;Samsung;SM-G991B;Android 14;v5.38.0",
    "accept-language": "en-US",
    "Content-Type": "application/json",
    "Accept": "multipart/mixed; deferSpec=20220824, application/json",
    "x-apollo-operation-id": "61994c6016ac8c0ebcca875084919e5e01cb3b116a86aaf9646e597c3a1fbd06",
    "x-apollo-operation-name": "InternetDataUsage",
}
USAGE_QUERY = """query InternetDataUsage {
  accountByServiceAccountId {
    internet {
      usage {
        monthlyUsage {
          policy
          startDate
          endDate
          daysRemaining
          currentUsage { value unit }
          allowableUsage { value unit }
        }
      }
    }
  }
}"""


class ProbeError(Exception):
    pass


def request_retry(method: str, url: str, *, session=None, retry=True, **kwargs) -> requests.Response:
    """Retries only transient network/HTTP failures, never redirects to credentials."""
    session = session or requests.Session()
    attempts = 3 if retry else 1
    for attempt in range(attempts):
        try:
            response = session.request(
                method, url, timeout=20, allow_redirects=False, verify=True, **kwargs
            )
        except requests.RequestException as exc:
            if attempt == attempts - 1:
                raise ProbeError(f"Network request failed: {type(exc).__name__}") from None
        else:
            if response.status_code not in (429, 500, 502, 503, 504):
                return response
            if attempt == attempts - 1:
                return response
        time.sleep((2 ** attempt) + secrets.randbelow(500) / 1000)
    raise ProbeError("All network attempts failed")


def checked_json(response: requests.Response, endpoint: str) -> dict:
    if not 200 <= response.status_code < 300:
        # Intentionally do not print bodies, which can contain account data.
        raise ProbeError(f"{endpoint} returned HTTP {response.status_code}")
    try:
        result = response.json()
    except ValueError:
        raise ProbeError(f"{endpoint} did not return JSON") from None
    if not isinstance(result, dict):
        raise ProbeError(f"{endpoint} returned an unexpected JSON structure")
    return result


def as_iso(value):
    if isinstance(value, dict):
        return value.get("iso")
    return value if isinstance(value, str) else None


def iso_to_datetime(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def outage_summary(data: dict, now=None) -> dict:
    if "outageInfoResponseList" not in data:
        return {"status": "unknown", "note": "No outageInfoResponseList key"}
    outages = data.get("outageInfoResponseList")
    if not isinstance(outages, list):
        return {"status": "unknown", "note": "Outage list is not a list"}
    if not outages:
        return {"status": "no_reported_incident", "active_outage": False, "incidents": []}
    message = data.get("customerMessage")
    if not isinstance(message, dict):
        return {"status": "unknown", "incident_count": len(outages)}
    now = now or datetime.now(timezone.utc)
    summaries = []
    for index, outage in enumerate(outages):
        if not isinstance(outage, dict):
            summaries.append({"classification": "unknown"})
            continue
        # The API's customerMessage is a response-level object; individual
        # records may not have their own associated customer message.
        planned = message.get("planned")
        start = as_iso(outage.get("startTime"))
        start_dt = iso_to_datetime(start)
        upcoming = bool(start_dt and start_dt.tzinfo and start_dt > now)
        if planned is True and upcoming:
            classification = "scheduled_maintenance"
        elif planned is True and start_dt and start_dt.tzinfo:
            classification = "planned_maintenance_underway"
        elif planned is False:
            classification = "unplanned_outage"
        else:
            classification = "unknown"
        details = message.get("messages")
        summaries.append({
            "index": index,
            "classification": classification,
            "outage_id": outage.get("outageId"),
            "planned": planned,
            "outage_type": message.get("outageType"),
            "affected_services": outage.get("services"),
            "start": start,
            "estimated_restoration": as_iso(message.get("etr")),
            "estimated_restoration_display": message.get("etrOutputString"),
            "summary": details.get("shortDescription") if isinstance(details, dict) else None,
            "customer_message": message.get("customerFacingText"),
        })
    return {"status": "unknown" if any(i["classification"] == "unknown" for i in summaries) else "incidents_reported",
            "active_outage": any(i["classification"] in ("planned_maintenance_underway", "unplanned_outage") for i in summaries),
            "incident_count": len(outages),
            "incidents": summaries}


def fetch_outage(address: str) -> dict:
    http = requests.Session()
    session = request_retry("POST", OUTAGE_SESSION_URL, session=http,
                            headers={**COMMON_WEB_HEADERS, "Content-Length": "0"},
                            data=b"")
    if session.status_code != 201:
        raise ProbeError(f"Outage session endpoint returned HTTP {session.status_code} (expected 201)")
    token = session.headers.get("X-OutageDataService-Token")
    if not token:
        raise ProbeError("Session response missing X-OutageDataService-Token header")
    response = request_retry("GET", OUTAGE_STATUS_URL, session=http,
                             params={"includeIdealOutage": "true",
                                     "tokenizeDateTime": "true", "address": address},
                             headers={**COMMON_WEB_HEADERS,
                                      "Authorization": f"Bearer {token}"})
    return outage_summary(checked_json(response, "Outage status API"))


def pkce_params():
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    activity = str(uuid.uuid4())
    state = secrets.token_urlsafe(24)
    query = {
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
    }
    return AUTH_URL + "?" + urlencode(query), verifier, activity, state


def _code_from_url(url: str, state: str):
    try:
        parsed = urlparse(url)
        if (parsed.scheme, parsed.netloc, parsed.path, parsed.fragment) != ("xfinitydigitalhome", "auth", "", ""):
            raise ValueError
        params = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(values) != 1 for values in params.values()):
            raise ValueError
        if params.get("state") != [state] or "error" in params:
            raise ValueError
        code = params.get("code", [""])[0]
        if not re.fullmatch(r"oi-[a-f0-9]{32}", code, re.IGNORECASE):
            raise ValueError
        return code
    except ValueError:
        raise ProbeError("Invalid OAuth callback: expected final redirect, code, and matching state") from None


def prompt_secret(label):
    if not sys.stdin.isatty():
        raise ProbeError("Interactive login required; rerun in a terminal with --login-method manual")
    # getpass can otherwise fall back to echoed input if terminal control fails.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            return getpass(label, stream=sys.stderr)
    except GetPassWarning:
        raise ProbeError("Secure terminal input unavailable") from None


def manual_login(url, state):
    if not sys.stdin.isatty():
        raise ProbeError("Manual login requires an interactive terminal")
    print("Open DevTools > Network, enable Preserve log, then visit this URL:\n" + url, file=sys.stderr)
    print("After login and MFA, copy the full final Location header or failed request's Request URL "
          "beginning xfinitydigitalhome://auth (including code and state). "
          "Clear Network filters first. Do not copy an intermediate code.", file=sys.stderr)
    webbrowser.open(url)
    return _code_from_url(prompt_secret("Final callback URL (hidden): ").strip(), state)


# Embedded to keep the probe a single Python file. Node's built-in WebSocket is
# used only for optional Chrome capture; HTTP/manual modes do not require Node.
CHROME_CAPTURE_JS = r"""
const fs = require('node:fs');
let socket;
let finished = false;
function finish(status, callback) {
  if (finished) return;
  finished = true;
  // stdout is a private pipe to Python; never print callback/headers to stderr.
  if (socket?.readyState === 1) {
    socket.send(JSON.stringify({id: 999999, method: 'Browser.close'}));
  }
  if (callback) process.stdout.write(JSON.stringify({callback}), () => process.exit(status));
  else process.exit(status);
}
process.on('uncaughtException', () => finish(1));
process.on('unhandledRejection', () => finish(1));
async function main() {
  if (typeof WebSocket !== 'function') return finish(4);
  const config = JSON.parse(fs.readFileSync(0, 'utf8'));
  setTimeout(() => finish(2), config.timeout * 1000);
  const base = new URL(config.base);
  let target;
  for (let attempt = 0; attempt < 50 && !target; attempt++) {
    const response = await fetch(base + 'json/list', {signal: AbortSignal.timeout(3000)});
    if (!response.ok) return finish(1);
    target = (await response.json()).find(t => t.type === 'page');
    if (!target) await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!target) return finish(1);
  const endpoint = new URL(target.webSocketDebuggerUrl);
  if (endpoint.protocol !== 'ws:' || endpoint.hostname !== base.hostname || endpoint.port !== base.port) {
    return finish(1);
  }
  socket = new WebSocket(endpoint);
  let sequence = 0;
  const pending = new Map();
  function send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      pending.set(id, {resolve, reject});
      socket.send(JSON.stringify({id, method, params}));
    });
  }
  function inspect(candidate) {
    if (typeof candidate !== 'string') return;
    let url;
    try { url = new URL(candidate); } catch { return; }
    if (url.protocol === 'xfinitydigitalhome:' && url.host === 'auth') finish(0, candidate);
  }
  function headers(values) {
    for (const [name, value] of Object.entries(values || {})) {
      if (name.toLowerCase() === 'location') inspect(value);
    }
  }
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (pending.has(message.id)) {
      const waiter = pending.get(message.id);
      pending.delete(message.id);
      message.error ? waiter.reject(new Error('CDP command failed')) : waiter.resolve(message.result);
    }
    const params = message.params || {};
    if (message.method === 'Network.requestWillBeSent') {
      inspect(params.request?.url);
      headers(params.redirectResponse?.headers);
    }
    if (message.method === 'Network.responseReceived') headers(params.response?.headers);
    if (message.method === 'Network.responseReceivedExtraInfo') headers(params.headers);
    if (['Page.frameRequestedNavigation', 'Page.frameScheduledNavigation',
         'Page.frameStartedNavigating'].includes(message.method)) inspect(params.url);
  });
  socket.addEventListener('error', () => finish(1));
  socket.addEventListener('close', () => finish(3));
  socket.addEventListener('open', async () => {
    await send('Network.enable');
    await send('Page.enable');
    await send('Page.navigate', {url: config.url});
  });
}
main().catch(() => finish(1));
"""


def chrome_executable():
    configured = os.getenv("XFINITY_CHROME_PATH")
    candidates = [configured] if configured else [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"), shutil.which("chromium"),
        shutil.which("chromium-browser"),
        str(Path(os.getenv("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.getenv("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise ProbeError("Chrome not found; set XFINITY_CHROME_PATH or use --login-method manual")


def chrome_login(url, state, timeout=600):
    """Observe user-driven login in an isolated Chrome; never fill credentials."""
    node = shutil.which("node")
    if not node:
        raise ProbeError("Chrome capture requires Node.js 22+; use --login-method manual instead")
    chrome = chrome_executable()
    browser = None
    try:
        # No normal browser profile is opened or inspected. TemporaryDirectory
        # creates a private directory, removed after the owned browser exits.
        with tempfile.TemporaryDirectory(prefix="xfinity-probe-chrome-") as directory:
            try:
                browser = subprocess.Popen([
                    chrome, "--user-data-dir=" + directory,
                    "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                    "--no-first-run", "--no-default-browser-check", "--new-window", "about:blank",
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                port_file = Path(directory) / "DevToolsActivePort"
                deadline = time.monotonic() + 15
                while not port_file.is_file():
                    if browser.poll() is not None or time.monotonic() > deadline:
                        raise ProbeError("Chrome debugging interface unavailable; use --login-method manual")
                    time.sleep(0.1)
                port = int(port_file.read_text().splitlines()[0])
                if not 0 < port < 65536:
                    raise ProbeError("Chrome returned an invalid debugging port")
                print("Sign in and complete MFA in the new Chrome window. Waiting up to "
                      f"{timeout // 60} minutes for the final callback; the window closes after capture.",
                      file=sys.stderr)
                result = subprocess.run([node, "-e", CHROME_CAPTURE_JS],
                                        input=json.dumps({"base": f"http://127.0.0.1:{port}/",
                                                          "url": url, "timeout": timeout}),
                                        capture_output=True, text=True, timeout=timeout + 10)
                errors = {2: "Chrome login timed out", 3: "Chrome closed before callback capture",
                          4: "Chrome capture requires Node.js with built-in WebSocket (22+)"}
                if result.returncode:
                    raise ProbeError(errors.get(result.returncode, "Chrome callback capture failed") +
                                     "; retry with --login-method manual")
                try:
                    callback = json.loads(result.stdout)["callback"]
                    if not isinstance(callback, str):
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise ProbeError("Chrome capture returned an invalid result") from None
                return _code_from_url(callback, state)
            finally:
                if browser is not None and browser.poll() is None:
                    browser.terminate()
                    try:
                        browser.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        browser.kill()
                        browser.wait(timeout=5)
    except subprocess.TimeoutExpired:
        raise ProbeError("Chrome login timed out; retry with --login-method manual") from None
    except (OSError, ValueError, IndexError):
        raise ProbeError("Chrome launch or cleanup failed; retry with --login-method manual") from None


class LoginForms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"attrs": attrs, "inputs": []}
            self.forms.append(self.current)
        elif tag in ("input", "button", "select", "textarea") and self.current is not None:
            attrs["tag"] = tag
            self.current["inputs"].append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


LOGIN_HOSTS = {"xerxes-sub.xerxessecure.com", "login.xfinity.com", "oauth.xfinity.com"}


def trusted_login_url(url):
    try:
        p = urlparse(url)
        return (p.scheme == "https" and p.hostname in LOGIN_HOSTS and
                p.port in (None, 443) and not p.username and not p.password)
    except ValueError:
        return False


def http_login(session, url, state):
    fallback = "; rerun with --login-method manual"
    method, data = "GET", None
    submissions = set()
    for _ in range(20):
        if url.startswith(REDIRECT_URI):
            return _code_from_url(url, state)
        if not trusted_login_url(url):
            raise ProbeError("Unsupported login destination" + fallback)
        response = request_retry(method, url, session=session, retry=False,
                                 data=data, headers={"User-Agent": COMMON_WEB_HEADERS["User-Agent"]})
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if not location:
                raise ProbeError("Login redirect missing destination" + fallback)
            if method == "POST" and response.status_code in (307, 308):
                raise ProbeError("Login requires replaying credentials" + fallback)
            url, method, data = urljoin(url, location), "GET", None
            continue
        if response.status_code != 200:
            raise ProbeError("Login rejected or unavailable" + fallback)
        html = response.text
        if re.search(r"captcha|recaptcha|hcaptcha", html, re.I):
            raise ProbeError("Login requires CAPTCHA" + fallback)
        parser = LoginForms()
        parser.feed(html)
        if len(parser.forms) != 1:
            raise ProbeError("Unsupported login page or JavaScript challenge" + fallback)
        form = parser.forms[0]
        target = urljoin(url, form["attrs"].get("action", ""))
        if not trusted_login_url(target) or form["attrs"].get("method", "get").lower() != "post":
            raise ProbeError("Unsupported login form destination or method" + fallback)
        if any(key in form["attrs"] for key in ("onsubmit",)):
            raise ProbeError("Login requires JavaScript" + fallback)
        fields, prompts = {}, []
        submit_count = 0
        for item in form["inputs"]:
            if any(key in item for key in ("onclick", "formaction", "formmethod")):
                raise ProbeError("Unsupported form control" + fallback)
            name = item.get("name")
            kind = item.get("type", "submit" if item["tag"] == "button" else "text").lower()
            if "disabled" in item or kind in ("reset", "button"):
                continue
            if item["tag"] in ("select", "textarea"):
                raise ProbeError("Unsupported login selection" + fallback)
            if kind == "submit":
                submit_count += 1
                if submit_count > 1:
                    raise ProbeError("Ambiguous login actions" + fallback)
            if kind in ("hidden", "submit"):
                if name:
                    if name in fields:
                        raise ProbeError("Ambiguous login fields" + fallback)
                    fields[name] = item.get("value", "")
                continue
            if not name or name in fields:
                raise ProbeError("Ambiguous login fields" + fallback)
            autocomplete = item.get("autocomplete", "").lower()
            if kind == "password" and autocomplete != "new-password":
                label = "Xfinity password (hidden): "
            elif autocomplete == "one-time-code" or name.lower() in ("otp", "otpcode", "verificationcode"):
                label = "MFA code (hidden): "
            elif autocomplete == "username" or name.lower() in ("username", "user", "email", "user_id"):
                label = "Xfinity username (hidden): "
            else:
                raise ProbeError("Unsupported login or MFA fields" + fallback)
            fields[name] = ""
            prompts.append((name, label))
        fingerprint = (target, tuple(fields))
        if not prompts or fingerprint in submissions:
            raise ProbeError("Unrecognized challenge or login not accepted" + fallback)
        submissions.add(fingerprint)
        for name, label in prompts:
            fields[name] = prompt_secret(label)
        url, method, data = target, "POST", fields
    raise ProbeError("Too many login redirects" + fallback)


def token_request(data: dict, session) -> dict:
    post_data = {
        "client_id": CLIENT_ID,
        "active_x1_account_count": "true",
        "partner_id": "comcast",
        "mso_partner_hint": "true",
        "scope": "profile",
        "rm_hint": "true",
        **data,
    }
    if ANDROID_OAUTH_CLIENT_SECRET:
        post_data["client_secret"] = ANDROID_OAUTH_CLIENT_SECRET
    response = request_retry("POST", AUTH_TOKEN_URL, session=session, retry=False,
                             data=post_data,
                             headers={"Content-Type": "application/x-www-form-urlencoded",
                                      "Accept": "application/json",
                                      "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; SM-G991B)"})
    if response.status_code in (400, 401):
        raise ProbeError("OAuth exchange rejected; code/refresh token may be invalid or the Android client secret may be required")
    result = checked_json(response, "OAuth token endpoint")
    if result.get("error") or not all(isinstance(result.get(k), str) and result[k] for k in ("access_token", "id_token")):
        raise ProbeError("OAuth token response did not include both access_token and id_token")
    return result


def jwt_exp_unverified(token: str):
    # Read only to determine approximate cache expiry, NOT to establish trust.
    if not isinstance(token, str):
        return None
    try:
        parts = token.split(".")
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, ValueError, TypeError, KeyError, OverflowError):
        return None


def secure_write(path, writer):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    os.close(fd)
    try:
        writer(temporary)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def cache_write(token):
    if CACHE_TOKENS:
        secure_write(TOKEN_FILE, lambda name: Path(name).write_text(json.dumps(token)))


def cache_read():
    if CACHE_TOKENS and TOKEN_FILE.is_file() and not TOKEN_FILE.is_symlink():
        try:
            os.chmod(TOKEN_FILE, 0o600)
            value = json.loads(TOKEN_FILE.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            pass
    return {}


def auth_session():
    session = requests.Session()
    jar = MozillaCookieJar(str(TOKEN_FILE.with_name("cookies.txt")))
    if CACHE_TOKENS and Path(jar.filename).is_file() and not Path(jar.filename).is_symlink():
        try:
            os.chmod(jar.filename, 0o600)
            jar.load(ignore_discard=True)
        except (OSError, ValueError):
            pass
    session.cookies = jar
    return session


def save_cookies(session):
    if CACHE_TOKENS:
        secure_write(TOKEN_FILE.with_name("cookies.txt"),
                     lambda name: session.cookies.save(name, ignore_discard=True))


def token_expiry(token):
    candidates = []
    for key in ("access_token", "id_token"):
        exp = jwt_exp_unverified(token.get(key, ""))
        if exp is not None:
            candidates.append(exp)
    try:
        lifetime = float(token["expires_in"])
        if math.isfinite(lifetime) and lifetime > 0:
            candidates.append(int(time.time() + lifetime))
    except (KeyError, ValueError, TypeError):
        pass
    return min(candidates) if candidates else 0


def obtain_account_tokens(login_method, session):
    if ACCOUNT_ACCESS_TOKEN and ACCOUNT_ID_TOKEN:
        return {"access_token": ACCOUNT_ACCESS_TOKEN, "id_token": ACCOUNT_ID_TOKEN,
                "token_type": "Bearer"}
    cached = cache_read()
    exp = cached.get("expires_at", 0)
    if (all(isinstance(cached.get(k), str) and cached[k] for k in ("access_token", "id_token"))
            and isinstance(exp, (int, float)) and math.isfinite(exp) and exp > time.time() + 120):
        return cached
    if cached.get("refresh_token"):
        try:
            token = token_request({"grant_type": "refresh_token", "refresh_token": cached["refresh_token"]}, session)
        except ProbeError:
            print("Refresh failed; new authorization required.", file=sys.stderr)
        else:
            if not token.get("refresh_token"):
                token["refresh_token"] = cached["refresh_token"]
            token["expires_at"] = token_expiry(token)
            cache_write(token)
            return token
    url, verifier, activity, state = pkce_params()
    if login_method == "chrome":
        code = chrome_login(url, state)
    elif login_method == "manual":
        code = manual_login(url, state)
    else:
        code = http_login(session, url, state)
    token = token_request({"code": code, "grant_type": "authorization_code",
                           "activity_id": activity, "redirect_uri": TOKEN_REDIRECT_URI,
                           "code_verifier": verifier}, session)
    token["expires_at"] = token_expiry(token)
    cache_write(token)
    save_cookies(session)
    return token


def to_gb(quantity) -> float | None:
    if not isinstance(quantity, dict):
        return None
    try:
        value = float(quantity["value"])
    except (TypeError, ValueError, KeyError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    unit = str(quantity.get("unit", "")).upper()
    if unit == "TB":
        return round(value * 1000, 2)
    if unit == "GB":
        return round(value, 2)
    if unit == "MB":
        return round(value / 1000, 2)
    return None


def usage_summary(data: dict, today=None) -> dict:
    if data.get("errors"):
        raise ProbeError("GraphQL reported errors (details suppressed)")
    try:
        usage = data["data"]["accountByServiceAccountId"]["internet"]["usage"]
        months = usage["monthlyUsage"]
    except (KeyError, TypeError):
        raise ProbeError("Usage GraphQL response missing expected fields") from None
    if not isinstance(months, list):
        raise ProbeError("Usage history is not a list")
    if not months:
        return {"status": "no_usage_history"}
    today = today or date.today()
    valid = []
    for month in months:
        try:
            start = date.fromisoformat(month["startDate"])
            end = date.fromisoformat(month["endDate"])
            if start <= end and start <= today:
                valid.append((start, end, month))
        except (KeyError, TypeError, ValueError):
            continue
    current = [item for item in valid if item[0] <= today <= item[1]]
    choices = current or valid
    if not choices:
        raise ProbeError("No readable current or historical usage cycle")
    latest = max(choices, key=lambda item: item[0])[2]
    used = to_gb(latest.get("currentUsage"))
    allowed = to_gb(latest.get("allowableUsage"))
    if str(latest.get("policy", "")).lower() == "unlimited":
        allowed = None
    result = {
        "status": ("ok" if used is not None else "incomplete_usage") if current else "historical_only",
        "start_date": latest.get("startDate"),
        "end_date": latest.get("endDate"),
        "days_remaining": latest.get("daysRemaining"),
        "policy": latest.get("policy"),
        "used_gb": used,
        "allowance_gb": allowed,
        "remaining_gb": round(max(0, allowed - used), 2) if used is not None and allowed is not None else None,
        "used_percent": round(100 * used / allowed, 1) if used is not None and allowed and allowed > 0 else None,
    }
    return result


def fetch_usage(tokens: dict, session=None) -> dict:
    headers = dict(MOBILE_HEADERS)
    headers["Authorization"] = f"{tokens.get('token_type', 'Bearer')} {tokens['access_token']}"
    headers["x-id-token"] = tokens["id_token"]
    response = request_retry("POST", GRAPHQL_URL, session=session,
                             headers=headers,
                             json={"operationName": "InternetDataUsage", "variables": {},
                                   "query": USAGE_QUERY})
    return usage_summary(checked_json(response, "Usage GraphQL API"))


def output(results):
    for service, result in results.items():
        print(f"\n{service.upper()}: {result['status']}")
        if service == "usage" and "used_gb" in result:
            print(f"  Cycle: {result['start_date']} through {result['end_date']}")
            print(f"  Used: {result['used_gb']} GB; allowance: {result['allowance_gb']} GB")
            print(f"  Consumed: {result['used_percent']}%; remaining: {result['remaining_gb']} GB")
            print(f"  Policy: {result['policy']}")
        for incident in result.get("incidents", []):
            print(f"  {incident['classification']}: {incident.get('start')} to {incident.get('estimated_restoration')}")
            print(f"  Services: {incident.get('affected_services')}")
            print(f"  {incident.get('customer_message') or incident.get('summary') or ''}")
        if result.get("error"):
            print("  " + result["error"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Unofficial Xfinity API probe")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--outage-only", action="store_true")
    group.add_argument("--usage-only", action="store_true")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--login-method", choices=("http", "manual", "chrome"), default="http")
    parser.add_argument("--manual-login", action="store_true", help="Alias for --login-method manual")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    results = {}
    for service in (["outage"] if args.outage_only else ["usage"] if args.usage_only else ["outage", "usage"]):
        try:
            if service == "outage":
                if not SERVICE_ADDRESS.strip():
                    raise ProbeError("Set XFINITY_SERVICE_ADDRESS or SERVICE_ADDRESS")
                results[service] = fetch_outage(SERVICE_ADDRESS)
            else:
                session = auth_session()
                tokens = obtain_account_tokens("manual" if args.manual_login else args.login_method, session)
                results[service] = fetch_usage(tokens, session)
        except ProbeError as exc:
            results[service] = {"status": "unknown", "error": str(exc)}
        except (OSError, ValueError, TypeError, KeyError):
            results[service] = {"status": "unknown", "error": "Local storage or response processing failed"}
        except (KeyboardInterrupt, EOFError):
            results[service] = {"status": "unknown", "error": "Login interrupted"}
    if args.json:
        print(json.dumps(results, ensure_ascii=False, allow_nan=False))
    else:
        output(results)
    successful = {"ok", "no_reported_incident", "incidents_reported"}
    return 0 if all(value["status"] in successful for value in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
