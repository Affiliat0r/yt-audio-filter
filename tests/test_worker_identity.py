"""Tests for worker identity, job targeting, and the loopback discovery endpoint.

Three machines can now each run a worker, so a job has to be routable to one of
them. That rests on two things this file pins down:

* an id that is the *same* after a restart (``worker/identity.py``), because the
  browser stores it and a renamed worker strands every targeted job;
* a ``/whoami`` endpoint a public HTTPS page can actually reach
  (``worker/discovery.py``) — which is mostly a CORS problem, including
  Chrome's Private Network Access preflight.

No sockets are bound: the discovery handler is driven directly through a probe
that stands in for ``BaseHTTPRequestHandler``'s socket plumbing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

from worker.client import StudioClient
from worker.discovery import (
    DEFAULT_DISCOVERY_PORT,
    DISCOVERY_HOST,
    WhoAmIHandler,
    WorkerIdentity,
    make_handler,
    request_path,
    start_discovery,
)
from worker.identity import (
    derive_worker_id,
    persist_worker_id,
    read_persisted_worker_id,
    resolve_worker_id,
    sanitise_hostname,
)
from worker.worker import build_heartbeat


# ---------------------------------------------------------------------------
# Worker id derivation
# ---------------------------------------------------------------------------


def test_derived_id_is_hostname_plus_a_short_hash():
    worker_id = derive_worker_id(hostname="Hasan-Laptop", fingerprint="winguid:abc")

    prefix, _, suffix = worker_id.rpartition("-")
    assert prefix == "hasan-laptop"
    assert len(suffix) == 8
    assert all(c in "0123456789abcdef" for c in suffix)


def test_derivation_is_deterministic_for_the_same_machine():
    first = derive_worker_id(hostname="box", fingerprint="mac:aabbccddeeff")
    second = derive_worker_id(hostname="box", fingerprint="mac:aabbccddeeff")

    assert first == second


def test_two_machines_with_the_same_hostname_still_differ():
    # Two out-of-the-box laptops can genuinely share a hostname; the machine
    # fingerprint is what keeps their ids apart.
    a = derive_worker_id(hostname="DESKTOP-1", fingerprint="winguid:aaa")
    b = derive_worker_id(hostname="DESKTOP-1", fingerprint="winguid:bbb")

    assert a != b


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("DESKTOP-8FQ1J2.local", "desktop-8fq1j2-local"),
        ("hasan's laptop", "hasan-s-laptop"),
        ("  Box  ", "box"),
        ("", "worker"),
        ("...", "worker"),
        ("a" * 60, "a" * 32),
    ],
)
def test_hostname_sanitisation(raw, expected):
    assert sanitise_hostname(raw) == expected


# ---------------------------------------------------------------------------
# Worker id resolution + persistence
# ---------------------------------------------------------------------------


def test_resolved_id_is_stable_across_calls(tmp_path: Path):
    state = tmp_path / "worker_id.txt"

    first = resolve_worker_id(None, path=state, hostname="box", fingerprint="fp")
    second = resolve_worker_id(None, path=state, hostname="box", fingerprint="fp")

    assert first == second
    assert read_persisted_worker_id(state) == first


def test_persisted_id_survives_a_hostname_change(tmp_path: Path):
    """The whole point of persisting: a rename must not orphan targeted jobs."""
    state = tmp_path / "worker_id.txt"
    original = resolve_worker_id(None, path=state, hostname="old-name", fingerprint="fp")

    renamed = resolve_worker_id(None, path=state, hostname="brand-new-name", fingerprint="fp")

    assert renamed == original
    assert "old-name" in renamed


def test_persisted_id_survives_a_new_network_card(tmp_path: Path):
    state = tmp_path / "worker_id.txt"
    original = resolve_worker_id(None, path=state, hostname="box", fingerprint="mac:1")

    assert resolve_worker_id(None, path=state, hostname="box", fingerprint="mac:2") == original


def test_explicit_worker_id_wins_and_is_not_persisted(tmp_path: Path):
    state = tmp_path / "worker_id.txt"

    assert resolve_worker_id("  chosen-name  ", path=state, hostname="box") == "chosen-name"
    assert not state.exists()


def test_blank_worker_id_counts_as_unset(tmp_path: Path):
    state = tmp_path / "worker_id.txt"

    worker_id = resolve_worker_id("   ", path=state, hostname="box", fingerprint="fp")

    assert worker_id == derive_worker_id(hostname="box", fingerprint="fp")


def test_an_unwritable_state_dir_still_yields_an_id(tmp_path: Path):
    blocker = tmp_path / "state"
    blocker.write_text("not a directory", encoding="utf-8")

    worker_id = resolve_worker_id(
        None, path=blocker / "worker_id.txt", hostname="box", fingerprint="fp"
    )

    assert worker_id == derive_worker_id(hostname="box", fingerprint="fp")


def test_reading_an_absent_or_empty_state_file_is_none(tmp_path: Path):
    assert read_persisted_worker_id(tmp_path / "absent.txt") is None

    empty = tmp_path / "empty.txt"
    empty.write_text("  \n", encoding="utf-8")
    assert read_persisted_worker_id(empty) is None


def test_persist_worker_id_creates_the_state_directory(tmp_path: Path):
    target = tmp_path / "nested" / "worker_id.txt"

    assert persist_worker_id("box-12345678", target) is True
    assert read_persisted_worker_id(target) == "box-12345678"


# ---------------------------------------------------------------------------
# Claim heartbeat
# ---------------------------------------------------------------------------


class _FakeResponse:
    status_code = 200

    def json(self) -> Dict[str, Any]:
        return {"job": None}


class _FakeSession:
    """Records the request instead of making one."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()


