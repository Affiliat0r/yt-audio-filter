"""Re-authorise YouTube from any device, via the Studio.

The worker runs unattended. When its refresh token dies, Google's normal
installed-app flow wants to open a browser *on this machine* — which is no use
when the person is on a phone in another building.

This routes consent through the Studio instead:

1. the worker mints a PKCE pair and registers the public half,
2. the Studio shows "<hostname> needs authorising" and hands the user a link,
3. Google redirects the finished authorisation back to the Studio,
4. the worker collects the relayed code and exchanges it here.

**Only the worker can redeem that code.** The PKCE ``code_verifier`` and the
OAuth client secret both stay on this machine, so the code sitting briefly in
the Studio's Redis is not enough to mint a token — which is what makes it
acceptable to relay it through a server at all.

Device flow would have been simpler, but Google rejects it for anything except
a "TVs and Limited Input devices" client (``invalid_client``: Invalid client
type), and this project's client is a Web application.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

import requests

from yt_audio_filter.logger import get_logger
from yt_audio_filter.uploader import CREDENTIALS_DIR, SCOPES

from .contract import AuthRequest

logger = get_logger()

#: Google's downloaded JSON for a Web application client, `{"web": {...}}`.
WEB_CLIENT_FILE = CREDENTIALS_DIR / "client_secrets_web.json"

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
CALLBACK_PATH = "/api/auth/youtube/callback"

#: How long to wait for a human. Generous: they may be away from the phone.
DEFAULT_TIMEOUT_SECONDS = 600
POLL_SECONDS = 3.0


class RemoteAuthError(RuntimeError):
    """The remote flow was configured but did not complete."""


def load_web_client(
    path: Optional[Path] = None, env: Optional[dict] = None
) -> Optional[Tuple[str, str]]:
    """``(client_id, client_secret)`` for the Web client, or None if absent.

    Absent is a normal state, not an error: remote authorisation is opt-in, and
    a worker without it simply falls back to the local browser flow.
    """
    env = os.environ if env is None else env
    path = WEB_CLIENT_FILE if path is None else Path(path)

    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            block = raw.get("web") or raw.get("installed") or raw
            cid, secret = block.get("client_id"), block.get("client_secret")
            if cid and secret:
                return str(cid), str(secret)
            logger.warning("%s has no client_id/client_secret pair", path.name)
        except Exception as exc:  # noqa: BLE001 - fall through to env
            logger.warning("Could not read %s: %s", path.name, exc)

    cid = (env.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
    secret = (env.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
    if cid and secret:
        return cid, secret
    return None


def make_pkce_pair() -> Tuple[str, str]:
    """A ``(verifier, challenge)`` pair per RFC 7636, method S256.

    The challenge is the *unpadded* base64url SHA-256 of the verifier; leaving
    the ``=`` padding on is the classic way to get ``invalid_grant`` at the
    exchange, long after the user has already approved.
    """
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def redirect_uri_for(base_url: str, env: Optional[dict] = None) -> str:
    """Must match the Studio's byte for byte — Google compares exactly.

    Mirrors ``redirectUri()`` in ``web/app/api/auth/youtube/start/route.ts``,
    including the same env override, so a deployment that sets one sets both.
    """
    env = os.environ if env is None else env
    override = (env.get("GOOGLE_OAUTH_REDIRECT_URI") or "").strip()
    if override:
        return override
    return base_url.rstrip("/") + CALLBACK_PATH


def exchange_code(
    code: str,
    verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    *,
    session: Optional[requests.Session] = None,
    timeout: Any = (10, 30),
):
    """Trade the relayed code for credentials. Never leaves this machine."""
    poster = session.post if session is not None else requests.post
    response = poster(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=timeout,
    )
    status = getattr(response, "status_code", 0)
    payload = {}
    try:
        payload = response.json()
    except ValueError:
        pass
    if not 200 <= status < 300:
        raise RemoteAuthError(
            f"Token exchange failed ({payload.get('error', status)}): "
            f"{payload.get('error_description', 'no detail')}"
        )
    if not payload.get("refresh_token"):
        # Without this the credentials work for an hour and then need the whole
        # dance again. `prompt=consent` on the Studio side is what guarantees it.
        raise RemoteAuthError(
            "Google returned no refresh token; the consent URL must request "
            "access_type=offline and prompt=consent"
        )

    from google.oauth2.credentials import Credentials

    return Credentials(
        token=payload.get("access_token"),
        refresh_token=payload["refresh_token"],
        token_uri=TOKEN_ENDPOINT,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


class RemoteAuthorizer:
    """The zero-argument hook ``uploader.REMOTE_AUTH_HANDLER`` expects.

    Returns fresh ``Credentials``, or ``None`` when remote authorisation is not
    configured — in which case the caller falls back to its existing error,
    which tells the user what to run at the machine itself.
    """

    def __init__(
        self,
        client,
        base_url: str,
        worker_id: str,
        hostname: str,
        *,
        report: Optional[Callable[[str, list], None]] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        poll_seconds: float = POLL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.base_url = base_url
        self.worker_id = worker_id
        self.hostname = hostname
        self.report = report
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self._sleep = sleep
        self._now = now

    def _say(self, message: str, lines: Optional[list] = None) -> None:
        logger.info(message)
        if self.report:
            try:
                self.report(message, lines or [])
            except Exception:  # noqa: BLE001 - progress is never load-bearing
                pass

    def __call__(self):
        web = load_web_client()
        if not web:
            logger.info(
                "Remote YouTube authorisation is not configured "
                "(no %s); falling back to the local flow",
                WEB_CLIENT_FILE.name,
            )
            return None
        client_id, client_secret = web

        verifier, challenge = make_pkce_pair()
        state = secrets.token_urlsafe(32)

        ack = self.client.request_auth(
            AuthRequest(
                worker_id=self.worker_id,
                hostname=self.hostname,
                state=state,
                code_challenge=challenge,
            )
        )
        self._say(
            "YouTube access needs re-authorising",
            [
                f"Open the Studio and press Authorise for {self.hostname}.",
                ack.authorize_url or "(the Studio will show the link)",
                "Waiting up to 10 minutes...",
            ],
        )

        deadline = self._now() + self.timeout_seconds
        while self._now() < deadline:
            self._sleep(self.poll_seconds)
            try:
                answer = self.client.fetch_auth_code(state)
            except Exception as exc:  # noqa: BLE001 - keep polling through blips
                logger.debug("Auth poll failed (retrying): %s", exc)
                continue

            if answer.status == "expired":
                raise RemoteAuthError("The authorisation request expired")
            if answer.status != "ready" or not answer.code:
                continue
            if answer.state and answer.state != state:
                # Someone else's flow. Refusing is the whole point of `state`.
                raise RemoteAuthError("Authorisation state mismatch; refusing the code")

            self._say("Authorisation received; exchanging for a token")
            credentials = exchange_code(
                answer.code,
                verifier,
                client_id,
                client_secret,
                redirect_uri_for(self.base_url),
            )
            self._say("YouTube access restored")
            return credentials

        raise RemoteAuthError(
            "Timed out waiting for authorisation — nobody pressed Authorise"
        )
