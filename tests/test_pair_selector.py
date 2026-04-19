"""Unit tests for yt_audio_filter.pair_selector."""

import pytest

from yt_audio_filter.channel_discovery import Candidate
from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.pair_selector import select_pair, select_pairs


def _c(vid: str, duration: int, title: str = "t") -> Candidate:
    return Candidate(
        video_id=vid,
        url=f"https://youtu.be/{vid}",
        title=title,
        duration=duration,
        view_count=0,
    )


def test_prefers_visual_longer_than_audio_with_least_slack() -> None:
    audio = [_c("a", 60)]
    visuals = [_c("v1", 300), _c("v2", 80), _c("v3", 55)]
    pick = select_pair(audio, visuals)
    # v2 is 80s — the smallest visual that is >= 60s audio
    assert pick.visual.video_id == "v2"
    assert pick.duration_slack == 20


def test_falls_back_to_longest_short_visual_when_none_long_enough() -> None:
    audio = [_c("a", 600)]
    visuals = [_c("v1", 100), _c("v2", 500), _c("v3", 50)]
    pick = select_pair(audio, visuals)
    # No visual is >= 600s; best fallback is v2 (500s, smallest gap)
    assert pick.visual.video_id == "v2"
    assert pick.duration_slack == -100


def test_skips_processed_pair() -> None:
    audio = [_c("a", 60)]
    visuals = [_c("v1", 80), _c("v2", 120)]
    # v1 already processed with a — expect v2
    pick = select_pair(audio, visuals, processed_pair_set={("a", "v1")})
    assert pick.visual.video_id == "v2"


def test_tries_next_audio_when_first_has_no_remaining_visuals() -> None:
    audio = [_c("a1", 60), _c("a2", 90)]
    visuals = [_c("v1", 100)]
    # a1 already paired with v1; a2 should get v1
    pick = select_pair(audio, visuals, processed_pair_set={("a1", "v1")})
    assert pick.audio.video_id == "a2"
    assert pick.visual.video_id == "v1"


def test_all_combinations_used_raises() -> None:
    audio = [_c("a", 60)]
    visuals = [_c("v", 80)]
    with pytest.raises(OverlayError, match="No unprocessed"):
        select_pair(audio, visuals, processed_pair_set={("a", "v")})


def test_empty_audio_raises() -> None:
    with pytest.raises(OverlayError, match="No audio"):
        select_pair([], [_c("v", 100)])


def test_empty_visuals_raises() -> None:
    with pytest.raises(OverlayError, match="No visual"):
        select_pair([_c("a", 60)], [])


def test_select_pairs_returns_non_overlapping() -> None:
    audio = [_c("a1", 60), _c("a2", 120), _c("a3", 200)]
    visuals = [_c("v1", 80), _c("v2", 150), _c("v3", 250)]
    picks = select_pairs(audio, visuals, count=3)
    assert len(picks) == 3
    audios = {p.audio.video_id for p in picks}
    visuals_used = {p.visual.video_id for p in picks}
    assert len(audios) == 3
    assert len(visuals_used) == 3


def test_select_pairs_stops_when_exhausted() -> None:
    audio = [_c("a1", 60)]
    visuals = [_c("v1", 80), _c("v2", 150)]
    picks = select_pairs(audio, visuals, count=5)
    assert len(picks) == 1


def test_select_pairs_count_zero_raises() -> None:
    audio = [_c("a", 60)]
    visuals = [_c("v", 80)]
    with pytest.raises(OverlayError):
        select_pairs(audio, visuals, count=0)
