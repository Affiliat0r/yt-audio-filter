"""Sidecar map from job id to the rendered file on this machine.

The rendered file never leaves this machine. When the user later clicks
"Upload to YouTube", the job comes back with ``uploadRequested`` set and the
worker has to find the local MP4 again — possibly after a restart. That
mapping lives here as a small JSON file under ``worker/state/``.

Music-removal jobs additionally stash the source video's YouTube metadata,
because ``uploader.upload_to_youtube`` derives its SEO title/description/tags
from it and re-fetching would be a needless network round trip.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from yt_audio_filter.logger import get_logger
from yt_audio_filter.youtube import VideoMetadata

logger = get_logger()

DEFAULT_STATE_DIR = Path(__file__).resolve().parent / "state"
DEFAULT_STATE_PATH = DEFAULT_STATE_DIR / "rendered_jobs.json"

#: Keep the file bounded; oldest entries fall off. Jobs expire on the Studio
#: side long before this many renders pile up.
MAX_ENTRIES = 250


def video_metadata_to_json(meta: VideoMetadata) -> Dict[str, Any]:
    """Flatten a ``VideoMetadata`` for the sidecar file."""
    return {
        "videoId": meta.video_id,
        "title": meta.title,
        "description": meta.description,
        "channel": meta.channel,
        "tags": list(meta.tags),
        "duration": int(meta.duration),
        "viewCount": int(meta.view_count),
        "filePath": str(meta.file_path),
    }


def video_metadata_from_json(raw: Dict[str, Any]) -> VideoMetadata:
    """Rebuild a ``VideoMetadata`` from the sidecar file."""
    return VideoMetadata(
        video_id=str(raw.get("videoId") or ""),
        title=str(raw.get("title") or ""),
        description=str(raw.get("description") or ""),
        channel=str(raw.get("channel") or "Unknown"),
        tags=[str(t) for t in (raw.get("tags") or [])],
        duration=int(raw.get("duration") or 0),
        view_count=int(raw.get("viewCount") or 0),
        file_path=Path(str(raw.get("filePath") or "")),
    )


@dataclass(frozen=True)
class RenderedJob:
    """One recorded render output."""

    job_id: str
    path: Path
    kind: str
    recorded_at: float
    video_metadata: Optional[VideoMetadata] = None


class RenderedJobStore:
    """JSON-backed ``job_id -> rendered file`` map, written atomically."""

    def __init__(self, path: Path = DEFAULT_STATE_PATH) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------ io

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Rendered-job state unreadable (%s): %s", self.path, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # -------------------------------------------------------------- public

    def record(
        self,
        job_id: str,
        path: Path,
        kind: str,
        video_metadata: Optional[VideoMetadata] = None,
    ) -> None:
        """Remember where a job's output landed, trimming the oldest entries."""
        data = self._read()
        data[job_id] = {
            "path": str(Path(path).resolve()),
            "kind": kind,
            "recordedAt": time.time(),
            "videoMetadata": (
                video_metadata_to_json(video_metadata) if video_metadata else None
            ),
        }
        if len(data) > MAX_ENTRIES:
            ordered: List[str] = sorted(
                data, key=lambda k: float(data[k].get("recordedAt") or 0.0)
            )
            for stale in ordered[: len(data) - MAX_ENTRIES]:
                data.pop(stale, None)
        try:
            self._write(data)
        except OSError as exc:
            # Losing the sidecar costs a later re-render, not this render.
            logger.warning("Could not persist rendered-job state: %s", exc)

    def lookup(self, job_id: str) -> Optional[RenderedJob]:
        """Return the recorded render for ``job_id``, or ``None``."""
        entry = self._read().get(job_id)
        if not isinstance(entry, dict):
            return None
        raw_path = entry.get("path")
        if not raw_path:
            return None
        raw_meta = entry.get("videoMetadata")
        return RenderedJob(
            job_id=job_id,
            path=Path(str(raw_path)),
            kind=str(entry.get("kind") or ""),
            recorded_at=float(entry.get("recordedAt") or 0.0),
            video_metadata=(
                video_metadata_from_json(raw_meta) if isinstance(raw_meta, dict) else None
            ),
        )
