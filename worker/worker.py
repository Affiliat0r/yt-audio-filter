"""Entry point for the Quran Studio render worker.

Run it from the repository root::

    python -m worker.worker           # or: python worker/worker.py
    worker\\run_worker.bat             # Windows convenience wrapper

The loop is deliberately boring: claim a job, run it, report, repeat. Network
errors back off and retry; a job that explodes is reported as a failure and
the loop continues. Nothing but Ctrl-C stops the worker.
"""

from __future__ import annotations

# Allow `python worker/worker.py` (no package context) as well as `-m`.
if __package__ in (None, ""):  # pragma: no cover - direct-script bootstrap
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    __package__ = "worker"

import argparse
import os
import socket
import subprocess
import threading
import time
import traceback
from functools import lru_cache
from typing import Optional, Sequence

from yt_audio_filter.exceptions import YTAudioFilterError
from yt_audio_filter.logger import get_logger, setup_logger
from yt_audio_filter.uploader import NONINTERACTIVE_ENV

from . import WORKER_VERSION, blob
from .catalog_sync import DEFAULT_INTERVAL_SECONDS, start_catalog_sync
from .client import StudioClient
from .config import WorkerConfig, WorkerConfigError, load_blob_token, load_config
from .contract import Job, JobResult, WorkerHeartbeat
from .discovery import WorkerIdentity, start_discovery
from .handlers import JobCancelled, JobContext, run_job
from .state import RenderedJobStore

logger = get_logger()

#: Network-error backoff ceiling. Vercel redeploys and home Wi-Fi both blip.
MAX_BACKOFF_SECONDS = 60.0


@lru_cache(maxsize=1)
def detect_gpu() -> Optional[str]:
    """Best-effort GPU name via ``nvidia-smi``.

    Deliberately not ``torch.cuda`` — importing torch here would add seconds
    to startup for a string the UI only shows in a status badge.
    """
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return lines[0] if lines else None


def _forbid_interactive_auth() -> None:
    """Guarantee no upload can pop a sign-in window on this machine.

    The worker runs unattended — often on a PC in another room from whoever
    pressed Upload. `InstalledAppFlow.run_local_server` would open a browser
    and block forever, freezing the single-job loop behind a prompt nobody can
    answer. With this set, authentication fails fast and says what to run here.
    """
    os.environ.setdefault(NONINTERACTIVE_ENV, "1")


def can_remove_music() -> bool:
    """Whether Demucs is installed here.

    A light worker (`pip install -e .`) has no PyTorch, so music removal is
    impossible on it. Advertising that in the heartbeat lets the Studio grey
    the option out instead of letting someone queue a job that can only fail.
    Checked with find_spec rather than a real import — importing torch costs
    seconds and megabytes, and this runs on every poll.
    """
    from importlib.util import find_spec

    try:
        return find_spec("torch") is not None and find_spec("demucs") is not None
    except (ImportError, ValueError):
        return False


def build_heartbeat(
    worker_id: str = "", current_job_id: Optional[str] = None
) -> WorkerHeartbeat:
    """The body of every ``/api/worker/claim`` call.

    ``worker_id`` is what lets the Studio hold a job back for one machine.
    """
    return WorkerHeartbeat(
        hostname=socket.gethostname(),
        gpu=detect_gpu(),
        version=WORKER_VERSION,
        worker_id=worker_id,
        current_job_id=current_job_id,
        can_remove_music=can_remove_music(),
    )


def error_fields(exc: BaseException) -> tuple[str, str]:
    """Split an exception into ``(error, errorDetails)`` for the Studio.

    ``YTAudioFilterError`` (and therefore ``OverlayError``) already carries
    the two-part message/details shape the UI renders; anything else gets a
    trimmed traceback so a surprise failure is still diagnosable.
    """
    if isinstance(exc, YTAudioFilterError):
        return exc.message, exc.details or type(exc).__name__
    message = str(exc) or type(exc).__name__
    return message, "".join(traceback.format_exception_only(type(exc), exc)).strip()


def _safe_complete(
    client: StudioClient,
    job_id: str,
    ok: bool,
    result: Optional[JobResult] = None,
    error: Optional[str] = None,
    error_details: Optional[str] = None,
) -> None:
    """Report a terminal state, retrying once — losing this strands the job."""
    for attempt in (1, 2):
        try:
            client.complete(job_id, ok, result, error, error_details)
            return
        except Exception as exc:  # noqa: BLE001 - reported below
            logger.warning(
                "Failed to report completion of %s (attempt %d): %s", job_id, attempt, exc
            )
            if attempt == 1:
                time.sleep(2.0)
    logger.error("Giving up on reporting completion of %s", job_id)


def wrong_worker_error(job: Job, worker_id: str) -> Optional[tuple[str, str]]:
    """``(error, details)`` when this job was routed to a different machine.

    The Studio is the one that filters on ``targetWorkerId``; this is a
    backstop. Running the job anyway would render on the wrong laptop —
    possibly one without a GPU — and nobody would ever notice, so a routing bug
    is turned into a visible job failure instead.
    """
    target = job.target_worker_id
    if not target or target == worker_id:
        return None
    return (
        f"Job is targeted at worker {target!r}, but this worker is {worker_id!r}",
        "The Studio handed out a job pinned to another machine. Nothing was "
        "rendered. Re-queue it from the Studio while that machine's worker is "
        "online, or clear the target.",
    )


