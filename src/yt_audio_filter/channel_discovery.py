"""Discover candidate audio + visual videos from YouTube channels."""

from dataclasses import dataclass
from typing import Iterable, List, Optional

from .exceptions import YTAudioFilterError
from .logger import get_logger

logger = get_logger()


class ChannelDiscoveryError(YTAudioFilterError):
    """Raised when channel discovery yields no usable candidates."""


@dataclass
class Candidate:
    video_id: str
    url: str
    title: str
    duration: int  # seconds
    view_count: int


def _to_candidate(v) -> Candidate:
    return Candidate(
        video_id=v.video_id,
        url=v.url,
        title=v.title,
        duration=int(v.duration or 0),
        view_count=int(v.view_count or 0),
    )


def fetch_candidates(
    channel_url: str,
    max_videos: Optional[int] = 200,
    include_shorts: bool = False,
    min_duration_s: int = 30,
) -> List[Candidate]:
    """Scrape a channel and return candidates with usable duration metadata.

    Videos with duration 0 (unknown) are dropped, since we can't match by
    length without it. `min_duration_s` filters out extremely short clips
    that would be visually jarring even when looped.
    """
    # Lazy import: scraper.py rebinds sys.stdout/stderr at module import time,
    # which interferes with pytest capture when channel_discovery is imported.
    from .scraper import get_channel_videos

    logger.info(f"Discovering candidates from {channel_url} (max={max_videos})...")
    raw = list(get_channel_videos(channel_url, max_videos=max_videos, include_shorts=include_shorts))
    candidates = [_to_candidate(v) for v in raw]
    usable = [c for c in candidates if c.duration >= min_duration_s]
    dropped = len(candidates) - len(usable)
    if dropped:
        logger.debug(f"Dropped {dropped} videos with duration < {min_duration_s}s (or unknown)")
    if not usable:
        raise ChannelDiscoveryError(
            f"No usable candidates found in {channel_url}",
            f"Scraped {len(candidates)} videos but none had duration >= {min_duration_s}s.",
        )
    logger.info(f"Kept {len(usable)} candidates from {channel_url}")
    return usable


def filter_out_processed(
    audio_candidates: Iterable[Candidate],
    video_candidates: Iterable[Candidate],
    processed_pair_set: set,
) -> tuple:
    """Return (audio, video) lists with no candidate that has been paired with *every*
    counterpart already. `processed_pair_set` is a set of (audio_id, video_id) tuples.
    """
    audio_list = list(audio_candidates)
    video_list = list(video_candidates)
    video_ids = {v.video_id for v in video_list}
    audio_ids = {a.video_id for a in audio_list}

    exhausted_audio = {
        a.video_id
        for a in audio_list
        if video_ids and all((a.video_id, vid) in processed_pair_set for vid in video_ids)
    }
    exhausted_video = {
        v.video_id
        for v in video_list
        if audio_ids and all((aid, v.video_id) in processed_pair_set for aid in audio_ids)
    }

    audio_filtered = [a for a in audio_list if a.video_id not in exhausted_audio]
    video_filtered = [v for v in video_list if v.video_id not in exhausted_video]
    return audio_filtered, video_filtered
