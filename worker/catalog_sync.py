"""Push the local cartoon catalog to the Studio, on startup and on a timer.

Vercel has no yt-dlp and no disk, so the gallery it renders is whatever the
worker last uploaded. Each video carries a ``cacheState`` computed from the
worker's cache directory, which is what lets the UI mark a visual as
"already downloaded" or "already upscaled" — i.e. cheap to render.

Runs on a daemon thread so a slow channel scrape never delays a job.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, List, Optional

from yt_audio_filter import cartoon_catalog
from yt_audio_filter.logger import get_logger

from .client import StudioClient
from .contract import catalog_video_to_json

logger = get_logger()

DEFAULT_CHANNELS_PATH = Path("config/channels.json")
DEFAULT_INTERVAL_SECONDS = 30 * 60

_VIDEO_PREFIX = "video_"
_UPSCALED_PREFIX = "upscaled_"


def scan_cache_states(cache_dir: Path) -> Dict[str, str]:
    """Map ``video_id -> "downloaded" | "upscaled"`` from one directory scan.

    ``upscaled_<id>.mp4`` wins over ``video_<id>.<ext>``; ids absent from the
    result are "new". A single ``iterdir`` beats one glob per video — the
    catalog runs to a few hundred entries.
    """
    states: Dict[str, str] = {}
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return states

    try:
        entries = list(cache_dir.iterdir())
    except OSError as exc:
        logger.warning("Could not scan cache dir %s: %s", cache_dir, exc)
        return states

    for entry in entries:
        name = entry.name
        if name.endswith(".part") or name.endswith(".tmp"):
            continue
        if not entry.is_file():
            continue
        if name.startswith(_UPSCALED_PREFIX) and name.endswith(".mp4"):
            states[name[len(_UPSCALED_PREFIX) : -len(".mp4")]] = "upscaled"
        elif name.startswith(_VIDEO_PREFIX):
            video_id = Path(name).stem[len(_VIDEO_PREFIX) :]
            states.setdefault(video_id, "downloaded")
    return states


def cache_state_for(video_id: str, states: Dict[str, str]) -> str:
    """``"new"`` unless the cache scan found something for this id."""
    return states.get(video_id, "new")


def sync_catalog_once(
    client: StudioClient,
    cache_dir: Path,
    channels_path: Path = DEFAULT_CHANNELS_PATH,
    worker_id: Optional[str] = None,
) -> int:
    """Scrape (or read the cached) catalog and POST it to the Studio.

    Returns the number of videos accepted by the Studio.
    """
    channels = cartoon_catalog.load_channels(Path(channels_path))
    videos = cartoon_catalog.list_videos(channels=channels, cache_dir=Path(cache_dir))
    states = scan_cache_states(Path(cache_dir))
    payload: List[Dict] = [
        catalog_video_to_json(video, cache_state_for(video.video_id, states))
        for video in videos
    ]
    count = client.push_catalog(payload, worker_id=worker_id)
    logger.info(
        "Catalog sync: pushed %d video(s) across %d channel(s)", count, len(channels)
    )
    return count


def start_catalog_sync(
    client: StudioClient,
    cache_dir: Path,
    *,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    channels_path: Path = DEFAULT_CHANNELS_PATH,
    stop: Optional[threading.Event] = None,
    worker_id: Optional[str] = None,
) -> threading.Thread:
    """Start the background sync loop and return its (daemon) thread."""
    stop_event = stop or threading.Event()

    def _loop() -> None:
        while True:
            try:
                sync_catalog_once(client, cache_dir, channels_path, worker_id=worker_id)
            except Exception as exc:  # noqa: BLE001 - sync must never crash us
                logger.warning("Catalog sync failed: %s", exc)
            if stop_event.wait(interval_seconds):
                return

    thread = threading.Thread(target=_loop, name="catalog-sync", daemon=True)
    thread.start()
    return thread
