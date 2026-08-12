"""Worker configuration: environment variables plus an optional ``worker/.env``.

The parser here is intentionally ~30 lines rather than a `python-dotenv`
dependency — the repo's install story is already long enough. Real process
environment always wins over the file, matching dotenv semantics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

from .discovery import DEFAULT_DISCOVERY_PORT
from .identity import DEFAULT_WORKER_ID_PATH, resolve_worker_id

#: Location of the optional key=value file, next to this module.
ENV_FILE = Path(__file__).resolve().parent / ".env"

DEFAULT_POLL_SECONDS = 5.0
DEFAULT_CACHE_DIR = "cache"


class WorkerConfigError(RuntimeError):
    """A required setting is missing or unusable."""


@dataclass(frozen=True)
class WorkerConfig:
    """Everything the worker needs to talk to the Studio and the local disk."""

    base_url: str
    token: str
    blob_token: Optional[str] = None
    poll_seconds: float = DEFAULT_POLL_SECONDS
    cache_dir: Path = Path(DEFAULT_CACHE_DIR)
    #: Vercel automation-bypass secret. Only needed when the target deployment
    #: has Deployment Protection enabled; ignored otherwise.
    bypass_secret: Optional[str] = None
    #: Stable identity of this machine. Sent on every claim so the Studio can
    #: route a job to one specific laptop; see ``worker/identity.py``.
    worker_id: str = ""
    #: Port of the loopback ``/whoami`` endpoint the browser probes.
    discovery_port: int = DEFAULT_DISCOVERY_PORT

    @property
    def blob_enabled(self) -> bool:
        return bool(self.blob_token)


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a minimal ``KEY=value`` file.

    Supports ``#`` comments, blank lines, an optional ``export`` prefix, and
    single- or double-quoted values. Anything else is left verbatim; there is
    no interpolation, on purpose.
    """
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _lookup(
    key: str, env: Mapping[str, str], file_values: Mapping[str, str]
) -> Optional[str]:
    """Process env first, then the .env file. Blank strings count as unset."""
    for source in (env, file_values):
        value = source.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def load_config(
    env: Optional[Mapping[str, str]] = None,
    env_file: Optional[Path] = None,
    worker_id_path: Optional[Path] = None,
) -> WorkerConfig:
    """Build a :class:`WorkerConfig`, raising when a required value is absent.

    Args:
        env: Environment mapping to read (defaults to ``os.environ``).
        env_file: Override for the ``.env`` path (defaults to ``worker/.env``).
        worker_id_path: Override for the persisted worker-id file (defaults to
            ``worker/state/worker_id.txt``).

    Raises:
        WorkerConfigError: When ``STUDIO_BASE_URL`` or ``WORKER_TOKEN`` is
            missing, ``WORKER_POLL_SECONDS`` is not a positive number, or
            ``WORKER_DISCOVERY_PORT`` is not a valid port.
    """
    env = os.environ if env is None else env
    file_values = parse_env_file(ENV_FILE if env_file is None else Path(env_file))

    base_url = _lookup("STUDIO_BASE_URL", env, file_values)
    if not base_url:
        raise WorkerConfigError(
            "STUDIO_BASE_URL is not set. Point it at your Vercel deployment, "
            f"e.g. https://quran-studio.vercel.app (see {ENV_FILE.name}.example)."
        )

    token = _lookup("WORKER_TOKEN", env, file_values)
    if not token:
        raise WorkerConfigError(
            "WORKER_TOKEN is not set. It must match the WORKER_TOKEN environment "
            "variable configured on the Vercel project."
        )

    poll_raw = _lookup("WORKER_POLL_SECONDS", env, file_values)
    poll_seconds = DEFAULT_POLL_SECONDS
    if poll_raw is not None:
        try:
            poll_seconds = float(poll_raw)
        except ValueError as exc:
            raise WorkerConfigError(
                f"WORKER_POLL_SECONDS must be a number, got {poll_raw!r}"
            ) from exc
        if poll_seconds <= 0:
            raise WorkerConfigError(
                f"WORKER_POLL_SECONDS must be > 0, got {poll_seconds}"
            )

    cache_dir = _lookup("WORKER_CACHE_DIR", env, file_values) or DEFAULT_CACHE_DIR

    port_raw = _lookup("WORKER_DISCOVERY_PORT", env, file_values)
    discovery_port = DEFAULT_DISCOVERY_PORT
    if port_raw is not None:
        try:
            discovery_port = int(port_raw)
        except ValueError as exc:
            raise WorkerConfigError(
                f"WORKER_DISCOVERY_PORT must be an integer, got {port_raw!r}"
            ) from exc
        if not 1 <= discovery_port <= 65535:
            raise WorkerConfigError(
                f"WORKER_DISCOVERY_PORT must be 1-65535, got {discovery_port}"
            )

    return WorkerConfig(
        base_url=base_url.rstrip("/"),
        token=token,
        blob_token=_lookup("BLOB_READ_WRITE_TOKEN", env, file_values),
        poll_seconds=poll_seconds,
        cache_dir=Path(cache_dir),
        bypass_secret=_lookup("VERCEL_AUTOMATION_BYPASS_SECRET", env, file_values),
        worker_id=resolve_worker_id(
            _lookup("WORKER_ID", env, file_values),
            path=DEFAULT_WORKER_ID_PATH if worker_id_path is None else Path(worker_id_path),
        ),
        discovery_port=discovery_port,
    )


def load_blob_token(
    env: Optional[Mapping[str, str]] = None,
    env_file: Optional[Path] = None,
) -> Optional[str]:
    """Read just ``BLOB_READ_WRITE_TOKEN``.

    ``--selftest-blob`` uses this so the token can be verified without a
    Studio URL or worker token configured yet.
    """
    env = os.environ if env is None else env
    file_values = parse_env_file(ENV_FILE if env_file is None else Path(env_file))
    return _lookup("BLOB_READ_WRITE_TOKEN", env, file_values)
