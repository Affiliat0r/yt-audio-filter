"""Guards for the 1080p-source era (2026-11-21, docs/lessons-learned.md).

Two things went wrong around the download fix: the cause was a stale yt-dlp
that failed silently, and the 720p defaults built for 360p sources would
have shrunk the 1080p visuals the fix brought back.
"""

import datetime
from pathlib import Path
from unittest import mock

from yt_audio_filter import ffmpeg_overlay, youtube


def test_ytdlp_age_is_read_from_its_date_version() -> None:
    today = datetime.date(2026, 11, 21)
    assert youtube.ytdlp_age_days("2026.10.26", today) == 26
    assert youtube.ytdlp_age_days("2025.12.08", today) == 348
    assert youtube.ytdlp_age_days("nightly-garbage", today) is None


def test_a_stale_ytdlp_is_announced(caplog) -> None:
    with mock.patch.object(youtube, "ytdlp_age_days", return_value=348):
        youtube.warn_if_ytdlp_stale()
    assert "pip install -U yt-dlp" in caplog.text


def _probe(height):
    return mock.patch("yt_audio_filter.ffmpeg.get_video_info", return_value={"height": height})


def test_a_1080p_visual_lifts_a_720p_render() -> None:
    with _probe(1080):
        assert ffmpeg_overlay.resolution_for_source((1280, 720), Path("v.mp4")) == (1920, 1080)


def test_a_360p_visual_keeps_the_asked_resolution() -> None:
    with _probe(360):
        assert ffmpeg_overlay.resolution_for_source((1280, 720), Path("v.mp4")) == (1280, 720)


def test_a_4k_visual_is_capped_at_1080p() -> None:
    with _probe(2160):
        assert ffmpeg_overlay.resolution_for_source((1280, 720), Path("v.mp4")) == (1920, 1080)


def test_an_unreadable_visual_keeps_the_asked_resolution() -> None:
    with mock.patch("yt_audio_filter.ffmpeg.get_video_info", return_value={}):
        assert ffmpeg_overlay.resolution_for_source((1280, 720), Path("v.mp4")) == (1280, 720)