def test_claim_body_carries_the_worker_id_alongside_the_token():
    session = _FakeSession()
    client = StudioClient(
        "https://studio.test", "tok", session=session, bypass_secret="bypass-secret"
    )

    with patch("worker.worker.detect_gpu", return_value=None):
        assert client.claim(build_heartbeat("laptop-a1b2c3d4")) is None

    call = session.calls[0]
    assert call["url"] == "https://studio.test/api/worker/claim"
    assert call["json"]["workerId"] == "laptop-a1b2c3d4"
    assert call["json"]["currentJobId"] is None
    # The pre-existing auth headers must be untouched.
    assert call["headers"]["x-worker-token"] == "tok"
    assert call["headers"]["x-vercel-protection-bypass"] == "bypass-secret"


def test_heartbeat_reports_the_job_currently_running():
    with patch("worker.worker.detect_gpu", return_value="RTX 4060"):
        beat = build_heartbeat("laptop-a1b2c3d4", "job-9")

    assert beat.to_json()["workerId"] == "laptop-a1b2c3d4"
    assert beat.to_json()["currentJobId"] == "job-9"


# ---------------------------------------------------------------------------
# Discovery handler — driven directly, no socket
# ---------------------------------------------------------------------------


IDENTITY = WorkerIdentity(
    worker_id="laptop-a1b2c3d4", hostname="Hasan-Laptop", gpu="RTX 4060", version="1.0.0"
)

STUDIO_ORIGIN = "https://quran-studio-mocha.vercel.app"


class HandlerProbe:
    """Runs one request through a handler without any socket.

    ``BaseHTTPRequestHandler.__init__`` would immediately try to read a request
    off a real connection, so the instance is created unconstructed and the four
    output methods the handler uses are replaced with recorders.
    """

    def __init__(self, path: str = "/whoami", headers: Optional[Dict[str, str]] = None):
        self.status: Optional[int] = None
        self.sent: List[Tuple[str, str]] = []
        self.chunks: List[bytes] = []

        handler = object.__new__(make_handler(IDENTITY))
        handler.path = path
        handler.headers = dict(headers or {})
        handler.send_response = self._send_response  # type: ignore[method-assign]
        handler.send_header = self._send_header  # type: ignore[method-assign]
        handler.end_headers = lambda: None  # type: ignore[method-assign]
        handler.wfile = self  # type: ignore[assignment]
        self.handler = handler

    # -- recorders standing in for the socket ----------------------------
    def _send_response(self, code: int, message: Optional[str] = None) -> None:
        self.status = code

    def _send_header(self, key: str, value: Any) -> None:
        self.sent.append((key, str(value)))

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    # -- drivers ---------------------------------------------------------
    def get(self) -> "HandlerProbe":
        self.handler.do_GET()
        return self

    def options(self) -> "HandlerProbe":
        self.handler.do_OPTIONS()
        return self

    @property
    def headers_out(self) -> Dict[str, str]:
        return {key.lower(): value for key, value in self.sent}

    @property
    def body(self) -> Dict[str, Any]:
        return json.loads(b"".join(self.chunks).decode("utf-8"))


