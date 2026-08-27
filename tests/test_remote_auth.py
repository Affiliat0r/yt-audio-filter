"""Remote YouTube authorisation — the worker half.

Consent is routed through the Studio so it can be granted from a phone. The
property that makes relaying an authorisation code acceptable at all is that
the PKCE verifier and the client secret never leave this machine, so the code
sitting briefly in the Studio's Redis cannot be redeemed by anyone who reads it.
"""

from __future__ import annotations

import base64
import hashlib
import json
from unittest.mock import MagicMock

import pytest

from worker.contract import AuthCodeResponse, AuthRequestAck
from worker.remote_auth import (
    RemoteAuthError,
    RemoteAuthorizer,
    exchange_code,
    load_web_client,
    make_pkce_pair,
    redirect_uri_for,
)


# ------------------------------------------------------------------- PKCE


def test_pkce_challenge_matches_rfc7636_s256() -> None:
    verifier, challenge = make_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert challenge == expected


def test_pkce_challenge_is_unpadded() -> None:
    """Padding left on is the classic cause of invalid_grant at the exchange —
    discovered long after the user has already approved."""
    _, challenge = make_pkce_pair()
    assert "=" not in challenge


def test_pkce_verifier_is_within_the_legal_length() -> None:
    verifier, _ = make_pkce_pair()
    assert 43 <= len(verifier) <= 128


def test_pkce_pairs_are_unique() -> None:
    assert make_pkce_pair()[0] != make_pkce_pair()[0]


# --------------------------------------------------------------- redirect


def test_redirect_uri_matches_the_studio_default() -> None:
    assert (
        redirect_uri_for("https://studio.example", env={})
        == "https://studio.example/api/auth/youtube/callback"
    )


def test_redirect_uri_tolerates_a_trailing_slash() -> None:
    assert (
        redirect_uri_for("https://studio.example/", env={})
        == "https://studio.example/api/auth/youtube/callback"
    )


def test_redirect_uri_env_override_wins() -> None:
    """Mirrors the Studio's own override so the two cannot disagree — Google
    compares the redirect byte for byte."""
    got = redirect_uri_for(
        "https://studio.example",
        env={"GOOGLE_OAUTH_REDIRECT_URI": "https://other.example/cb"},
    )
    assert got == "https://other.example/cb"


# ------------------------------------------------------------- web client


def test_web_client_read_from_json(tmp_path) -> None:
    p = tmp_path / "client_secrets_web.json"
    p.write_text(json.dumps({"web": {"client_id": "cid", "client_secret": "sec"}}))
    assert load_web_client(p, env={}) == ("cid", "sec")


def test_web_client_falls_back_to_env(tmp_path) -> None:
    got = load_web_client(
        tmp_path / "absent.json",
        env={"GOOGLE_OAUTH_CLIENT_ID": "cid", "GOOGLE_OAUTH_CLIENT_SECRET": "sec"},
    )
    assert got == ("cid", "sec")


def test_web_client_absent_is_not_an_error(tmp_path) -> None:
    """Remote auth is opt-in; absence means fall back, not fail."""
    assert load_web_client(tmp_path / "absent.json", env={}) is None


def test_web_client_ignores_a_half_filled_file(tmp_path) -> None:
    p = tmp_path / "client_secrets_web.json"
    p.write_text(json.dumps({"web": {"client_id": "cid"}}))
    assert load_web_client(p, env={}) is None


# --------------------------------------------------------------- exchange


