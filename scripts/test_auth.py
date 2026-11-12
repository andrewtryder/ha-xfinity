"""Opt-in diagnostic script to test Xfinity browser authentication.

SECURITY & PRIVACY RULES:
- Never prints passwords, authorization codes, tokens, cookies, or secrets.
- Never hardcodes credentials or persists tokens to disk.
- Restricts session files to owner-only permissions (0600).
- Accepts callback URLs interactively to prevent shell history leakage.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
import json
import os
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


def load_env() -> dict[str, str]:
    """Load key-value pairs from .env if present."""
    env_file = REPO_ROOT / ".env"
    env_vars: dict[str, str] = {}
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"').strip("'")
    return env_vars


def save_browser_session(auth: BrowserAuthorization) -> None:
    """Save active browser authorization session to cache directory with 0600 permissions."""
    CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(CACHE_DIR, 0o700)
    except OSError:
        pass

    # Only store PKCE session parameters; tokens are NEVER persisted
    payload = {
        "url": auth.url,
        "verifier": auth.verifier,
        "activity": auth.activity,
        "state": auth.state,
        "expires_at": auth.expires_at,
        "consumed": auth.consumed,
    }

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(SESSION_FILE, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    with open(fd, "w") as f:
        f.write(json.dumps(payload))


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
    print("\n========================================")
    print("  Xfinity Browser Authentication Setup")
    print("========================================")
    print("\nStep 1: Open Google Chrome with Developer Tools (F12 or Cmd+Option+I).")
    print("        In DevTools, go to the Network tab and check 'Preserve log'.")
    print(
        "\nStep 2: Navigate to the following authorization link (expires in 15 minutes):"
    )
    print(f"\n   {auth.url}\n")
    print(
        "Step 3: Sign in with your Xfinity credentials and complete any verification."
    )
    print("Step 4: When sign-in completes, Chrome will try to open:")
    print("        xfinitydigitalhome://auth?code=oi-...&state=...")
    print(
        "        Copy the entire URL from the Chrome address bar, or from the DevTools"
    )
    print("        Console / Network redirect Location header.")
    print("\nStep 5: Run the completion command and paste the URL when prompted:")
    print("        uv run python scripts/test_auth.py --complete\n")
    return auth


async def complete_browser_handoff() -> None:
    """Complete the browser handoff session and exchange code for tokens."""
    auth = load_browser_session()
    if auth is None:
        print(
            "[ERROR] No active browser authorization session found. Run --start-browser first."
        )
        sys.exit(1)

    print("\n--- Enter Authorization Callback ---")
    try:
        callback_url = input(
            "Paste complete callback URL (xfinitydigitalhome://auth?code=...&state=...): "
        ).strip()
    except KeyboardInterrupt, EOFError:
        print("\nAborted.")
        sys.exit(1)

    if not callback_url:
        print("[ERROR] Callback URL is required.")
        sys.exit(1)

    print("\nValidating callback URL and exchanging code with Xfinity...")
    async with aiohttp.ClientSession() as session:
        try:
            tokens = await async_complete_browser_auth(session, auth, callback_url)
            # Record that session was consumed (tokens remain in memory only)
            save_browser_session(auth)

            refresh_token = tokens.get("refresh_token")
            if not isinstance(refresh_token, str) or not refresh_token:
                print("[ERROR] Token exchange rejected or missing refresh token.")
                sys.exit(1)

            print("Token exchange succeeded! Verifying live account access...")
            env_vars = load_env()
            address = (
                os.environ.get("XFINITY_ADDRESS")
                or env_vars.get("XFINITY_ADDRESS")
                or ""
            )
            await verify_account_data(session, refresh_token, address)
        except Exception as err:
            print(f"\n[ERROR] Authentication failed: {err}")
            sys.exit(1)


async def verify_account_data(
    session: aiohttp.ClientSession,
    refresh_token: str,
    address: str,
) -> None:
    """Verify that the refresh token can fetch live account data with XfinityClient."""
    token_state = {"current_refresh": refresh_token}

    def on_rotated(new_refresh: str) -> None:
        token_state["current_refresh"] = new_refresh

    client = XfinityClient(
        session,
        address or "Primary Account",
        refresh_token,
        on_tokens_rotated=on_rotated,
    )
    try:
        await client.async_ensure_token()

        summary = await client.async_fetch_usage(date.today())
        print("\n[SUCCESS] Authentication and GraphQL usage query verified!")
        print(f"  Usage status:   {summary.get('status')}")
        if "used_gb" in summary:
            print(f"  Used GB:        {summary.get('used_gb')} GB")
        if "allowance_gb" in summary:
            print(f"  Allowance GB:   {summary.get('allowance_gb')} GB")

        if address and address != "Primary Account":
            try:
                outage = await client.async_fetch_outage()
                print(f"  Outage status:  {outage.get('status')}")
            except Exception as err:
                print(f"  [WARN] Outage query failed for address '{address}': {err}")
        else:
            print(
                "  Outage check:   Skipped (set XFINITY_ADDRESS to verify outage endpoint)"
            )

        print("\nAccount access is verified and ready for Home Assistant.")
    except Exception as err:
        print(f"\n[ERROR] Account data verification failed: {err}")
        sys.exit(1)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Test Xfinity browser authentication.")
    parser.add_argument(
        "--start-browser",
        action="store_true",
        help="Generate authorization URL for browser handoff.",
    )
    parser.add_argument(
        "--complete",
        action="store_true",
        help="Complete browser handoff by interactively pasting callback URL.",
    )
    args = parser.parse_args()

    if args.complete:
        await complete_browser_handoff()
        return

    await start_browser_handoff()


if __name__ == "__main__":
    asyncio.run(main())
