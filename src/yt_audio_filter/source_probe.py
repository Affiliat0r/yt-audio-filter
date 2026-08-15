"""Find out how good a visual source really is, before anything renders it.

The Studio's output-quality card has to answer one question: would a 1080p
render carry real detail, or just stretch a 360p cartoon? There are two ways
to answer it, and which one applies depends on whether the visual has been
downloaded yet:

* **cached** — ffprobe the file on disk. Ground truth (``kind="measured"``).
* **not cached** — ask yt-dlp what formats YouTube advertises
  (``kind="listed"``). That is an optimistic *upper bound*: protected videos
  list 1080p but SABR usually only delivers format 18 (360p), so the honest
  number is not known until the first download completes.

Nothing here raises for "could not tell". A missing ffprobe, a bot-detected
listing or a dead URL all return an all-``None`` listed result, and the card
says "unknown" — a quality hint must never fail the job that asked for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logger import get_logger

logger = get_logger()

#: Cache basename prefix ``youtube.download_stream`` writes for the
#: video-only stream (``_STREAM_PREFIX["video-only"]``), mirrored by
#: ``worker.catalog_sync._VIDEO_PREFIX``.
_VIDEO_PREFIX = "video_"

#: ``download_stream`` names its output ``<prefix>_<id>.<ext>`` where the prefix
#: comes from the download mode: overlay renders fetch "video-only" (``video_``)
#: while music removal fetches "video+audio" (``full_``). Both hold a real video
#: stream ffprobe can measure, so both count as cached — checking only ``video_``
#: made every music-removal probe fall through to a slow yt-dlp format listing
#: for a file that was already sitting on disk.
_FULL_PREFIX = "full_"

#: The extensions a download may land on, in ``download_stream``'s own order.
_CACHE_EXTENSIONS = ("mp4", "m4a", "webm", "mkv", "opus")


@dataclass(frozen=True)
class SourceQualityInfo:
    """What we know about a source visual. Mirrors ``SourceQuality`` on the wire.

    ``codec`` is only ever populated for ``kind="measured"``: a listed
    format's advertised codec says nothing about the stream SABR will
    actually hand over.
    """

    kind: str  # "measured" | "listed"
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    codec: Optional[str] = None


def find_cached_video(video_id: str, cache_dir: Path) -> Optional[Path]:
    """The cached video-only download for ``video_id``, if there is one.

    Zero-byte files are skipped — ``download_stream`` leaves those behind
    when a download dies mid-flight, and ffprobe would learn nothing from
    them.

    ``upscaled_<id>.mp4`` is deliberately ignored: this describes the
    *source*, and what an upscale would add on top is the caller's business.

    Prefix is the OUTER loop, and that ordering is load-bearing. ``video_`` is
    the video-only stream an overlay render actually consumes — typically the
    1080p VP9/webm one — while ``full_`` is the combined download that music
    removal fetches, usually format 18 at 360p. Iterating extensions first lets
    ``full_<id>.mp4`` win over ``video_<id>.webm`` purely because mp4 is checked
    before webm, and the probe then reports 360p for a render that will use the
    1080p file. That is wrong in the worst direction: the quality card would
    tell the user to drop to a lower preset than their source can support.
    """
    cache_dir = Path(cache_dir)
    for prefix in (_VIDEO_PREFIX, _FULL_PREFIX):
        for ext in _CACHE_EXTENSIONS:
            candidate = cache_dir / f"{prefix}{video_id}.{ext}"
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate
            except OSError as e:
                logger.debug(f"Could not stat {candidate}: {e}")
    return None


def probe_source_quality(video_id: str, url: str, cache_dir: Path) -> SourceQualityInfo:
    """Measure the cached visual, or fall back to YouTube's format listing."""
    cached = find_cached_video(video_id, cache_dir)
    if cached is not None:
        from .ffmpeg import get_video_info

        info = get_video_info(cached)
        if info:
            return SourceQualityInfo(
                kind="measured",
                width=info.get("width"),
                height=info.get("height"),
                fps=info.get("fps"),
                codec=info.get("codec"),
            )
        # An empty probe means ffprobe could not read the file (or is not
        # installed). The listing below is a worse answer, but still an answer.
        logger.warning(f"ffprobe told us nothing about {cached.name}; listing formats instead")

    return _listed_quality(url)


def _listed_quality(url: str) -> SourceQualityInfo:
    """Quality of the best format YouTube advertises for ``url``."""
    try:
        best = _best_listed_format(url)
    except Exception as e:  # noqa: BLE001 - a quality hint may never fail a job
        logger.warning(f"Could not list formats for {url}: {e}")
        return SourceQualityInfo(kind="listed")

    if best is None:
        return SourceQualityInfo(kind="listed")

    fps = best.get("fps")
    width = best.get("width")
    return SourceQualityInfo(
        kind="listed",
        width=int(width) if width else None,
        height=int(best["height"]),
        fps=float(fps) if fps else None,
    )


def _best_listed_format(url: str) -> Optional[Dict[str, Any]]:
    """The highest-resolution video format yt-dlp can see, without downloading."""
    from .youtube import ensure_ytdlp_available, validate_youtube_url

    ensure_ytdlp_available()
    validate_youtube_url(url)

    import yt_dlp

    # Same options as ``yt_metadata.fetch_yt_metadata``: the client cascade
    # dodges n-challenge JS deobfuscation, and the bgutil script path must be
    # disabled or the plugin's Deno cold start stalls the probe for ~15 s.
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extractor_args": {
            "youtube": {"player_client": ["tv_embedded", "ios", "web_embedded", "android"]},
            "youtubepot-bgutilscript": {"script_path": ["__disabled__"]},
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return None

    # Audio-only formats carry vcodec "none"; a format without a height tells
    # us nothing about resolution either way.
    candidates: List[Dict[str, Any]] = [
        fmt
        for fmt in (info.get("formats") or [])
        if fmt.get("vcodec") not in (None, "none") and fmt.get("height")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda fmt: int(fmt["height"]))