def test_whoami_returns_exactly_the_four_public_fields():
    probe = HandlerProbe("/whoami", {"Origin": STUDIO_ORIGIN}).get()

    assert probe.status == 200
    assert probe.body == {
        "workerId": "laptop-a1b2c3d4",
        "hostname": "Hasan-Laptop",
        "gpu": "RTX 4060",
        "version": "1.0.0",
    }


def test_whoami_ignores_a_query_string_and_a_trailing_slash():
    assert HandlerProbe("/whoami/").get().status == 200
    assert HandlerProbe("/whoami?t=123").get().status == 200


def test_whoami_echoes_the_requesting_origin():
    headers = HandlerProbe("/whoami", {"Origin": STUDIO_ORIGIN}).get().headers_out

    assert headers["access-control-allow-origin"] == STUDIO_ORIGIN
    assert headers["vary"] == "Origin"
    assert headers["content-type"] == "application/json"


def test_whoami_falls_back_to_a_wildcard_origin():
    # curl / a same-origin probe sends no Origin at all.
    assert HandlerProbe("/whoami").get().headers_out["access-control-allow-origin"] == "*"


def test_preflight_grants_private_network_access():
    """Without this header Chrome refuses the https -> 127.0.0.1 fetch and the
    Studio can never detect the local worker."""
    probe = HandlerProbe(
        "/whoami",
        {
            "Origin": STUDIO_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        },
    ).options()

    headers = probe.headers_out
    assert probe.status == 204
    assert headers["access-control-allow-private-network"] == "true"
    assert headers["access-control-allow-origin"] == STUDIO_ORIGIN
    assert "GET" in headers["access-control-allow-methods"]
    assert "OPTIONS" in headers["access-control-allow-methods"]
    assert headers["access-control-allow-headers"] == "*"


def test_preflight_echoes_the_requested_headers():
    probe = HandlerProbe(
        "/whoami",
        {"Origin": STUDIO_ORIGIN, "Access-Control-Request-Headers": "content-type"},
    ).options()

    assert probe.headers_out["access-control-allow-headers"] == "content-type"


def test_the_actual_response_also_carries_the_private_network_header():
    headers = HandlerProbe("/whoami", {"Origin": STUDIO_ORIGIN}).get().headers_out

    assert headers["access-control-allow-private-network"] == "true"


@pytest.mark.parametrize("path", ["/", "/jobs", "/whoami/../etc/passwd", "/api/worker/claim"])
def test_every_other_path_is_a_404_that_leaks_nothing(path):
    probe = HandlerProbe(path, {"Origin": STUDIO_ORIGIN}).get()

    assert probe.status == 404
    assert "workerId" not in probe.body
    assert "laptop-a1b2c3d4" not in json.dumps(probe.body)


def test_only_get_and_options_are_implemented():
    # Anything else falls through to BaseHTTPRequestHandler's 501.
    assert not hasattr(WhoAmIHandler, "do_POST")
    assert not hasattr(WhoAmIHandler, "do_PUT")
    assert not hasattr(WhoAmIHandler, "do_DELETE")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/whoami", "/whoami"),
        ("/whoami/", "/whoami"),
        ("/whoami?x=1", "/whoami"),
        ("/", "/"),
        ("", "/"),
    ],
)
def test_request_path_normalisation(raw, expected):
    assert request_path(raw) == expected


def test_make_handler_binds_a_distinct_identity_per_server():
    other = WorkerIdentity(worker_id="other", hostname="other-box")

    assert make_handler(IDENTITY).identity.worker_id == "laptop-a1b2c3d4"
    assert make_handler(other).identity.worker_id == "other"


# ---------------------------------------------------------------------------
# Discovery startup
# ---------------------------------------------------------------------------


def test_discovery_binds_loopback_only():
    # The mocked server's serve_forever returns at once, so the worker thread
    # starts and exits without a socket ever existing.
    with patch("worker.discovery._DiscoveryServer") as fake_server:
        service = start_discovery(IDENTITY, DEFAULT_DISCOVERY_PORT)

    assert service is not None
    service.stop()

    assert fake_server.call_args.args[0] == ("127.0.0.1", 7717)
    assert DISCOVERY_HOST == "127.0.0.1"  # never 0.0.0.0, never LAN-reachable
    assert DEFAULT_DISCOVERY_PORT == 7717  # must match DISCOVERY_PORT in types.ts


def test_a_busy_port_disables_discovery_instead_of_killing_the_worker():
    with patch(
        "worker.discovery._DiscoveryServer",
        side_effect=OSError("address already in use"),
    ):
        assert start_discovery(IDENTITY, DEFAULT_DISCOVERY_PORT) is None
