"""Keep the ``YTDLP_COOKIES`` GitHub secret fresh, from this PC.

GitHub's runners need YouTube cookies (CLAUDE.md, "Datacenter IPs need
cookies"), and those cookies have to come from a sign-in on a residential
connection: a sign-in from a datacenter is exactly what Google challenges. So
this runs here, against a browser profile kept for nothing else::

    python scripts/refresh_youtube_cookies.py --login          # once
    python scripts/refresh_youtube_cookies.py                  # each refresh
    python scripts/refresh_youtube_cookies.py --install-task   # daily (Windows)

``--login`` first asks for a GitHub token if none is stored — fine-grained,
this repository only, **Secrets: read and write** — and saves it next to the
YouTube OAuth token in ``~/.yt-audio-filter/``. Then a browser window opens:
sign in to YouTube with the throwaway account, never the channel's. The window
closes by itself once YouTube sees the sign-in, and the first refresh runs.

Every refresh opens that profile with no window, loads YouTube so Google can
rotate the session the way it would in any browser, exports the youtube.com
cookies, and writes them to the secret. Rotation invalidates the previous
export, and that is the point: the secret always holds the session's current
cookies instead of a snapshot that ages until YouTube stops honouring it. A
download that is mid-flight during a refresh can lose its session, so schedule
it when nothing runs.

Exit status: 0 updated, 2 signed out (run ``--login`` again), 1 anything else.
Each run appends one line, never a cookie, to ``cookie_refresh.log`` there.

Needs ``pip install -e ".[cookies]"``, plus ``playwright install chromium``
on a machine without Google Chrome.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import requests

from yt_audio_filter.cookie_refresh import cookies_to_netscape

#: Beside the YouTube OAuth token, and as machine-local as it.
CREDENTIALS_DIR = Path.home() / ".yt-audio-filter"
PROFILE_DIR = CREDENTIALS_DIR / "youtube-cookie-profile"
TOKEN_FILE = CREDENTIALS_DIR / "github_secrets_token"
LOG_FILE = CREDENTIALS_DIR / "cookie_refresh.log"
TOKEN_ENV = "GH_SECRETS_TOKEN"

DEFAULT_REPO = "Affiliat0r/yt-audio-filter"
SECRET_NAME = "YTDLP_COOKIES"
TASK_NAME = "QuranStudioCookieRefresh"

YOUTUBE_URL = "https://www.youtube.com/"
LOGIN_URL = "https://accounts.google.com/ServiceLogin?service=youtube&continue=" + YOUTUBE_URL

#: Both are present only while signed in: LOGIN_INFO is YouTube's own, and
#: __Secure-3PSID is the Google session it rides on.
SIGNED_IN_COOKIES = ("LOGIN_INFO", "__Secure-3PSID")

#: Long enough for YouTube's page scripts to run, which is when Google rotates.
ROTATION_WAIT_MS = 10_000

#: GitHub refuses a secret larger than this.
MAX_SECRET_BYTES = 48 * 1024

GITHUB_API = "https://api.github.com"


class SignedOut(Exception):
    """The profile no longer holds a YouTube session."""


def signed_in(cookies: List[dict]) -> bool:
    names = {c.get("name") for c in cookies}
    return all(name in names for name in SIGNED_IN_COOKIES)


# ---------------------------------------------------------------- the browser


def _launch(playwright, headless: bool):
    """The dedicated profile, in installed Chrome when this machine has it.

    Google refuses sign-in from a browser that announces it is automated ("This
    browser or app may not be secure"), so the automation switch stays off, and
    real Chrome is preferred over Playwright's own build for the same reason.
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    options = dict(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled"],
    )
    try:
        return playwright.chromium.launch_persistent_context(channel="chrome", **options)
    except Exception as exc:  # noqa: BLE001 - only "no Chrome here" falls through
        if "is not found" not in str(exc):
            raise
        return playwright.chromium.launch_persistent_context(channel="chromium", **options)


def login(timeout_minutes: int = 10) -> bool:
    """Open a window on Google's sign-in and wait until YouTube sees a session."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        context = _launch(p, headless=False)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(LOGIN_URL)
            print("Sign in with the throwaway account in the browser window.")
            print("Leave the window open: it closes by itself once you are signed in.")
            deadline = time.monotonic() + timeout_minutes * 60
            while time.monotonic() < deadline:
                if signed_in(context.cookies([YOUTUBE_URL])):
                    return True
                page.wait_for_timeout(2000)
            return False
        finally:
            context.close()


def export_cookies(headless: bool = True) -> str:
    """The profile's youtube.com cookies as a Netscape cookies.txt."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        context = _launch(p, headless=headless)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(YOUTUBE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(ROTATION_WAIT_MS)
            # Park on a page with no scripts before reading, so nothing can
            # rotate the session between this export and the profile closing.
            page.goto(YOUTUBE_URL + "robots.txt")
            cookies = context.cookies([YOUTUBE_URL])
        finally:
            context.close()

    if not signed_in(cookies):
        raise SignedOut("The cookie profile is signed out of YouTube.")
    return cookies_to_netscape(cookies) + "\n"


# ------------------------------------------------------------- the secret


def github_token(prompt: bool = False) -> Optional[str]:
    """``GH_SECRETS_TOKEN``, else the stored token, else (with ``prompt``) ask once."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    if TOKEN_FILE.is_file():
        return TOKEN_FILE.read_text(encoding="utf-8").strip() or None
    if not prompt:
        return None
    token = getpass.getpass(
        "GitHub token (fine-grained, this repository, Secrets: read and write): "
    ).strip()
    if token:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token, encoding="utf-8")
        TOKEN_FILE.chmod(0o600)
    return token or None


def encrypt_secret(public_key_b64: str, value: str) -> str:
    """Seal ``value`` to the repository's public key, as GitHub's API requires."""
    from nacl import encoding, public

    key = public.PublicKey(public_key_b64.encode(), encoding.Base64Encoder())
    return base64.b64encode(public.SealedBox(key).encrypt(value.encode())).decode()


def put_secret(repo: str, name: str, value: str, token: str, session=requests) -> None:
    """Create or replace an Actions secret on ``repo``."""
    if len(value.encode()) > MAX_SECRET_BYTES:
        raise ValueError(f"{name} would be {len(value.encode())} bytes; GitHub's limit is 48 KB.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base = f"{GITHUB_API}/repos/{repo}/actions/secrets"

    response = session.get(f"{base}/public-key", headers=headers, timeout=30)
    _raise_for_github(response, repo)
    key = response.json()

    body = {"encrypted_value": encrypt_secret(key["key"], value), "key_id": key["key_id"]}
    response = session.put(f"{base}/{name}", headers=headers, json=body, timeout=30)
    _raise_for_github(response, repo)


def _raise_for_github(response, repo: str) -> None:
    if response.status_code in (200, 201, 204):
        return
    if response.status_code == 401:
        raise RuntimeError("GitHub rejected the token: it is wrong or has expired.")
    if response.status_code in (403, 404):
        raise RuntimeError(
            f"The token cannot manage secrets on {repo}. It needs access to that "
            f"repository with Secrets: read and write."
        )
    raise RuntimeError(f"GitHub answered {response.status_code}: {response.text[:200]}")


# --------------------------------------------------------------- scheduling


def install_task(at: str) -> None:
    """Run this script daily at ``at`` (HH:MM, local time) via Task Scheduler."""
    if os.name != "nt":
        raise SystemExit(
            f"--install-task is Windows only. Elsewhere, add a cron entry:\n"
            f'  {at[3:]} {at[:2]} * * * "{sys.executable}" "{Path(__file__).resolve()}"'
        )
    command = f'"{sys.executable}" "{Path(__file__).resolve()}"'
    subprocess.run(
        ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", at, "/TN", TASK_NAME, "/TR", command],
        check=True,
    )
    print(f"Scheduled '{TASK_NAME}' daily at {at}.")


def _log(line: str) -> None:
    print(line)
    try:
        CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')}  {line}\n")
    except OSError:
        pass  # the log is a convenience; the exit status is the record


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--login", action="store_true", help="sign in once, then refresh")
    parser.add_argument("--install-task", action="store_true", help="refresh daily (Windows)")
    parser.add_argument("--at", default="04:00", help="time of day for --install-task")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--secret", default=SECRET_NAME)
    parser.add_argument("--show", action="store_true", help="refresh with a visible window")
    args = parser.parse_args(argv)

    if args.install_task:
        install_task(args.at)
        return 0

    token = github_token(prompt=args.login)
    if not token:
        _log(f"No GitHub token. Run with --login, or set {TOKEN_ENV}.")
        return 1

    try:
        if args.login and not login():
            _log("No YouTube sign-in was seen. Run --login again.")
            return 2
        cookies = export_cookies(headless=not args.show)
        put_secret(args.repo, args.secret, cookies, token)
    except SignedOut as exc:
        _log(f"{exc} Run with --login to sign in again.")
        return 2
    except Exception as exc:  # noqa: BLE001 - reported, and the exit status says so
        _log(f"Refresh failed: {exc}")
        return 1

    count = sum(1 for line in cookies.splitlines() if line and not line.startswith("#"))
    _log(f"Updated {args.secret} on {args.repo} with {count} youtube.com cookies.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
