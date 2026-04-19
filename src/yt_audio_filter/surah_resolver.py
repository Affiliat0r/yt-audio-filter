"""Resolve canonical surah names to YouTube channel video candidates.

Each item in `surah_names` may be either:

  - A canonical surah name (e.g. "Al-Ikhlas") — looked up in the audio
    channel by scraping titles and running `detect_surah`.
  - A direct YouTube URL — used as-is, bypassing the channel index. This
    is the escape hatch when a channel doesn't carry a particular surah.

Returns `Candidate`s in the order the user supplied. Raises `OverlayError`
if any name is unresolved on the channel, listing what's available so the
user can pick from the actual catalogue.
"""

from typing import Dict, List, Tuple

from .channel_discovery import Candidate, fetch_candidates
from .exceptions import OverlayError
from .logger import get_logger
from .surah_detector import detect_all_surahs, detect_surah
from .youtube import is_youtube_url

logger = get_logger()


def _candidate_from_url(url: str) -> Candidate:
    """Build a Candidate from a direct YouTube URL via on-the-fly metadata fetch."""
    # Lazy import to avoid pulling yt_metadata into module-load surface for
    # tests that mock the channel-scrape path (mirrors channel_discovery's
    # treatment of scraper.get_channel_videos).
    from .yt_metadata import fetch_yt_metadata

    meta = fetch_yt_metadata(url)
    if not meta.video_id or meta.video_id == "unknown":
        raise OverlayError(
            f"Could not extract video id from URL: {url}",
            "Pass a canonical YouTube watch URL such as "
            "https://www.youtube.com/watch?v=XXXX or https://youtu.be/XXXX.",
        )
    return Candidate(
        video_id=meta.video_id,
        url=url,
        title=meta.title or meta.video_id,
        duration=int(meta.duration or 0),
        view_count=0,
    )


def resolve_surahs(
    surah_names: List[str],
    audio_channel_url: str,
    max_candidates: int = 200,
) -> List[Candidate]:
    """Resolve each requested item (canonical name OR direct URL) to a Candidate.

    Names are looked up case-insensitively in an index built from the channel.
    URLs skip the channel scrape and resolve via direct metadata fetch.

    The channel is scraped only if at least one item is a name (avoids
    needless network when the user provides URLs for everything).

    Raises `OverlayError` if `surah_names` is empty, if a name doesn't
    resolve in the channel, or if a URL fails to yield metadata.
    """
    if not surah_names:
        raise OverlayError("No surahs requested")

    name_items = [s for s in surah_names if not is_youtube_url(s.strip())]
    url_items = [s for s in surah_names if is_youtube_url(s.strip())]
    logger.info(
        f"Resolving {len(surah_names)} item(s): "
        f"{len(name_items)} name(s), {len(url_items)} direct URL(s)"
    )

    index: Dict[str, Candidate] = {}
    if name_items:
        candidates = fetch_candidates(audio_channel_url, max_videos=max_candidates)
        # For each surah, collect all candidates whose title mentions it, then
        # score so a clean single-surah title wins over a compilation. Scoring:
        # (number of distinct surahs detected in title ASC, duration ASC,
        # channel-order ASC). Lower tuple wins. This way a standalone
        # "Surah An Naas" (1 surah, ~66 s) beats "Juz 30 – Surah Adh Dhuha –
        # Surah An Naas" (2 surahs, ~1290 s) even when the compilation is
        # newer on the channel.
        per_surah: Dict[str, List[Tuple[int, int, int, Candidate]]] = {}
        for order, cand in enumerate(candidates):
            matches = detect_all_surahs(cand.title)
            n_surahs = len(matches)
            for m in matches:
                per_surah.setdefault(m.name, []).append(
                    (n_surahs, cand.duration, order, cand)
                )
        for name, entries in per_surah.items():
            entries.sort(key=lambda t: (t[0], t[1], t[2]))
            index[name] = entries[0][3]
        logger.info(f"Indexed {len(index)} unique surah(s) from channel")

    lower_to_canonical: Dict[str, str] = {name.lower(): name for name in index}

    resolved: List[Candidate] = []
    missing: List[str] = []
    for requested in surah_names:
        item = requested.strip()
        if is_youtube_url(item):
            cand = _candidate_from_url(item)
            logger.info(f"Resolved URL → {cand.title!r} ({cand.duration}s)")
            resolved.append(cand)
            continue
        canonical = lower_to_canonical.get(item.lower())
        if canonical is None:
            missing.append(requested)
        else:
            resolved.append(index[canonical])

    if missing:
        available = sorted(index.keys())
        sample = available[:10]
        sample_str = ", ".join(sample)
        if len(available) > len(sample):
            sample_str += f", ... (+{len(available) - len(sample)} more)"
        raise OverlayError(
            f"Surahs not found on channel: {', '.join(missing)}",
            f"Available on channel (sample): {sample_str}. "
            "To use a specific YouTube video for a missing surah, pass "
            "the URL directly via --surah https://www.youtube.com/watch?v=...",
        )

    return resolved