def _token_response(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else {}
    return r


def test_exchange_sends_the_verifier_and_secret() -> None:
    session = MagicMock()
    session.post.return_value = _token_response(
        200, {"access_token": "at", "refresh_token": "rt"}
    )

    creds = exchange_code(
        "code", "verifier", "cid", "sec", "https://s/cb", session=session
    )

    sent = session.post.call_args.kwargs["data"]
    assert sent["code_verifier"] == "verifier"
    assert sent["client_secret"] == "sec"
    assert sent["grant_type"] == "authorization_code"
    assert creds.refresh_token == "rt"


def test_exchange_refuses_a_response_without_a_refresh_token() -> None:
    """Without one the credentials die in an hour and the whole dance repeats."""
    session = MagicMock()
    session.post.return_value = _token_response(200, {"access_token": "at"})

    with pytest.raises(RemoteAuthError, match="no refresh token"):
        exchange_code("c", "v", "cid", "sec", "https://s/cb", session=session)


def test_exchange_surfaces_googles_error() -> None:
    session = MagicMock()
    session.post.return_value = _token_response(
        400, {"error": "invalid_grant", "error_description": "Bad code"}
    )

    with pytest.raises(RemoteAuthError, match="invalid_grant"):
        exchange_code("c", "v", "cid", "sec", "https://s/cb", session=session)


# -------------------------------------------------------- the orchestration


def _authorizer(client, now=None, timeout_seconds=30, **kw):
    return RemoteAuthorizer(
        client,
        "https://studio.example",
        "w-1",
        "HOSTPC",
        timeout_seconds=timeout_seconds,
        poll_seconds=0,
        sleep=lambda _s: None,
        now=now or (lambda: 0.0),
        **kw,
    )


def test_unconfigured_returns_none_rather_than_raising(monkeypatch) -> None:
    """The caller then falls back to its existing, more actionable error."""
    monkeypatch.setattr("worker.remote_auth.load_web_client", lambda *a, **k: None)
    client = MagicMock()
    assert _authorizer(client)() is None
    client.request_auth.assert_not_called()


def test_happy_path_registers_polls_and_exchanges(monkeypatch) -> None:
    monkeypatch.setattr(
        "worker.remote_auth.load_web_client", lambda *a, **k: ("cid", "sec")
    )
    exchanged = {}

    def fake_exchange(code, verifier, cid, sec, redirect, **kw):
        exchanged.update(code=code, verifier=verifier, redirect=redirect)
        return "CREDS"

    monkeypatch.setattr("worker.remote_auth.exchange_code", fake_exchange)

    client = MagicMock()
    client.request_auth.return_value = AuthRequestAck(
        authorize_url="https://s/start", expires_at=0
    )
    client.fetch_auth_code.side_effect = [
        AuthCodeResponse(state="", status="pending"),
        AuthCodeResponse(state="", status="pending"),
        AuthCodeResponse(state="", status="ready", code="the-code"),
    ]

    assert _authorizer(client)() == "CREDS"

    # Only the public half of the PKCE pair was registered with the Studio.
    sent = client.request_auth.call_args[0][0]
    assert sent.code_challenge
    assert not hasattr(sent, "code_verifier")
    assert exchanged["code"] == "the-code"
    assert exchanged["redirect"] == "https://studio.example/api/auth/youtube/callback"


def test_a_mismatched_state_is_refused(monkeypatch) -> None:
    """Refusing a code minted for a different flow is what `state` is for."""
    monkeypatch.setattr(
        "worker.remote_auth.load_web_client", lambda *a, **k: ("cid", "sec")
    )
    monkeypatch.setattr("worker.remote_auth.exchange_code", lambda *a, **k: "CREDS")

    client = MagicMock()
    client.request_auth.return_value = AuthRequestAck(authorize_url="u")
    client.fetch_auth_code.return_value = AuthCodeResponse(
        state="somebody-elses", status="ready", code="c"
    )

    with pytest.raises(RemoteAuthError, match="state mismatch"):
        _authorizer(client)()


def test_expiry_stops_the_wait(monkeypatch) -> None:
    monkeypatch.setattr(
        "worker.remote_auth.load_web_client", lambda *a, **k: ("cid", "sec")
    )
    client = MagicMock()
    client.request_auth.return_value = AuthRequestAck(authorize_url="u")
    client.fetch_auth_code.return_value = AuthCodeResponse(status="expired")

    with pytest.raises(RemoteAuthError, match="expired"):
        _authorizer(client)()


def test_nobody_presses_authorise(monkeypatch) -> None:
    monkeypatch.setattr(
        "worker.remote_auth.load_web_client", lambda *a, **k: ("cid", "sec")
    )
    client = MagicMock()
    client.request_auth.return_value = AuthRequestAck(authorize_url="u")
    client.fetch_auth_code.return_value = AuthCodeResponse(status="pending")

    clock = iter([0.0, 1.0, 2.0, 999.0, 999.0, 999.0])
    with pytest.raises(RemoteAuthError, match="Timed out"):
        _authorizer(client, now=lambda: next(clock), timeout_seconds=10)()


def test_a_transient_poll_failure_does_not_abort(monkeypatch) -> None:
    """Vercel cold starts and flaky wifi must not cost the user the flow."""
    monkeypatch.setattr(
        "worker.remote_auth.load_web_client", lambda *a, **k: ("cid", "sec")
    )
    monkeypatch.setattr("worker.remote_auth.exchange_code", lambda *a, **k: "CREDS")

    client = MagicMock()
    client.request_auth.return_value = AuthRequestAck(authorize_url="u")
    client.fetch_auth_code.side_effect = [
        RuntimeError("connection reset"),
        AuthCodeResponse(status="ready", code="c"),
    ]

    assert _authorizer(client)() == "CREDS"


# ------------------------------------------------- Redis cost of idle polling


def test_idle_claim_omits_the_heartbeat() -> None:
    """An idle poll must cost one Redis command, not five.

    Two workers polling every 5s with a heartbeat on each exhausted a 500,000
    command monthly allowance in under three days, which takes the whole Studio
    down — not just the workers.
    """
    from worker.client import StudioClient
    from worker.contract import WorkerHeartbeat

    client = StudioClient("https://studio.example", "tok")
    sent = {}

    def fake_post(path, payload):
        sent[path] = payload
        return {"job": None}

    client._post = fake_post  # type: ignore[method-assign]
    beat = WorkerHeartbeat(hostname="pc", gpu="gpu", version="1", worker_id="w-1")

    client.claim(beat, include_heartbeat=False)
    body = sent["/api/worker/claim"]
    assert body == {"workerId": "w-1"}, "an idle poll should carry only the id"

    client.claim(beat, include_heartbeat=True)
    full = sent["/api/worker/claim"]
    assert full["hostname"] == "pc" and full["version"] == "1"
