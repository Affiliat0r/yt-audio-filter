"""The cookies refresher: what counts as signed in, and how the secret is written.

The browser half needs a real YouTube sign-in and is not exercised here; what
is tested is everything that decides whether a refresh overwrites the secret,
and that what it writes is something GitHub — and only GitHub — can open.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import refresh_youtube_cookies as refresh  # noqa: E402


def _cookie(name: str) -> dict:
    return {"name": name, "value": "v", "domain": ".youtube.com", "path": "/", "expires": -1}


def test_signed_in_needs_both_session_cookies() -> None:
    assert refresh.signed_in([_cookie("LOGIN_INFO"), _cookie("__Secure-3PSID")])
    assert not refresh.signed_in([_cookie("LOGIN_INFO")])
    assert not refresh.signed_in([_cookie("VISITOR_INFO1_LIVE"), _cookie("YSC")])


def test_the_environment_token_wins_over_the_stored_one(monkeypatch, tmp_path) -> None:
    stored = tmp_path / "token"
    stored.write_text("from-file")
    monkeypatch.setattr(refresh, "TOKEN_FILE", stored)
    monkeypatch.setenv(refresh.TOKEN_ENV, "from-env")
    assert refresh.github_token() == "from-env"
    monkeypatch.delenv(refresh.TOKEN_ENV)
    assert refresh.github_token() == "from-file"


def test_no_token_and_no_prompt_is_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(refresh, "TOKEN_FILE", tmp_path / "missing")
    monkeypatch.delenv(refresh.TOKEN_ENV, raising=False)
    assert refresh.github_token(prompt=False) is None


class _Response:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body or {}
        self.text = str(self._body)

    def json(self) -> dict:
        return self._body


class _FakeGitHub:
    """Hands out a real public key and records what was PUT with it."""

    def __init__(self, public_key_b64: str, put_status: int = 201):
        self.public_key_b64 = public_key_b64
        self.put_status = put_status
        self.puts: list = []

    def get(self, url, headers, timeout):
        assert url.endswith("/actions/secrets/public-key")
        return _Response(200, {"key_id": "kid-1", "key": self.public_key_b64})

    def put(self, url, headers, json, timeout):
        self.puts.append((url, json))
        return _Response(self.put_status)


def test_the_secret_is_sealed_to_the_repository_key() -> None:
    nacl = pytest.importorskip("nacl.public")
    from nacl import encoding

    private = nacl.PrivateKey.generate()
    public_b64 = private.public_key.encode(encoding.Base64Encoder()).decode()
    github = _FakeGitHub(public_b64)

    refresh.put_secret("owner/repo", "YTDLP_COOKIES", "cookie file", "tok", session=github)

    ((url, body),) = github.puts
    assert url.endswith("/repos/owner/repo/actions/secrets/YTDLP_COOKIES")
    assert body["key_id"] == "kid-1"
    opened = nacl.SealedBox(private).decrypt(base64.b64decode(body["encrypted_value"]))
    assert opened == b"cookie file"


def test_a_token_without_secrets_access_says_so() -> None:
    pytest.importorskip("nacl.public")
    from nacl import encoding, public

    key = public.PrivateKey.generate().public_key.encode(encoding.Base64Encoder()).decode()
    with pytest.raises(RuntimeError, match="Secrets: read and write"):
        refresh.put_secret("o/r", "S", "v", "tok", session=_FakeGitHub(key, put_status=403))


def test_an_oversized_secret_is_refused_before_anything_is_sent() -> None:
    github = _FakeGitHub("unused")
    with pytest.raises(ValueError, match="48 KB"):
        refresh.put_secret("o/r", "S", "x" * (refresh.MAX_SECRET_BYTES + 1), "t", session=github)
    assert github.puts == []
