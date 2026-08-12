"""Loopback discovery endpoint — ``GET http://127.0.0.1:7717/whoami``.

The Studio runs on Vercel, so the browser has no way of knowing *which* of the
user's machines it is sitting in front of. This endpoint is the answer: the
page fetches ``http://127.0.0.1:7717/whoami``, and whatever answers is by
definition the worker on the same physical machine. The Studio then pins new
jobs to that ``workerId``.

That cross-origin fetch — an ``https://`` page reaching a loopback address —
is the entire reason this file is fiddlier than "return some JSON":

* ``Access-Control-Allow-Origin`` must echo the request's ``Origin`` (or be
  ``*``), or the browser discards the response.
* Chrome's **Private Network Access** rules preflight *any* public-page →
  private-address request and require
  ``Access-Control-Allow-Private-Network: true`` on the ``OPTIONS`` response.
  Without it Chrome fails the fetch with a network error and the whole feature
  silently does nothing.

Security posture: bound to ``127.0.0.1`` only (never ``0.0.0.0``), serves one
path, and returns four non-secret fields. No job control, no file access, no
tokens. Treat everything here as readable by any process on this machine.
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Type
from urllib.parse import urlsplit

from yt_audio_filter.logger import get_logger

logger = get_logger()

#: Must match ``DISCOVERY_PORT`` in ``web/lib/types.ts``.
DEFAULT_DISCOVERY_PORT = 7717

#: Loopback only. Binding 0.0.0.0 would expose the endpoint to the LAN.
DISCOVERY_HOST = "127.0.0.1"

WHOAMI_PATH = "/whoami"

ALLOWED_METHODS = "GET, OPTIONS"


@dataclass(frozen=True)
class WorkerIdentity:
    """Exactly what ``/whoami`` may reveal — nothing else belongs here."""

    worker_id: str
    hostname: str
    gpu: Optional[str] = None
    version: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "workerId": self.worker_id,
            "hostname": self.hostname,
            "gpu": self.gpu,
            "version": self.version,
        }


def request_path(raw_path: str) -> str:
    """Normalise a request target to a bare path.

    Strips the query string and any trailing slash so ``/whoami?x=1`` and
    ``/whoami/`` both match, while ``/`` stays ``/``.
    """
    path = urlsplit(raw_path or "/").path or "/"
    return path.rstrip("/") or "/"


class WhoAmIHandler(BaseHTTPRequestHandler):
    """Serves ``/whoami``; everything else is a 404.

    ``identity`` is overridden per-server by :func:`make_handler`.
    """

    identity: WorkerIdentity = WorkerIdentity(worker_id="", hostname="")

    server_version = "QuranStudioWorkerDiscovery"
    sys_version = ""

    # -------------------------------------------------------------- helpers

    def _send_cors_headers(self, *, preflight: bool) -> None:
        """CORS for a public HTTPS page talking to loopback.

        The private-network header goes on both responses: it is only *required*
        on the preflight, but Chrome versions differ on whether they also check
        the actual response, and it costs nothing.
        """
        origin = self.headers.get("Origin") if self.headers else None
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        if preflight:
            self.send_header("Access-Control-Allow-Methods", ALLOWED_METHODS)
            requested = (
                self.headers.get("Access-Control-Request-Headers") if self.headers else None
            )
            self.send_header("Access-Control-Allow-Headers", requested or "*")
            self.send_header("Access-Control-Max-Age", "600")

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers(preflight=False)
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------- handlers

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        """Preflight. Carries the Private Network Access grant."""
        self.send_response(204)
        self._send_cors_headers(preflight=True)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        if request_path(self.path) != WHOAMI_PATH:
            self._send_json(404, {"error": "not found"})
            return
        self._send_json(200, self.identity.to_json())

    # Any other verb falls through to BaseHTTPRequestHandler's 501.

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Route access logs to the app logger instead of stderr."""
        logger.debug("discovery: " + format, *args)


def make_handler(identity: WorkerIdentity) -> Type[WhoAmIHandler]:
    """Bind an identity to a fresh handler class (the API takes a class)."""
    return type("BoundWhoAmIHandler", (WhoAmIHandler,), {"identity": identity})


class _DiscoveryServer(ThreadingHTTPServer):
    daemon_threads = True

    # Platform-split on purpose. On Windows SO_REUSEADDR lets a second process
    # bind a port another process is *actively listening on*, so two workers on
    # one machine would both "start" and answer at random — exactly the
    # ambiguity this endpoint exists to resolve. Elsewhere SO_REUSEADDR only
    # skips the TIME_WAIT wait, which is what makes an immediate restart work.
    allow_reuse_address = sys.platform != "win32"


class DiscoveryService:
    """A running discovery endpoint plus the thread serving it."""

    def __init__(self, server: ThreadingHTTPServer, thread: threading.Thread) -> None:
        self._server = server
        self._thread = thread

    @property
    def host(self) -> str:
        return str(self._server.server_address[0])

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{WHOAMI_PATH}"

    def stop(self, timeout: float = 2.0) -> None:
        """Shut the endpoint down; safe to call twice."""
        try:
            self._server.shutdown()
        except Exception:  # noqa: BLE001 - shutdown is best-effort
            pass
        try:
            self._server.server_close()
        except Exception:  # noqa: BLE001 - ditto
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)


def start_discovery(
    identity: WorkerIdentity,
    port: int = DEFAULT_DISCOVERY_PORT,
    host: str = DISCOVERY_HOST,
) -> Optional[DiscoveryService]:
    """Start ``/whoami`` on a daemon thread, or return ``None`` if the port is taken.

    Fail-soft on purpose: a second worker on this machine, or an unrelated app
    already on 7717, must not stop the worker from doing its actual job. The
    only cost is that the browser cannot auto-target this machine.
    """
    try:
        server = _DiscoveryServer((host, port), make_handler(identity))
    except OSError as exc:
        logger.warning(
            "Local discovery endpoint disabled: cannot bind %s:%d (%s). Another "
            "worker or application already owns the port; set "
            "WORKER_DISCOVERY_PORT to move it.",
            host,
            port,
            exc,
        )
        return None

    thread = threading.Thread(
        target=server.serve_forever, name="worker-discovery", daemon=True
    )
    thread.start()
    service = DiscoveryService(server, thread)
    logger.info("Discovery endpoint listening on %s", service.url)
    return service
