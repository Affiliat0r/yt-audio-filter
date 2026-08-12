"""Stable per-machine identity for the worker.

Several laptops can now run their own worker against the same Studio, so a
job has to be routable to one of them. That only works if a worker answers to
the *same* id after a restart: the browser stores the id it discovered on this
machine, and a worker that renamed itself on every launch would strand every
targeted job.

The id is resolved in three steps, first hit wins:

1. ``WORKER_ID`` from the environment or ``worker/.env`` — an explicit, human
   readable name ("hasan-desktop") always beats anything derived.
2. ``worker/state/worker_id.txt`` — whatever this machine used last time.
3. Derivation from ``<sanitised-hostname>-<8 hex chars>``, where the hash
   covers the hostname plus a durable machine fingerprint (Windows
   ``MachineGuid`` / ``/etc/machine-id`` / MAC address). Never a timestamp,
   never ``uuid4()``.

Step 3 writes its result to step 2's file, so a later hostname change, a new
network card, or a re-imaged registry key cannot silently rename the worker.
"""

from __future__ import annotations

import hashlib
import re
import socket
import sys
import uuid
from pathlib import Path
from typing import Optional

from yt_audio_filter.logger import get_logger

logger = get_logger()

#: Same ``worker/state/`` directory the rendered-job sidecar uses (gitignored).
DEFAULT_STATE_DIR = Path(__file__).resolve().parent / "state"
DEFAULT_WORKER_ID_PATH = DEFAULT_STATE_DIR / "worker_id.txt"

#: Length of the hex suffix. 8 chars ≈ 4 billion values; collisions between the
#: handful of laptops in one household are not a real risk, and the id stays
#: short enough to read in a log line.
HASH_LENGTH = 8

#: Keep the readable half bounded so the whole id fits a UI badge.
MAX_HOSTNAME_CHARS = 32

FALLBACK_HOSTNAME = "worker"

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def sanitise_hostname(hostname: str) -> str:
    """Lowercase a hostname down to ``[a-z0-9-]``, bounded in length.

    ``DESKTOP-8FQ1J2.local`` becomes ``desktop-8fq1j2-local``. An empty or
    entirely non-alphanumeric name falls back to ``worker`` so the id always
    has a readable half.
    """
    slug = _NON_SLUG.sub("-", str(hostname or "").strip().lower()).strip("-")
    if not slug:
        return FALLBACK_HOSTNAME
    return slug[:MAX_HOSTNAME_CHARS].strip("-") or FALLBACK_HOSTNAME


def _windows_machine_guid() -> Optional[str]:
    """``HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid``, or ``None``.

    Written at install time and stable for the life of the Windows install —
    the most durable per-machine value available without admin rights.
    """
    try:
        import winreg  # noqa: PLC0415 - Windows-only import
    except ImportError:  # pragma: no cover - non-Windows
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
    except OSError:  # pragma: no cover - key missing / access denied
        return None
    text = str(value).strip()
    return text or None


def _linux_machine_id() -> Optional[str]:
    """``/etc/machine-id`` (systemd) or the D-Bus equivalent."""
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return None


def machine_fingerprint() -> str:
    """A durable string identifying this physical machine.

    Falls back to the primary MAC address. ``uuid.getnode()`` invents a random
    (multicast-bit-set) value when it cannot read a real NIC — that would not be
    stable, but it only ever feeds the *first* derivation, which is immediately
    persisted, so the instability never reaches a second run.
    """
    if sys.platform == "win32":
        guid = _windows_machine_guid()
        if guid:
            return f"winguid:{guid}"
    else:
        machine_id = _linux_machine_id()
        if machine_id:
            return f"machineid:{machine_id}"

    node = uuid.getnode()
    if node >> 40 & 0x1:  # multicast bit -> uuid invented this address
        logger.debug("No stable machine id found; using a synthesised MAC.")
    return f"mac:{node:012x}"


def derive_worker_id(
    hostname: Optional[str] = None, fingerprint: Optional[str] = None
) -> str:
    """Build ``<sanitised-hostname>-<hash>`` from durable machine inputs.

    Deterministic: identical inputs always yield an identical id.
    """
    host = hostname if hostname is not None else socket.gethostname()
    mark = fingerprint if fingerprint is not None else machine_fingerprint()
    digest = hashlib.sha256(f"{host}|{mark}".encode("utf-8")).hexdigest()
    return f"{sanitise_hostname(host)}-{digest[:HASH_LENGTH]}"


def read_persisted_worker_id(
    path: Path = DEFAULT_WORKER_ID_PATH,
) -> Optional[str]:
    """Return the id recorded by a previous run, or ``None``."""
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def persist_worker_id(worker_id: str, path: Path = DEFAULT_WORKER_ID_PATH) -> bool:
    """Record ``worker_id`` for future runs. Returns False if it could not.

    Fail-soft: an unwritable state directory costs identity stability across a
    hostname change, not this run.
    """
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(f"{worker_id}\n", encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.warning("Could not persist the worker id to %s: %s", target, exc)
        return False
    return True


def resolve_worker_id(
    configured: Optional[str] = None,
    *,
    path: Path = DEFAULT_WORKER_ID_PATH,
    hostname: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> str:
    """Resolve this machine's worker id: configured, then persisted, then derived.

    Args:
        configured: Value of ``WORKER_ID``; blank or ``None`` means unset.
        path: Where the derived id is remembered (``worker/state/worker_id.txt``).
        hostname: Override for tests; defaults to ``socket.gethostname()``.
        fingerprint: Override for tests; defaults to :func:`machine_fingerprint`.
    """
    explicit = (configured or "").strip()
    if explicit:
        return explicit

    persisted = read_persisted_worker_id(path)
    if persisted:
        return persisted

    worker_id = derive_worker_id(hostname=hostname, fingerprint=fingerprint)
    persist_worker_id(worker_id, path)
    return worker_id
