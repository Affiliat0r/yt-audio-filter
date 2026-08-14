"""HTTP client for the Studio worker API (``/api/worker/*``).

Four endpoints, all authenticated with the static ``x-worker-token`` header:

===========================  ======  ==========================================
``POST /api/worker/claim``   claim   heartbeat in, ``{job: Job|null}`` out
``POST /api/worker/progress``update  ``{cancelled: bool}`` out — honour it
``POST /api/worker/complete``finish  terminal state, ok / error
``POST /api/worker/catalog`` push    the scraped cartoon catalog
===========================  ======  ==========================================
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from yt_audio_filter.logger import get_logger

from .contract import Job, JobResult, WorkerHeartbeat

logger = get_logger()

#: (connect, read) timeouts. The read side is generous because Vercel cold
#: starts on an idle deployment can take a few seconds.
DEFAULT_TIMEOUT = (10, 60)


class StudioHTTPError(RuntimeError):
    """A Studio endpoint returned a non-2xx response."""

    def __init__(self, message: str, status_code: int = 0, body: str = ""):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body


class StudioClient:
    """Thin wrapper over ``requests.Session`` for the worker API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        session: Optional[requests.Session] = None,
        timeout: Any = DEFAULT_TIMEOUT,
        bypass_secret: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.bypass_secret = bypass_secret or None
        self.session = session or requests.Session()

    # ------------------------------------------------------------------ core

    def _headers(self) -> Dict[str, str]:
        headers = {"x-worker-token": self.token}
        # Vercel Deployment Protection intercepts requests with an SSO redirect
        # before they ever reach our routes. The automation-bypass secret is the
        # documented way for a non-browser client to get through; harmless when
        # the target deployment is not protected.
        if self.bypass_secret:
            headers["x-vercel-protection-bypass"] = self.bypass_secret
        return headers

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = self.session.post(
            url,
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        status = getattr(response, "status_code", 0)
        if not 200 <= status < 300:
            body = ""
            try:
                body = response.text[:500]
            except Exception:  # noqa: BLE001 - diagnostics only
                pass
            raise StudioHTTPError(f"POST {path} failed with HTTP {status}", status, body)
        try:
            data = response.json()
        except ValueError as exc:
            raise StudioHTTPError(f"POST {path} returned non-JSON body", status) from exc
        return data if isinstance(data, dict) else {}

    # --------------------------------------------------------------- actions

    def claim(self, heartbeat: WorkerHeartbeat) -> Optional[Job]:
        """Claim the next queued job, or return ``None`` when idle.

        The heartbeat body carries this machine's ``workerId``; the Studio uses
        it to skip jobs pinned to a *different* worker, so a laptop only ever
        gets handed the work meant for it (plus untargeted work).
        """
        data = self._post("/api/worker/claim", heartbeat.to_json())
        raw_job = data.get("job")
        if not raw_job:
            return None
        try:
            return Job.from_json(raw_job)
        except Exception as exc:  # noqa: BLE001 - malformed or too-new job
            # The Studio deploys on push while this worker is updated by hand,
            # so it WILL be handed job kinds it does not know. That must fail
            # the job, not the poll loop: letting this escape looks like a
            # transport error to run_forever, which backs off to a minute while
            # the server has already marked the job `claimed` — stranding it
            # forever and starving whatever was queued behind it.
            job_id = raw_job.get("id") if isinstance(raw_job, dict) else None
            kind = raw_job.get("kind") if isinstance(raw_job, dict) else None
            logger.warning("Rejecting unparseable job %s (%s): %s", job_id, kind, exc)
            if job_id:
                try:
                    self.complete(
                        job_id,
                        ok=False,
                        error=f"This worker cannot run job kind {kind!r}",
                        error_details=(
                            "The Studio is newer than this worker. Update it with "
                            "`git pull` and restart, or target a different machine."
                        ),
                    )
                except Exception:  # noqa: BLE001 - best effort; never stall the loop
                    logger.warning("Could not report the unparseable job as failed")
            return None

    def progress(
        self,
        job_id: str,
        stage: str,
        percent: Optional[int] = None,
        log_lines: Optional[List[str]] = None,
    ) -> bool:
        """Report progress. Returns True when the user cancelled the job."""
        payload: Dict[str, Any] = {"jobId": job_id, "stage": stage, "percent": percent}
        if log_lines:
            payload["logLines"] = list(log_lines)
        data = self._post("/api/worker/progress", payload)
        return bool(data.get("cancelled"))

    def complete(
        self,
        job_id: str,
        ok: bool,
        result: Optional[JobResult] = None,
        error: Optional[str] = None,
        error_details: Optional[str] = None,
    ) -> None:
        """Move the job to a terminal state (``done`` or ``error``)."""
        payload: Dict[str, Any] = {"jobId": job_id, "ok": ok}
        if result is not None:
            payload["result"] = result.to_json()
        if error is not None:
            payload["error"] = error
        if error_details is not None:
            payload["errorDetails"] = error_details
        self._post("/api/worker/complete", payload)

    def push_catalog(
        self, videos: List[Dict[str, Any]], worker_id: Optional[str] = None
    ) -> int:
        """Replace the Studio's cached cartoon catalog. Returns the count.

        ``worker_id`` scopes the per-video ``cacheState`` to this machine. The
        video list is identical everywhere, but "already downloaded" is a fact
        about one disk — without this, a second worker with an empty cache
        marked every video as uncached for every machine.
        """
        payload: Dict[str, Any] = {"videos": videos}
        if worker_id:
            payload["workerId"] = worker_id
        data = self._post("/api/worker/catalog", payload)
        count = data.get("count")
        return int(count) if isinstance(count, int) else len(videos)
