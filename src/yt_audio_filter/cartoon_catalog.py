"""Cartoon channel catalog: load configured channels, scrape videos with
TTL-based caching, and resolve local thumbnail paths for the Streamlit UI.

This module intentionally delegates scraping to `scraper.get_channel_videos`
(imported lazily inside `list_videos` to avoid the stdout-rebind-at-import
gotcha documented in channel_discovery.py)."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.request import urlopen

from .exceptions import OverlayError
from .logger import get_logger

logger = get_logger()

DEFAULT_CHANNELS_PATH = Path("config/channels.json")
DEFAULT_CACHE_DIR = Path("cache")
CATALOG_CACHE_FILENAME = "cartoon_catalog.json"
THUMBNAIL_SUBDIR = "thumbnails"


@dataclass(frozen=True)
class CartoonChannel:
    slug: str
    handle: str
    url: str
    display_name: str


@dataclass(frozen=True)
class CatalogVideo:
    video_id: str
    url: str
    title: str
    duration: int
    view_count: int
    upload_date: str
    thumbnail_url: str
    channel_slug: str


def load_channels(config_path: Path = DEFAULT_CHANNELS_PATH) -> List[CartoonChannel]:
    """Parse config/channels.json.

    Raises:
        OverlayError: if the file is missing, unreadable, or malformed.
    """
    if not config_path.exists():
        raise OverlayError(
            f"Channels config not found: {config_path}",
            "Expected a JSON file with a 'channels' array. "
            "See config/channels.json in the repository for the expected shape.",
        )

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise OverlayError(
            f"Channels config is not valid JSON: {config_path}",
            str(e),
        )
    except OSError as e:
        raise OverlayError(f"Failed to read channels config: {config_path}", str(e))

    if not isinstance(data, dict):
        raise OverlayError(
            f"Channels config must be a JSON object at the root: {config_path}"
        )

    raw_channels = data.get("channels")
    if not isinstance(raw_channels, list) or not raw_channels:
        raise OverlayError(
            f"Channels config must contain a non-empty 'channels' array: {config_path}"
        )

    required = ("slug", "handle", "url", "display_name")
    channels: List[CartoonChannel] = []
    for i, entry in enumerate(raw_channels):
        if not isinstance(entry, dict):
            raise OverlayError(
                f"Channel entry #{i} is not an object in {config_path}"
            )
        missing = [k for k in required if not entry.get(k)]
        if missing:
            raise OverlayError(
                f"Channel entry #{i} in {config_path} missing required fields: {missing}"
            )
        channels.append(
            CartoonChannel(
                slug=str(entry["slug"]),
                handle=str(entry["handle"]),
                url=str(entry["url"]),
                display_name=str(entry["display_name"]),
            )
        )

    return channels


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / CATALOG_CACHE_FILENAME


def _read_cache(cache_dir: Path) -> Dict:
    path = _cache_path(cache_dir)
    if not path.exists():
        return {"generated_at": None, "channels": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read cartoon catalog cache ({path}): {e}; starting fresh")
        return {"generated_at": None, "channels": {}}
    if not isinstance(data, dict):
        return {"generated_at": None, "channels": {}}
    data.setdefault("channels", {})
    if not isinstance(data["channels"], dict):
        data["channels"] = {}
    return data


def _write_cache(cache_dir: Path, cache: Dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir)
    cache["generated_at"] = _now_utc().isoformat()
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def _video_from_dict(d: Dict) -> CatalogVideo:
    return CatalogVideo(
        video_id=str(d["video_id"]),
        url=str(d["url"]),
        title=str(d.get("title", "")),
        duration=int(d.get("duration") or 0),
        view_count=int(d.get("view_count") or 0),
        upload_date=str(d.get("upload_date") or ""),
        thumbnail_url=str(d.get("thumbnail_url") or ""),
        channel_slug=str(d["channel_slug"]),
    )


def _scrape_channel(channel: CartoonChannel, max_videos: int) -> List[CatalogVideo]:
    # Lazy import: scraper.py rebinds sys.stdout/stderr at module import time,
    # which interferes with pytest capture when this module is imported.
    from .scraper import get_channel_videos

    logger.info(f"Scraping {channel.handle} (max={max_videos})")
    videos: List[CatalogVideo] = []
    for v in get_channel_videos(channel.url, max_videos=max_videos):
        videos.append(
            CatalogVideo(
                video_id=v.video_id,
                url=v.url,
                title=v.title,
                duration=int(v.duration or 0),
                view_count=int(v.view_count or 0),
                upload_date=v.upload_date or "",
                thumbnail_url=v.thumbnail_url
                or f"https://i.ytimg.com/vi/{v.video_id}/hqdefault.jpg",
                channel_slug=channel.slug,
            )
        )
    return videos


def list_videos(
    channels: Optional[List[CartoonChannel]] = None,
    max_per_channel: int = 50,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    ttl_seconds: int = 24 * 3600,
) -> List[CatalogVideo]:
    """Return a combined, de-duplicated list of videos across all channels.

    Reads the JSON cache at `cache/cartoon_catalog.json`; re-scrapes any
    channel whose `scraped_at` timestamp is older than `ttl_seconds`.
    Returns videos ordered by channel order (as declared in channels.json),
    then by scrape order within each channel (newest first).
    """
    if channels is None:
        channels = load_channels()

    cache = _read_cache(cache_dir)
    now = _now_utc()
    mutated = False

    combined: List[CatalogVideo] = []
    seen_ids: set = set()

    for channel in channels:
        entry = cache["channels"].get(channel.slug)
        need_refresh = True
        if isinstance(entry, dict):
            scraped_at = _parse_iso(entry.get("scraped_at", ""))
            if scraped_at is not None:
                age = (now - scraped_at).total_seconds()
                if age < ttl_seconds:
                    need_refresh = False

        if need_refresh:
            try:
                videos = _scrape_channel(channel, max_per_channel)
            except Exception as e:
                logger.warning(
                    f"Scraping {channel.handle} failed ({e}); using cached data if present"
                )
                videos = None
            if videos is not None:
                cache["channels"][channel.slug] = {
                    "scraped_at": now.isoformat(),
                    "videos": [asdict(v) for v in videos],
                }
                mutated = True
            else:
                # Fall back to any stale cached entry we have on disk.
                pass

        entry = cache["channels"].get(channel.slug, {})
        raw_videos = entry.get("videos", []) if isinstance(entry, dict) else []
        for raw in raw_videos:
            try:
                video = _video_from_dict(raw)
            except (KeyError, ValueError, TypeError) as e:
                logger.debug(f"Skipping malformed cached video entry: {e}")
                continue
            if video.video_id in seen_ids:
                continue
            seen_ids.add(video.video_id)
            combined.append(video)

    if mutated:
        _write_cache(cache_dir, cache)

    # Also expose user-picked videos from keyword search (`__search__` slug),
    # which aren't backed by a configured channel and therefore aren't in
    # the loop above. The render pipeline resolves visuals against this
    # combined list, so a search-pick must be visible here.
    search_entry = cache["channels"].get("__search__")
    if isinstance(search_entry, dict):
        for raw in search_entry.get("videos", []) or []:
            try:
                video = _video_from_dict(raw)
            except (KeyError, ValueError, TypeError):
                continue
            if video.video_id in seen_ids:
                continue
            seen_ids.add(video.video_id)
            combined.append(video)

    return combined


def ensure_thumbnail(
    video: CatalogVideo,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    timeout: int = 30,
) -> Path:
    """Download the video's thumbnail to `cache/thumbnails/<video_id>.jpg`.

    Returns the cached path. Re-uses the file on subsequent calls; no
    network access if the file already exists.
    """
    thumb_dir = cache_dir / THUMBNAIL_SUBDIR
    thumb_dir.mkdir(parents=True, exist_ok=True)
    dest = thumb_dir / f"{video.video_id}.jpg"

    if dest.exists() and dest.stat().st_size > 0:
        return dest

    if not video.thumbnail_url:
        raise OverlayError(
            f"No thumbnail URL for video {video.video_id}",
            "CatalogVideo.thumbnail_url is empty; cannot download.",
        )

    logger.info(f"Downloading thumbnail for {video.video_id}")
    tmp = dest.with_suffix(".jpg.tmp")
    try:
        with urlopen(video.thumbnail_url, timeout=timeout) as resp:
            data = resp.read()
        with open(tmp, "wb") as f:
            f.write(data)
        tmp.replace(dest)
    except Exception as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise OverlayError(
            f"Failed to download thumbnail for {video.video_id}",
            f"{video.thumbnail_url}: {e}",
        )

    return dest
