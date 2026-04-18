"""Unit tests for yt_audio_filter.channel_discovery."""

from unittest.mock import patch

import pytest

from yt_audio_filter.channel_discovery import (
    Candidate,
    ChannelDiscoveryError,
    fetch_candidates,
    filter_out_processed,
)
from yt_audio_filter.scraper import VideoInfo


def _vi(video_id: str, duration: int) -> VideoInfo:
    return VideoInfo(
        video_id=video_id,
        title=f"Title {video_id}",
        url=f"https://youtu.be/{video_id}",
        duration=duration,
        view_count=1000,
        upload_date="20250101",
        thumbnail_url="",
    )


def _cand(vid: str, duration: int = 100) -> Candidate:
    return Candidate(
        video_id=vid, url=f"https://youtu.be/{vid}", title="t", duration=duration, view_count=0
    )


def test_fetch_candidates_filters_short_and_unknown_duration() -> None:
    fake = [_vi("a", 120), _vi("b", 0), _vi("c", 10), _vi("d", 300)]
    with patch("yt_audio_filter.scraper.get_channel_videos", return_value=iter(fake)):
        result = fetch_candidates("@fake", min_duration_s=30)
    ids = [c.video_id for c in result]
    assert ids == ["a", "d"]


def test_fetch_candidates_raises_when_empty() -> None:
    with patch(
        "yt_audio_filter.scraper.get_channel_videos",
        return_value=iter([_vi("a", 10)]),
    ):
        with pytest.raises(ChannelDiscoveryError):
            fetch_candidates("@fake", min_duration_s=30)


def test_filter_out_processed_drops_exhausted_audio() -> None:
    audios = [_cand("a1"), _cand("a2")]
    videos = [_cand("v1"), _cand("v2")]
    # a1 is paired with BOTH v1 and v2 already → a1 should be dropped.
    processed = {("a1", "v1"), ("a1", "v2")}
    a_out, v_out = filter_out_processed(audios, videos, processed)
    assert [c.video_id for c in a_out] == ["a2"]
    assert [c.video_id for c in v_out] == ["v1", "v2"]


def test_filter_out_processed_drops_exhausted_visual() -> None:
    audios = [_cand("a1"), _cand("a2")]
    videos = [_cand("v1"), _cand("v2")]
    # v1 paired with both a1 and a2 → v1 exhausted.
    processed = {("a1", "v1"), ("a2", "v1")}
    a_out, v_out = filter_out_processed(audios, videos, processed)
    assert [c.video_id for c in v_out] == ["v2"]


def test_filter_out_processed_keeps_partial() -> None:
    audios = [_cand("a1")]
    videos = [_cand("v1"), _cand("v2")]
    processed = {("a1", "v1")}
    a_out, v_out = filter_out_processed(audios, videos, processed)
    # a1 still has v2 available → not exhausted
    assert [c.video_id for c in a_out] == ["a1"]
