"""Duration-based pairing of audio and visual YouTube candidates.

For each audio candidate, pick the visual whose duration is >= audio duration and
closest to it (least loop overhead). If no visual is long enough, fall back to
the longest available visual — we'll still loop it, but with the least repetition.
"""

from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from .channel_discovery import Candidate
from .exceptions import OverlayError
from .logger import get_logger

logger = get_logger()


@dataclass
class PairChoice:
    audio: Candidate
    visual: Candidate
    # visual.duration - audio.duration; negative means we'll need to loop.
    duration_slack: int


def _rank_visuals_for_audio(
    audio: Candidate,
    visuals: List[Candidate],
    processed_pair_set: Set[Tuple[str, str]],
) -> List[Tuple[int, int, Candidate]]:
    """Return visuals sorted by (not-long-enough penalty, loop overhead) ascending.

    The sort key guarantees:
      1. Visuals with duration >= audio.duration come first, in order of least
         duration_slack (closest match = least wasted footage).
      2. Visuals shorter than audio come next, ranked by absolute duration gap
         (less looping = fewer obvious repeats).
    Already-processed pairs are filtered out.
    """
    scored: List[Tuple[int, int, Candidate]] = []
    for v in visuals:
        if (audio.video_id, v.video_id) in processed_pair_set:
            continue
        slack = v.duration - audio.duration
        if slack >= 0:
            scored.append((0, slack, v))
        else:
            scored.append((1, -slack, v))
    scored.sort(key=lambda t: (t[0], t[1]))
    return scored


def select_pair(
    audio_candidates: List[Candidate],
    video_candidates: List[Candidate],
    processed_pair_set: Optional[Set[Tuple[str, str]]] = None,
) -> PairChoice:
    """Pick one (audio, visual) pair using the duration rule.

    Strategy: iterate through audio candidates (in the order they come from
    the channel — typically newest first). For each, find the best-ranking
    unused visual. Return the first audio that has at least one valid visual.

    Raises OverlayError if nothing pairs — the caller stops the batch.
    """
    processed_pair_set = processed_pair_set or set()
    if not audio_candidates:
        raise OverlayError("No audio candidates available to pair")
    if not video_candidates:
        raise OverlayError("No visual candidates available to pair")

    for audio in audio_candidates:
        ranked = _rank_visuals_for_audio(audio, video_candidates, processed_pair_set)
        if not ranked:
            continue
        _, _, visual = ranked[0]
        slack = visual.duration - audio.duration
        logger.info(
            f"Paired audio '{audio.title[:50]}' ({audio.duration}s) with visual "
            f"'{visual.title[:50]}' ({visual.duration}s); slack={slack:+d}s"
        )
        return PairChoice(audio=audio, visual=visual, duration_slack=slack)

    raise OverlayError(
        "No unprocessed audio+visual combinations remain",
        "Every audio candidate is already paired with every visual candidate. "
        "Add more videos to the channels, or delete entries from the state file.",
    )


def select_pairs(
    audio_candidates: List[Candidate],
    video_candidates: List[Candidate],
    count: int,
    processed_pair_set: Optional[Set[Tuple[str, str]]] = None,
) -> List[PairChoice]:
    """Select up to `count` non-overlapping pairs.

    Each audio or visual candidate is used at most once within this batch
    (prevents the batch from trivially picking the same "best match" over
    and over). Already-processed pairs from state are also excluded.
    """
    processed_pair_set = set(processed_pair_set or set())
    chosen: List[PairChoice] = []
    used_audio: Set[str] = set()
    used_visual: Set[str] = set()

    for _ in range(count):
        remaining_audio = [a for a in audio_candidates if a.video_id not in used_audio]
        remaining_visual = [v for v in video_candidates if v.video_id not in used_visual]
        if not remaining_audio or not remaining_visual:
            break
        try:
            pick = select_pair(remaining_audio, remaining_visual, processed_pair_set)
        except OverlayError:
            break
        chosen.append(pick)
        used_audio.add(pick.audio.video_id)
        used_visual.add(pick.visual.video_id)
        processed_pair_set.add((pick.audio.video_id, pick.visual.video_id))

    if not chosen:
        raise OverlayError(
            f"Could not select any pair (requested {count}); channels may be exhausted."
        )
    if len(chosen) < count:
        logger.warning(
            f"Only found {len(chosen)} usable pair(s) out of {count} requested — "
            f"continuing with what we have."
        )
    return chosen
