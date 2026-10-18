"""Offline fixtures demonstrate supported behavior, not live Xfinity compatibility."""
import base64
from contextlib import redirect_stdout, redirect_stderr
from datetime import date, datetime, timezone
import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import requests
import xfinity_api_probe as p


CODE = "oi-" + "a" * 32
CALLBACK = p.REDIRECT_URI + "?code=" + CODE + "&state=state"
NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def response(status=200, body=None, text="", headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = (json.dumps(body) if body is not None else text).encode()
    result.headers.update(headers or {})
    return result


def month(start="2026-10-01", end="2026-10-31", **extra):
    return {"startDate": start, "endDate": end, "policy": "limited",
            "currentUsage": {"value": 300, "unit": "GB"},
            "allowableUsage": {"value": 1.2, "unit": "TB"}, **extra}


def usage(*months):
    return {"data": {"accountByServiceAccountId": {"internet": {
        "usage": {"monthlyUsage": list(months)}}}}}


def outage(planned=True, start="2026-10-19T00:00:00Z"):
    return {"outageInfoResponseList": [{"outageId": "example", "services": ["Internet"],
                                        "startTime": {"iso": start}}],
            "customerMessage": {"planned": planned, "outageType": "example",
                                "etr": {"iso": "2026-10-17T00:00:00Z"},
                                "messages": {"shortDescription": "Maintenance"},
                                "customerFacingText": "Example message"}}


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, value in [("TOKEN_FILE", Path(self.temp.name) / "cache" / "oauth_token.json"),
                              ("ACCOUNT_ACCESS_TOKEN", ""), ("ACCOUNT_ID_TOKEN", ""),
                              ("ANDROID_OAUTH_CLIENT_SECRET", ""), ("CACHE_TOKENS", True)]:
            patcher = patch.object(p, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target in ("requests.sessions.Session.send", "socket.socket.connect", "webbrowser.open"):
            patcher = patch(target, side_effect=AssertionError("Live I/O forbidden in tests"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_pkce(self):
        url, verifier, activity, state = p.pkce_params()
        params = parse_qs(urlparse(url).query)
        digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(params["code_challenge"], [digest])
        self.assertEqual(params["state"], [state])
        self.assertEqual(params["activity_id"], [activity])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertNotEqual(p.pkce_params()[3], state)

    def test_callback_validation(self):
        self.assertEqual(p._code_from_url(CALLBACK, "state"), CODE)
        self.assertEqual(p._code_from_url(CALLBACK + "&activity_id=example", "state"), CODE)
        for url in (CALLBACK.replace("&state=state", ""), CALLBACK + "&state=state",
                    CALLBACK + "&code=" + CODE, CALLBACK.replace("state=state", "state=wrong"),
                    CALLBACK.replace("xfinitydigitalhome://auth", "https://evil.invalid"),
                    CALLBACK.replace(CODE, "0.ac.intermediate"), CALLBACK + "&error=denied"):
            with self.subTest(url=url), self.assertRaises(p.ProbeError):
                p._code_from_url(url, "state")

    def test_manual_input_hidden_and_stderr(self):
        output = io.StringIO()
        with patch.object(p.sys.stdin, "isatty", return_value=True), \
                patch.object(p, "getpass", return_value=CALLBACK) as hidden, \
                patch.object(p.webbrowser, "open"), redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(p.manual_login("https://example.invalid", "state"), CODE)
            hidden.assert_called_once()
        self.assertEqual(output.getvalue(), "")

    def test_no_interactive_input_without_tty(self):
        with patch.object(p.sys.stdin, "isatty", return_value=False):
            with self.assertRaises(p.ProbeError):
                p.prompt_secret("password")
            with self.assertRaises(p.ProbeError):
                p.manual_login("url", "state")

    def test_getpass_echo_fallback_is_rejected(self):
        import warnings

        def insecure_prompt(*args, **kwargs):
            warnings.warn("Cannot control echo", p.GetPassWarning)
            raise AssertionError("Must stop before echoed input")

        with patch.object(p.sys.stdin, "isatty", return_value=True), \
                patch.object(p, "getpass", side_effect=insecure_prompt):
            with self.assertRaisesRegex(p.ProbeError, "Secure terminal"):
                p.prompt_secret("Password")

    def test_http_username_password_otp_and_hidden_fields(self):
        session = Mock()
        forms = [
            '<form method="post" action="/username"><input type="hidden" name="csrf" value="a&amp;b"><input name="username"></form>',
            '<form method="post" action="/password"><input type="password" name="password"></form>',
            '<form method="post" action="/otp"><input name="challenge" autocomplete="one-time-code"></form>',
        ]
        session.request.side_effect = [response(text=f) for f in forms] + [response(302, headers={"Location": CALLBACK})]
        with patch.object(p, "prompt_secret", side_effect=["user", " password ", "123456"]):
            self.assertEqual(p.http_login(session, "https://login.xfinity.com/", "state"), CODE)
        calls = session.request.call_args_list
        self.assertEqual(calls[1].kwargs["data"], {"csrf": "a&b", "username": "user"})
        self.assertEqual(calls[2].kwargs["data"], {"password": " password "})
        self.assertEqual(calls[3].kwargs["data"], {"challenge": "123456"})
        for call in calls:
            self.assertTrue(call.kwargs["verify"])
            self.assertFalse(call.kwargs["allow_redirects"])

    def test_unsupported_forms_stop_before_credentials(self):
        forms = ['<script>login()</script>', '<div>captcha</div>',
                 '<form method="post" action="https://evil.invalid"><input name="username"></form>',
                 '<form><input type="password" name="password"></form>',
                 '<form method="post"><input name="security_answer"></form>',
                 '<form method="post" onsubmit="go()"><input name="username"></form>',
                 '<form method="post"><input name="username"><button>A</button><button>B</button></form>']
        for html in forms:
            with self.subTest(html=html), patch.object(p, "prompt_secret") as prompt:
                session = Mock()
                session.request.return_value = response(text=html)
                with self.assertRaisesRegex(p.ProbeError, "login-method manual"):
                    p.http_login(session, "https://login.xfinity.com/", "state")
                prompt.assert_not_called()

    def test_redirect_security_and_bounds(self):
        for location in ("http://login.xfinity.com/", "https://login.xfinity.com.evil.invalid/",
                         "https://user:password@login.xfinity.com/", "https://login.xfinity.com:444/"):
            session = Mock()
            session.request.return_value = response(302, headers={"Location": location})
            with self.assertRaises(p.ProbeError):
                p.http_login(session, "https://login.xfinity.com/", "state")
            self.assertEqual(session.request.call_count, 1)
        session = Mock()
        session.request.return_value = response(302, headers={"Location": "/loop"})
        with self.assertRaises(p.ProbeError):
            p.http_login(session, "https://login.xfinity.com/", "state")
        self.assertEqual(session.request.call_count, 20)

    def test_no_credential_replay_or_repeated_login_attempt(self):
        form = '<form method="post"><input name="username"></form>'
        for second in (response(text=form), response(307, headers={"Location": "/again"})):
            session = Mock()
            session.request.side_effect = [response(text=form), second]
            with patch.object(p, "prompt_secret", return_value="example") as prompt:
                with self.assertRaises(p.ProbeError):
                    p.http_login(session, "https://login.xfinity.com/", "state")
                self.assertEqual(prompt.call_count, 1)
            self.assertEqual(session.request.call_count, 2)

    def test_network_error_redaction_and_read_retry(self):
        session = Mock()
        session.request.side_effect = requests.ConnectionError("password=secret&code=sensitive")
        with self.assertRaises(p.ProbeError) as error:
            p.request_retry("POST", p.AUTH_TOKEN_URL, session=session, retry=False)
        self.assertNotIn("secret", str(error.exception))
        self.assertNotIn("sensitive", str(error.exception))
        self.assertEqual(session.request.call_count, 1)
        session.request.side_effect = [response(503), response(body={})]
        with patch.object(p.time, "sleep"):
            self.assertEqual(p.request_retry("GET", p.OUTAGE_STATUS_URL, session=session).status_code, 200)

    def test_token_exchange_no_retry_and_secret_configuration(self):
        session = Mock()
        session.request.return_value = response(body={"access_token": "access", "id_token": "id"})
        with patch.object(p, "ANDROID_OAUTH_CLIENT_SECRET", "configured-secret"):
            self.assertEqual(p.token_request({"grant_type": "authorization_code", "code": CODE}, session)["access_token"], "access")
        self.assertEqual(session.request.call_args.kwargs["data"]["client_secret"], "configured-secret")
        session.reset_mock()
        session.request.return_value = response(503, text="sensitive-body")
        with self.assertRaises(p.ProbeError) as error:
            p.token_request({}, session)
        self.assertNotIn("sensitive-body", str(error.exception))
        self.assertEqual(session.request.call_count, 1)

    def test_malformed_token_responses(self):
        for reply in (response(400, body={"error": "invalid_client"}), response(body=[]),
                      response(text="html"), response(body={"access_token": "access"}),
                      response(body={"access_token": {}, "id_token": "id"})):
            session = Mock()
            session.request.return_value = reply
            with self.assertRaises(p.ProbeError):
                p.token_request({}, session)

    def test_refresh_rotation_or_retention(self):
        for replacement in (None, "rotated"):
            with self.subTest(replacement=replacement):
                p.cache_write({"refresh_token": "original", "expires_at": 0})
                token = {"access_token": "access", "id_token": "id", "expires_in": 3600}
                if replacement:
                    token["refresh_token"] = replacement
                session = Mock()
                session.request.return_value = response(body=token)
                with patch.object(p, "http_login") as login:
                    result = p.obtain_account_tokens("http", session)
                    login.assert_not_called()
                self.assertEqual(result["refresh_token"], replacement or "original")
                self.assertEqual(p.cache_read(), result)
                self.assertEqual(session.request.call_args.kwargs["data"]["grant_type"], "refresh_token")

    def test_refresh_failure_then_new_exchange(self):
        p.cache_write({"refresh_token": "expired"})
        session = p.auth_session()
        with patch.object(session, "request", side_effect=[response(400), response(body={
                "access_token": "new", "id_token": "new-id", "expires_in": 3600})]) as request, \
                patch.object(p, "http_login", return_value=CODE), redirect_stderr(io.StringIO()):
            result = p.obtain_account_tokens("http", session)
        self.assertEqual(result["access_token"], "new")
        data = request.call_args.kwargs["data"]
        self.assertEqual(data["redirect_uri"], p.TOKEN_REDIRECT_URI)
        self.assertIn("code_verifier", data)

    def test_cache_and_cookie_permissions_and_reuse(self):
        p.cache_write({"access_token": "access", "id_token": "id", "expires_at": 9999999999})
        self.assertEqual(p.TOKEN_FILE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(p.TOKEN_FILE.parent.stat().st_mode & 0o777, 0o700)
        session = p.auth_session()
        session.cookies.set_cookie(requests.cookies.create_cookie("session", "cookie-value", domain="login.xfinity.com", secure=True))
        p.save_cookies(session)
        self.assertEqual(p.TOKEN_FILE.with_name("cookies.txt").stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(p.auth_session().cookies)[0].value, "cookie-value")
        with patch.object(p, "token_request") as request:
            self.assertEqual(p.obtain_account_tokens("http", session)["access_token"], "access")
            request.assert_not_called()

    def test_corrupt_cache_and_atomic_failure(self):
        p.cache_write({"old": True})
        with self.assertRaises(RuntimeError):
            p.secure_write(p.TOKEN_FILE, lambda _: (_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(p.cache_read(), {"old": True})
        self.assertEqual(len(list(p.TOKEN_FILE.parent.iterdir())), 1)
        for content in ("garbage", "[]", "null"):
            p.TOKEN_FILE.write_text(content)
            self.assertEqual(p.cache_read(), {})

    def test_conservative_expiry(self):
        payload = base64.urlsafe_b64encode(json.dumps({"exp": 1500}).encode()).decode().rstrip("=")
        with patch.object(p.time, "time", return_value=1000):
            self.assertEqual(p.token_expiry({"id_token": "x." + payload + ".x", "expires_in": 1000}), 1500)
        self.assertEqual(p.token_expiry({"expires_in": "nan"}), 0)
        self.assertIsNone(p.jwt_exp_unverified(None))

    def test_usage_current_cycle_over_future(self):
        result = p.usage_summary(usage(month(), month("2026-11-01", "2026-11-30")), date(2026, 10, 18))
        self.assertEqual(result["start_date"], "2026-10-01")
        self.assertEqual((result["used_gb"], result["allowance_gb"], result["used_percent"], result["remaining_gb"]),
                         (300, 1200, 25, 900))
        self.assertEqual(p.usage_summary(usage(month()), date(2026, 11, 1))["status"], "historical_only")

    def test_usage_missing_unlimited_zero_and_overage(self):
        for extra in ({"allowableUsage": None}, {"policy": "unlimited"}):
            result = p.usage_summary(usage(month(**extra)), date(2026, 10, 18))
            for key in ("allowance_gb", "used_percent", "remaining_gb"):
                self.assertIsNone(result[key])
        result = p.usage_summary(usage(month(allowableUsage={"value": 0, "unit": "GB"})), date(2026, 10, 18))
        self.assertIsNone(result["used_percent"])
        result = p.usage_summary(usage(month(currentUsage={"value": 1500, "unit": "GB"})), date(2026, 10, 18))
        self.assertEqual((result["used_percent"], result["remaining_gb"]), (125, 0))

    def test_malformed_usage_and_redaction(self):
        for data in ({}, {"errors": [{"message": "secret-token"}]}, usage({"startDate": None})):
            with self.assertRaises(p.ProbeError) as error:
                p.usage_summary(data)
            self.assertNotIn("secret-token", str(error.exception))
        for quantity in ({"value": "nan", "unit": "GB"}, {"value": -1, "unit": "GB"}, {"value": 1, "unit": "unknown"}):
            self.assertIsNone(p.to_gb(quantity))

    def test_outage_classifications(self):
        cases = [(True, "2026-10-19T00:00:00Z", "scheduled_maintenance", False),
                 (True, "2026-10-18T08:00:00-04:00", "planned_maintenance_underway", True),
                 (False, "2026-10-18T00:00:00Z", "unplanned_outage", True),
                 (True, None, "unknown", False), (True, "2026-10-18T08:00:00", "unknown", False),
                 (None, "invalid", "unknown", False)]
        for planned, start, classification, active in cases:
            with self.subTest(classification=classification):
                result = p.outage_summary(outage(planned, start), NOW)
                self.assertEqual(result["incidents"][0]["classification"], classification)
                self.assertEqual(result["active_outage"], active)
        self.assertEqual(p.outage_summary({"outageInfoResponseList": []})["status"], "no_reported_incident")

    def test_multiple_and_malformed_incidents(self):
        data = outage()
        data["outageInfoResponseList"].append({"startTime": {"iso": "2026-10-17T00:00:00Z"}})
        result = p.outage_summary(data, NOW)
        self.assertEqual(result["incident_count"], 2)
        self.assertTrue(result["active_outage"])
        for data in ({}, {"outageInfoResponseList": None}, {"outageInfoResponseList": [{}]},
                     {"outageInfoResponseList": [None], "customerMessage": {}}):
            self.assertEqual(p.outage_summary(data)["status"], "unknown")

    def test_outage_http_contract(self):
        session = Mock()
        session.request.side_effect = [response(201, headers={"X-OutageDataService-Token": "anonymous"}),
                                       response(body={"outageInfoResponseList": []})]
        with patch.object(p.requests, "Session", return_value=session):
            self.assertEqual(p.fetch_outage("example address")["status"], "no_reported_incident")
        kwargs = session.request.call_args.kwargs
        self.assertEqual(kwargs["params"], {"includeIdealOutage": "true", "tokenizeDateTime": "true", "address": "example address"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer anonymous")

    def test_graphql_http_contract(self):
        session = Mock()
        session.request.return_value = response(body=usage(month()))
        p.fetch_usage({"access_token": "access", "id_token": "id"}, session)
        kwargs = session.request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["x-id-token"], "id")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer access")
        self.assertEqual(kwargs["json"]["query"], p.USAGE_QUERY)

    def test_independent_failures_and_json_stdout(self):
        for failed in ("usage", "outage"):
            output = io.StringIO()
            with patch.object(p, "fetch_outage", side_effect=p.ProbeError("outage failed") if failed == "outage" else None,
                              return_value={"status": "no_reported_incident"}), \
                    patch.object(p, "obtain_account_tokens", return_value={}), \
                    patch.object(p, "fetch_usage", side_effect=p.ProbeError("usage failed") if failed == "usage" else None,
                                 return_value={"status": "ok"}), redirect_stdout(output):
                self.assertEqual(p.main(["--all", "--json"]), 1)
            results = json.loads(output.getvalue())
            self.assertEqual(list(results), ["outage", "usage"])
            self.assertEqual(results[failed]["status"], "unknown")

    def test_cli_modes_and_conflict(self):
        with patch.object(p, "fetch_outage", return_value={"status": "no_reported_incident"}), \
                patch.object(p, "obtain_account_tokens") as auth, redirect_stdout(io.StringIO()):
            self.assertEqual(p.main(["--outage-only"]), 0)
            auth.assert_not_called()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            p.main(["--all", "--usage-only"])
        self.assertEqual(error.exception.code, 2)

    def test_manual_alias_and_outage_before_auth(self):
        calls = []

        def fetch(address):
            calls.append("outage")
            return {"status": "no_reported_incident"}

        def auth(method, session):
            self.assertEqual(method, "manual")
            calls.append("auth")
            return {}

        with patch.object(p, "fetch_outage", side_effect=fetch), \
                patch.object(p, "obtain_account_tokens", side_effect=auth), \
                patch.object(p, "fetch_usage", return_value={"status": "ok"}), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(p.main(["--manual-login"]), 0)
        self.assertEqual(calls, ["outage", "auth"])

    def chrome_launch_mock(self, *args, **kwargs):
        directory = next(arg.split("=", 1)[1] for arg in args[0] if arg.startswith("--user-data-dir="))
        self.chrome_directory = Path(directory)
        self.assertEqual(self.chrome_directory.stat().st_mode & 0o777, 0o700)
        (self.chrome_directory / "DevToolsActivePort").write_text("12345\n/devtools/browser/example")
        self.browser = Mock()
        self.browser.poll.return_value = None
        return self.browser

    def test_chrome_capture_validates_callback_and_cleans_up(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"callback": CALLBACK}), "")
        output = io.StringIO()
        with patch.object(p, "chrome_executable", return_value="chrome"), \
                patch.object(p.shutil, "which", return_value="node"), \
                patch.object(p.subprocess, "Popen", side_effect=self.chrome_launch_mock), \
                patch.object(p.subprocess, "run", return_value=result) as run, \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(p.chrome_login("https://example.invalid/authorize", "state"), CODE)
        self.assertEqual(output.getvalue(), "")
        self.assertFalse(self.chrome_directory.exists())
        self.browser.terminate.assert_called_once()
        self.assertTrue(run.call_args.kwargs["capture_output"])
        config = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(config["base"], "http://127.0.0.1:12345/")

    def test_chrome_failures_redacted_and_cleanup(self):
        outcomes = [subprocess.CompletedProcess([], 0, json.dumps({"callback": CALLBACK.replace("state=state", "state=wrong")}), ""),
                    subprocess.CompletedProcess([], 0, "sensitive-invalid-output", ""),
                    subprocess.CompletedProcess([], 2, "sensitive-code", "sensitive-error"),
                    subprocess.CompletedProcess([], 3, "", ""),
                    subprocess.CompletedProcess([], 4, "", "")]
        for result in outcomes:
            with self.subTest(status=result.returncode), \
                    patch.object(p, "chrome_executable", return_value="chrome"), \
                    patch.object(p.shutil, "which", return_value="node"), \
                    patch.object(p.subprocess, "Popen", side_effect=self.chrome_launch_mock), \
                    patch.object(p.subprocess, "run", return_value=result), redirect_stderr(io.StringIO()):
                with self.assertRaises(p.ProbeError) as error:
                    p.chrome_login("https://example.invalid", "state")
                self.assertNotIn("sensitive", str(error.exception))
                self.assertNotIn(CODE, str(error.exception))
                self.assertFalse(self.chrome_directory.exists())
                self.browser.terminate.assert_called_once()

    def test_chrome_missing_node_does_not_launch_browser(self):
        with patch.object(p.shutil, "which", return_value=None), patch.object(p.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(p.ProbeError, "Node.js"):
                p.chrome_login("https://example.invalid", "state")
            launch.assert_not_called()

    def test_chrome_code_exchanged_with_original_pkce(self):
        session = p.auth_session()
        with patch.object(p, "pkce_params", return_value=("url", "verifier", "activity", "state")), \
                patch.object(p, "chrome_login", return_value=CODE) as capture, \
                patch.object(p, "token_request", return_value={"access_token": "access", "id_token": "id"}) as exchange:
            p.obtain_account_tokens("chrome", session)
        capture.assert_called_once_with("url", "state")
        data = exchange.call_args.args[0]
        self.assertEqual((data["code"], data["code_verifier"], data["activity_id"]), (CODE, "verifier", "activity"))

    @unittest.skipUnless(shutil.which("node"), "Optional Chrome backend requires Node")
    def test_embedded_chrome_listener_with_synthetic_cdp_events(self):
        # Run the real embedded listener, replacing fetch and WebSocket with
        # in-memory fakes. No browser, credentials, or network are involved.
        events = [
            {"method": "Network.requestWillBeSent", "params": {"request": {"url": CALLBACK}}},
            {"method": "Network.requestWillBeSent", "params": {"redirectResponse": {"headers": {"Location": CALLBACK}}}},
            {"method": "Network.responseReceivedExtraInfo", "params": {"headers": {"location": CALLBACK}}},
            {"method": "Network.responseReceived", "params": {"response": {"headers": {"Location": CALLBACK}}}},
            {"method": "Page.frameRequestedNavigation", "params": {"url": CALLBACK}},
            {"method": "Page.frameScheduledNavigation", "params": {"url": CALLBACK}},
        ]
        fixture = r'''
global.fetch = async () => ({ok:true, json:async () => [{type:'page', webSocketDebuggerUrl:'ws://127.0.0.1:12345/devtools/page/test'}]});
global.WebSocket = class extends EventTarget {
  constructor() { super(); this.readyState = 1; queueMicrotask(() => this.dispatchEvent(new Event('open'))); }
  message(value) { this.dispatchEvent(new MessageEvent('message', {data:JSON.stringify(value)})); }
  send(raw) {
    const command = JSON.parse(raw);
    queueMicrotask(() => {
      this.message({id:command.id, result:{}});
      if (command.method === 'Page.navigate') {
        // An intermediate redirect must not win the capture race.
        this.message({method:'Network.requestWillBeSent', params:{request:{url:'https://xerxes-sub.xerxessecure.com/xerxes-jwt/oauth/code?code=0.ac.fake'}}});
        this.message(TEST_EVENT);
      }
    });
  }
};
'''
        for event in events:
            with self.subTest(method=event["method"]):
                source = "const TEST_EVENT = " + json.dumps(event) + ";\n" + fixture + p.CHROME_CAPTURE_JS
                result = subprocess.run([shutil.which("node"), "-e", source], input=json.dumps({
                    "base": "http://127.0.0.1:12345/", "url": "https://example.invalid", "timeout": 2}),
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, "Synthetic CDP capture failed")
                self.assertEqual(json.loads(result.stdout), {"callback": CALLBACK})
                self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
