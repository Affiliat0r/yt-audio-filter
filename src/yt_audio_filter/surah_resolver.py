"""Resolve canonical surah names to YouTube channel video candidates.

Given a list of user-specified surah names and an audio channel URL, scrape
the channel, run `detect_surah` on every video title, build a lookup indexed
by canonical surah name, and return the matching `Candidate` for each
requested name in the order the user requested them.

If any requested name is not found on the channel, raise `OverlayError`
with the missing names AND a sample of available surah names (to aid
discovery of what's actually on the channel).
"""

from typing import Dict, List

from .channel_discovery import Candidate, fetch_candidates
from .exceptions import OverlayError
from .logger import get_logger
from .surah_detector import detect_surah

logger = get_logger()


def resolve_surahs(
    surah_names: List[str],
    audio_channel_url: str,
    max_candidates: int = 200,
) -> List[Candidate]:
    """Resolve each requested surah to a channel video.

    Scrapes the channel via `fetch_candidates`, runs `detect_surah` on every
    candidate title to build a canonical-name index, then looks up each
    requested surah (case-insensitive). If multiple candidates match the
    same canonical name, the newest wins (channel returns newest first, so
    the first match is kept and subsequent ones are ignored).

    Returns candidates in the same order the user requested, NOT the order
    they appear in the channel.

    Raises `OverlayError` if `surah_names` is empty, or if any requested
    name is not found. The error details include a sample of available
    canonical names from the channel.
    """
    if not surah_names:
        raise OverlayError("No surahs requested")

    logger.info(
        f"Resolving {len(surah_names)} surah(s) against channel {audio_channel_url}..."
    )

    candidates = fetch_candidates(audio_channel_url, max_videos=max_candidates)

    # Build index: canonical_name -> Candidate. Newest wins on ties because
    # fetch_candidates returns newest first, and we only insert if absent.
    index: Dict[str, Candidate] = {}
    for cand in candidates:
        match = detect_surah(cand.title)
        if match is None:
            continue
        if match.name not in index:
            index[match.name] = cand

    logger.info(f"Indexed {len(index)} unique surah(s) from channel")

    # Case-insensitive lookup map: lower(canonical_name) -> canonical_name
    lower_to_canonical: Dict[str, str] = {name.lower(): name for name in index}

    resolved: List[Candidate] = []
    missing: List[str] = []
    for requested in surah_names:
        key = requested.strip().lower()
        canonical = lower_to_canonical.get(key)
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
            f"Available on channel (sample): {sample_str}",
        )

    return resolved
