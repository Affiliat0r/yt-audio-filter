"""Live YouTube keyword search for the cartoon gallery.

Returns ``CatalogVideo`` objects compatible with the curated-channel catalog,
so the same gallery grid renders both. Results are cached on disk per query
with a short TTL — independent of the 24h channel-catalog cache.

Selecting a search-result video in the UI persists it into the channel
catalog under the synthetic ``__search__`` channel slug; downstream
``list_videos()`` exposes those picks so ``overlay_pipeline._resolve_visual_video``
can find them like any curated entry.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

from .cartoon_catalog import (
    CATALOG_CACHE_FILENAME,
    CatalogVideo,
    DEFAULT_CACHE_DIR,
    _read_cache,
    _video_from_dict,
    _write_cache,
)
from .exceptions import OverlayError
from .logger import get_logger

logger = get_logger()

SEARCH_CHANNEL_SLUG = "__search__"
SEARCH_CACHE_FILENAME = "cartoon_search_cache.json"
DEFAULT_SEARCH_TTL_SECONDS = 3600  # 1h
DEFAULT_MAX_RESULTS = 25


def _search_cache_path(cache_dir: Path) -> Path:
    return cache_dir / SEARCH_CACHE_FILENAME


def _read_search_cache(cache_dir: Path) -> Dict:
    path = _search_cache_path(cache_dir)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Search cache unreadable ({path}): {e}; starting fresh")
        return {}


def _write_search_cache(cache_dir: Path, cache: Dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _search_cache_path(cache_dir)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def _query_key(query: str, max_results: int) -> str:
    return f"{max_results}::{query.strip().lower()}"


def _ydl_search(query: str, max_results: int) -> List[CatalogVideo]:
    """Run a yt-dlp ``ytsearchN:`` query and return CatalogVideo entries.

    Raises:
        OverlayError: if yt_dlp is missing or the query fails.
    """
    try:
        import yt_dlp  # type: ignore
    except ImportError as e:
        raise OverlayError(
            "yt-dlp is required for keyword search but is not installed",
            str(e),
        )

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        # Same workaround as download_stream / yt_metadata: skip the slow
        # bgutil Deno cold-start. Harmless if the plugin is not installed.
        "extractor_args": {
            "youtubepot-bgutilscript": {"script_path": ["__disabled__"]},
        },
    }

    target = f"ytsearch{max_results}:{query}"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
    except Exception as e:
        raise OverlayError(
            f"YouTube search failed for query: {query!r}",
            str(e),
        )

    out: List[CatalogVideo] = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        if entry.get("live_status") in ("is_live", "is_upcoming"):
            continue
        vid = entry.get("id")
        dur = entry.get("duration")
        if not vid or not dur:
            continue

        ts = entry.get("timestamp") or entry.get("release_timestamp")
        if ts:
            try:
                from datetime import datetime, timezone

                upload_date = (
                    datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y%m%d")
                )
            except (ValueError, OSError, OverflowError):
                upload_date = ""
        else:
            upload_date = ""

        thumbs = entry.get("thumbnails") or []
        thumb_url = ""
        if thumbs and isinstance(thumbs, list):
            last = thumbs[-1]
            if isinstance(last, dict):
                thumb_url = str(last.get("url") or "")
        if not thumb_url:
            thumb_url = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

        out.append(
            CatalogVideo(
                video_id=str(vid),
                url=str(entry.get("url") or f"https://www.youtube.com/watch?v={vid}"),
                title=str(entry.get("title") or ""),
                duration=int(dur),
                view_count=int(entry.get("view_count") or 0),
                upload_date=upload_date,
                thumbnail_url=thumb_url,
                channel_slug=SEARCH_CHANNEL_SLUG,
            )
        )
    return out


def search_videos(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    ttl_seconds: int = DEFAULT_SEARCH_TTL_SECONDS,
) -> List[CatalogVideo]:
    """Search YouTube and return up to ``max_results`` videos.

    Cached on disk per ``(query, max_results)`` for ``ttl_seconds``. The
    cache survives Streamlit reruns without re-hitting YouTube.
    """
    q = query.strip()
    if not q:
        return []
    if max_results <= 0:
        return []

    cache = _read_search_cache(cache_dir)
    key = _query_key(q, max_results)
    entry = cache.get(key)
    now = time.time()
    if isinstance(entry, dict):
        ts = entry.get("ts")
        videos_raw = entry.get("videos")
        if isinstance(ts, (int, float)) and isinstance(videos_raw, list):
            if (now - ts) < ttl_seconds:
                videos: List[CatalogVideo] = []
                for raw in videos_raw:
                    try:
                        videos.append(_video_from_dict(raw))
                    except (KeyError, ValueError, TypeError):
                        continue
                if videos:
                    logger.debug(f"Search cache hit for {key!r} ({len(videos)} hits)")
                    return videos

    logger.info(f"YouTube search: {q!r} (max={max_results})")
    videos = _ydl_search(q, max_results)
    cache[key] = {
        "ts": now,
        "videos": [asdict(v) for v in videos],
    }
    _write_search_cache(cache_dir, cache)
    return videos


def add_pick_to_catalog(
    video: CatalogVideo,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> None:
    """Persist a search-result pick into the channel catalog under the
    ``__search__`` slug so ``cartoon_catalog.list_videos`` exposes it for
    the render pipeline. Idempotent (dedupes on video_id).
    """
    cache = _read_cache(cache_dir)
    entry = cache["channels"].get(SEARCH_CHANNEL_SLUG)
    if not isinstance(entry, dict):
        entry = {"scraped_at": None, "videos": []}
    raw_videos = entry.get("videos") if isinstance(entry.get("videos"), list) else []
    if any(
        isinstance(rv, dict) and rv.get("video_id") == video.video_id
        for rv in raw_videos
    ):
        return
    raw_videos.append(asdict(video))
    entry["videos"] = raw_videos
    cache["channels"][SEARCH_CHANNEL_SLUG] = entry
    _write_cache(cache_dir, cache)


def get_search_picks(cache_dir: Path = DEFAULT_CACHE_DIR) -> List[CatalogVideo]:
    """Read all persisted search picks (the ``__search__`` cache slug)."""
    cache = _read_cache(cache_dir)
    entry = cache["channels"].get(SEARCH_CHANNEL_SLUG)
    if not isinstance(entry, dict):
        return []
    raw = entry.get("videos") or []
    out: List[CatalogVideo] = []
    for rv in raw:
        try:
            out.append(_video_from_dict(rv))
        except (KeyError, ValueError, TypeError):
            continue
    return out