def execute_job(
    job: Job, cfg: WorkerConfig, client: StudioClient, store: RenderedJobStore
) -> None:
    """Run one job to a terminal state. Never raises for job-level failures."""
    mismatch = wrong_worker_error(job, cfg.worker_id)
    if mismatch is not None:
        message, details = mismatch
        logger.warning("Refusing job %s: %s", job.id, message)
        _safe_complete(client, job.id, False, error=message, error_details=details)
        return

    logger.info(
        "Job %s claimed: kind=%s upload_requested=%s", job.id, job.kind, job.upload_requested
    )
    ctx = JobContext(client, job.id)
    started = time.monotonic()
    try:
        result = run_job(job, ctx, cfg, store)
    except JobCancelled:
        # The Studio already moved the job to `cancelled`; nothing to report.
        logger.warning("Job %s cancelled by the user", job.id)
        return
    except Exception as exc:  # noqa: BLE001 - every failure is a job failure
        message, details = error_fields(exc)
        logger.error("Job %s failed: %s", job.id, message)
        logger.debug("Job %s traceback:\n%s", job.id, traceback.format_exc())
        _safe_complete(client, job.id, False, error=message, error_details=details)
        return

    elapsed = time.monotonic() - started
    logger.info("Job %s finished in %.1fs", job.id, elapsed)
    _safe_complete(client, job.id, True, result=result)


def run_forever(
    cfg: WorkerConfig,
    client: StudioClient,
    store: RenderedJobStore,
    *,
    stop: Optional[threading.Event] = None,
    once: bool = False,
) -> None:
    """Poll, dispatch, repeat. Only Ctrl-C (or ``stop``) ends this."""
    stop = stop or threading.Event()
    backoff = cfg.poll_seconds

    while not stop.is_set():
        try:
            job = client.claim(build_heartbeat(cfg.worker_id))
        except Exception as exc:  # noqa: BLE001 - transport hiccups are normal
            logger.warning("Claim failed (%s); retrying in %.0fs", exc, backoff)
            if stop.wait(backoff):
                return
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            continue

        backoff = cfg.poll_seconds

        if job is None:
            if once:
                return
            if stop.wait(cfg.poll_seconds):
                return
            continue

        try:
            execute_job(job, cfg, client, store)
        except Exception as exc:  # noqa: BLE001 - the loop outlives any job
            logger.exception("Unhandled error while running job %s: %s", job.id, exc)

        if once:
            return


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="quran-studio-worker",
        description=(
            "Local render worker: polls the Quran Studio on Vercel for jobs and "
            "runs them against the yt-audio-filter pipeline."
        ),
    )
    parser.add_argument(
        "--selftest-blob",
        action="store_true",
        help="Upload a tiny file to Vercel Blob, print the URL, and exit.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim and run at most one job, then exit (useful for smoke tests).",
    )
    parser.add_argument(
        "--no-catalog-sync",
        action="store_true",
        help="Skip the background cartoon-catalog push to the Studio.",
    )
    parser.add_argument(
        "--catalog-interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help="Seconds between catalog syncs (default: %(default)s).",
    )
    parser.add_argument(
        "--no-discovery",
        action="store_true",
        help=(
            "Do not serve the loopback /whoami endpoint. The Studio can then no "
            "longer detect that this machine's worker is the local one."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = _parse_args(argv)
    setup_logger(verbose=args.verbose)
    _forbid_interactive_auth()

    if args.selftest_blob:
        token = load_blob_token()
        if not token:
            logger.error(
                "BLOB_READ_WRITE_TOKEN is not set (checked the environment and "
                "worker/.env). Copy it from the Vercel project's Storage tab."
            )
            return 2
        try:
            url = blob.selftest(token)
        except Exception as exc:  # noqa: BLE001 - the message is the product
            logger.error("Blob self-test failed: %s", exc)
            return 1
        print(url)
        return 0

    try:
        cfg = load_config()
    except WorkerConfigError as exc:
        logger.error("%s", exc)
        return 2

    identity = WorkerIdentity(
        worker_id=cfg.worker_id,
        hostname=socket.gethostname(),
        gpu=detect_gpu(),
        version=WORKER_VERSION,
    )

    discovery = None if args.no_discovery else start_discovery(identity, cfg.discovery_port)
    if discovery is not None:
        discovery_status = f"listening on {discovery.host}:{discovery.port}"
    elif args.no_discovery:
        discovery_status = "off (--no-discovery)"
    else:
        discovery_status = f"unavailable (port {cfg.discovery_port} busy)"

    logger.info(
        "Worker %s starting: id=%s studio=%s cache=%s gpu=%s blob=%s discovery=%s",
        WORKER_VERSION,
        cfg.worker_id,
        cfg.base_url,
        cfg.cache_dir,
        identity.gpu or "none detected",
        "enabled" if cfg.blob_enabled else "disabled (no token)",
        discovery_status,
    )

    client = StudioClient(cfg.base_url, cfg.token, bypass_secret=cfg.bypass_secret)
    store = RenderedJobStore()
    stop = threading.Event()

    if not args.no_catalog_sync:
        start_catalog_sync(
            client,
            cfg.cache_dir,
            interval_seconds=args.catalog_interval,
            stop=stop,
            worker_id=cfg.worker_id,
        )

    try:
        run_forever(cfg, client, store, stop=stop, once=args.once)
    except KeyboardInterrupt:
        logger.info("Interrupted; shutting down.")
    finally:
        stop.set()
        if discovery is not None:
            discovery.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
