#!/usr/bin/env python3
"""Sign in once on this computer and print a Home Assistant refresh token.

Run from this directory after installing the requirements:

    python get_token.py

The password is typed into this terminal and is not saved. Nothing is written
to disk. A browser window opens for the Xfinity sign-in page. If Xfinity asks
for a verification code, enter it in that window before this script finishes.
"""

from __future__ import annotations

import sys

import xfinity_api_probe as probe


def main() -> int:
    print(
        "\n".join([
            "This signs in to Xfinity on this computer and prints a refresh token.",
            "Home Assistant asks for that token when you add the Xfinity integration.",
            "",
            "1. Type your Xfinity username and password below. Typing is hidden.",
            "2. A browser window opens. Leave it in front until sign-in finishes.",
            "3. If Xfinity asks for a verification code, enter it in that window.",
            "4. Copy the token printed at the end into Home Assistant.",
            "",
        ]),
        file=sys.stderr,
    )
    session = probe.requests.Session()
    try:
        url, verifier, activity, state = probe.pkce_params()
        code = probe.cloak_login(url, state)
        token = probe.token_request(
            {
                "code": code,
                "grant_type": "authorization_code",
                "activity_id": activity,
                "redirect_uri": probe.TOKEN_REDIRECT_URI,
                "code_verifier": verifier,
            },
            session,
        )
    except probe.ProbeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Login interrupted.", file=sys.stderr)
        return 1
    refresh = token.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        print("Xfinity did not return a refresh token.", file=sys.stderr)
        return 1
    print("Copy this refresh token into Home Assistant:", file=sys.stderr)
    print(refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
