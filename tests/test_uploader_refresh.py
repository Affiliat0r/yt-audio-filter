"""The worker uploads unattended, so an expired access token must refresh
silently rather than opening a browser consent window on the worker's machine.

Google access tokens last ~1 hour; refresh tokens last months. Falling through
to ``InstalledAppFlow.run_local_server()`` on every expiry meant any upload
more than an hour after the last sign-in blocked on a human — and the window
appears wherever the worker runs, not where the user pressed Upload.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from yt_audio_filter import uploader

# Mirrors the scope list inside get_authenticated_service(); it is a local
# there, not a module constant, so it cannot be imported.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def _creds(*, valid: bool, refresh_token: str | None, scopes=None):
    c = MagicMock()
    c.valid = valid
    c.refresh_token = refresh_token
    c.scopes = scopes if scopes is not None else list(SCOPES)
    return c


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    d = tmp_path / ".yt-audio-filter"
    d.mkdir()
    f = d / "oauth_token.pickle"
    monkeypatch.setattr(uploader, "CREDENTIALS_DIR", d)
    monkeypatch.setattr(uploader, "OAUTH_TOKEN_FILE", f)
    return f


def test_expired_token_refreshes_without_opening_a_browser(token_file):
    stale = _creds(valid=False, refresh_token="rt-abc")

    def refresh(_request):
        stale.valid = True

    stale.refresh.side_effect = refresh
    token_file.write_bytes(b"stub")  # content unused; uploader.pickle is patched

    with patch.object(uploader, "pickle") as pk, patch(
        "google_auth_oauthlib.flow.InstalledAppFlow"
    ) as flow:
        pk.load.return_value = stale
        pk.dump = MagicMock()
        uploader.authenticate_youtube()

    stale.refresh.assert_called_once()
    flow.from_client_secrets_file.assert_not_called()  # no browser
    pk.dump.assert_called()  # refreshed token persisted


def test_refresh_failure_falls_back_to_interactive_consent(token_file):
    """Refresh tokens do die — revoked access, or an OAuth app still in
    Testing mode, where they expire after 7 days."""
    dead = _creds(valid=False, refresh_token="rt-dead")
    dead.refresh.side_effect = RuntimeError("invalid_grant")
    token_file.write_bytes(b"stub")  # content unused; uploader.pickle is patched

    with patch.object(uploader, "pickle") as pk, patch.object(
        uploader, "check_credentials_configured", return_value=True
    ), patch("google_auth_oauthlib.flow.InstalledAppFlow") as flow:
        pk.load.return_value = dead
        pk.dump = MagicMock()
        flow.from_client_secrets_file.return_value.run_local_server.return_value = _creds(
            valid=True, refresh_token="rt-new"
        )
        uploader.authenticate_youtube()

    flow.from_client_secrets_file.assert_called_once()


def test_valid_token_neither_refreshes_nor_prompts(token_file):
    good = _creds(valid=True, refresh_token="rt-abc")
    token_file.write_bytes(b"stub")  # content unused; uploader.pickle is patched

    with patch.object(uploader, "pickle") as pk, patch(
        "google_auth_oauthlib.flow.InstalledAppFlow"
    ) as flow:
        pk.load.return_value = good
        uploader.authenticate_youtube()

    good.refresh.assert_not_called()
    flow.from_client_secrets_file.assert_not_called()


def test_token_without_refresh_token_goes_straight_to_consent(token_file):
    """No refresh token means nothing to refresh with; don't pretend."""
    partial = _creds(valid=False, refresh_token=None)
    token_file.write_bytes(b"stub")  # content unused; uploader.pickle is patched

    with patch.object(uploader, "pickle") as pk, patch.object(
        uploader, "check_credentials_configured", return_value=True
    ), patch("google_auth_oauthlib.flow.InstalledAppFlow") as flow:
        pk.load.return_value = partial
        pk.dump = MagicMock()
        flow.from_client_secrets_file.return_value.run_local_server.return_value = _creds(
            valid=True, refresh_token="rt-new"
        )
        uploader.authenticate_youtube()

    partial.refresh.assert_not_called()
    flow.from_client_secrets_file.assert_called_once()
