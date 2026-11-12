"""Opt-in diagnostic script to test Xfinity browser authentication.

SECURITY & PRIVACY RULES:
- Never prints passwords, authorization codes, tokens, cookies, or secrets.
- Never hardcodes credentials.
- Credentials read from environment or .env if present.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
import json
from pathlib import Path
import sys

import aiohttp

# Ensure custom_components is in path
REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from custom_components.xfinity.api import (  # noqa: E402
    XfinityClient,
)
from custom_components.xfinity.browser_auth import (  # noqa: E402
    BrowserAuthorization,
    async_complete_browser_auth,
)

CACHE_DIR = REPO_ROOT / ".cache"
SESSION_FILE = CACHE_DIR / "browser_session.json"


def save_browser_session(auth: BrowserAuthorization) -> None:
    """Save active browser authorization session to cache directory."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": auth.url,
        "verifier": auth.verifier,
        "activity": auth.activity,
        "state": auth.state,
        "expires_at": auth.expires_at,
        "consumed": auth.consumed,
    }
    SESSION_FILE.write_text(json.dumps(payload))


def load_browser_session() -> BrowserAuthorization | None:
    """Load browser authorization session from cache directory."""
    if not SESSION_FILE.is_file():
        return None
    try:
        data = json.loads(SESSION_FILE.read_text())
        return BrowserAuthorization(
            url=data["url"],
            verifier=data["verifier"],
            activity=data["activity"],
            state=data["state"],
            expires_at=data["expires_at"],
            consumed=data.get("consumed", False),
        )
    except Exception:
        return None


async def start_browser_handoff() -> BrowserAuthorization:
    """Initialize a browser authorization session and print instructions."""
    auth = BrowserAuthorization.create(timeout_seconds=900)
    save_browser_session(auth)
    print("\n--- Browser Handoff OAuth Flow ---")
    print("1. Open the following authorization URL in your web browser:")
    print(f"\n   {auth.url}\n")
    print("2. Sign in with your Xfinity account credentials.")
    print("3. When complete, your browser will attempt to navigate to:")
    print("   xfinitydigitalhome://auth?code=oi-...&state=...")
    print("4. Copy the entire URL from your browser address bar and run:")
    print('   uv run python scripts/test_auth.py --complete-browser "<CALLBACK_URL>"\n')
    return auth


async def complete_browser_handoff(callback_url: str) -> None:
    """Complete the browser handoff session and exchange code for tokens."""
    auth = load_browser_session()
    if auth is None:
        print(
            "[ERROR] No active browser authorization session found. Run --start-browser first."
        )
        return

    print("\n--- Exchanging Browser Authorization Callback ---")
    print("Validating callback URL and extracting authorization code...")
    async with aiohttp.ClientSession() as session:
        try:
            tokens = await async_complete_browser_auth(session, auth, callback_url)
            save_browser_session(auth)  # Record consumed state
            has_refresh = bool(tokens.get("refresh_token"))
            has_access = bool(tokens.get("access_token"))

            print("\n[SUCCESS] Token exchange completed without Browserless!")
            print(f"  Valid refresh token received: {has_refresh}")
            print(f"  Valid access token received:  {has_access}")

            refresh_token = tokens.get("refresh_token")
            if isinstance(refresh_token, str):
                await verify_account_data(session, refresh_token)
        except Exception as err:
            print(f"\n[ERROR] Browser-handoff token exchange failed: {err}")


async def verify_account_data(
    session: aiohttp.ClientSession, refresh_token: str
) -> None:
    """Verify that the refresh token can fetch live account data with XfinityClient."""
    print("\n--- Verifying Live Account Access via XfinityClient ---")
    client = XfinityClient(session, "123 Main St", refresh_token)
    try:
        print("1. Refreshing access token via AUTH_TOKEN_URL...")
        await client.async_ensure_token(force=True)
        print("   Access token successfully refreshed!")

        print("2. Querying GraphQL API for Internet data usage...")
        summary = await client.async_fetch_usage(date.today())
        print("   GraphQL usage query succeeded!")
        print(f"   Usage status: {summary.get('status')}")
        if "used_gb" in summary:
            print(f"   Used GB:      {summary.get('used_gb')}")
        if "allowance_gb" in summary:
            print(f"   Allowance GB: {summary.get('allowance_gb')}")
        if "policy" in summary:
            print(f"   Data Policy:  {summary.get('policy')}")

        print("\n[VERIFIED] Full end-to-end authentication confirmed!")
        print(
            "The integration can fetch account data using tokens obtained without Browserless."
        )
    except Exception as err:
        print(f"   [ERROR] Account data verification failed: {err}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Test Xfinity browser authentication.")
    parser.add_argument(
        "--start-browser",
        action="store_true",
        help="Generate authorization URL for browser handoff.",
    )
    parser.add_argument(
        "--complete-browser",
        type=str,
        help="Complete browser handoff with callback URL.",
    )
    args = parser.parse_args()

    print("========================================")
    print("  Xfinity Authentication Diagnostic")
    print("========================================")

    if args.complete_browser:
        await complete_browser_handoff(args.complete_browser)
        return

    await start_browser_handoff()


if __name__ == "__main__":
    asyncio.run(main())
