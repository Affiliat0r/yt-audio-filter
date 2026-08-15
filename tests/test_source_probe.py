"""Unit tests for source-quality probing.

Two answers, one shape: ffprobe on a cached file (ground truth), or yt-dlp's
format listing for a video nobody has downloaded yet (an optimistic upper
bound, because SABR often delivers far less than YouTube advertises).

No network and no FFmpeg: ``subprocess.run`` and ``yt_dlp.YoutubeDL`` are
both mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_audio_filter import source_probe
from yt_audio_filter.ffmpeg import get_video_info
from yt_audio_filter.source_probe import (
    SourceQualityInfo,
    find_cached_video,
    probe_source_quality,
)

URL = "https://www.youtube.com/watch?v=abc123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ffprobe_result(payload: dict, returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = json.dumps(payload)
    result.stderr = ""
    return result


def _fake_ydl(info: object) -> MagicMock:
    """A ``yt_dlp.YoutubeDL(...)`` context manager returning ``info``."""
    ydl = MagicMock()
    ydl.extract_info.return_value = info
    factory = MagicMock()
    factory.return_value.__enter__.return_value = ydl
    return factory


# ---------------------------------------------------------------------------
# Cached-file detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ext", ["mp4", "m4a", "webm", "mkv", "opus"])
def test_every_extension_download_stream_may_land_on_is_found(tmp_path: Path, ext):
    (tmp_path / f"video_abc123.{ext}").write_bytes(b"x")

    found = find_cached_video("abc123", tmp_path)
    assert found is not None and found.name == f"video_abc123.{ext}"


def test_zero_byte_downloads_do_not_count_as_cached(tmp_path: Path):
    # download_stream leaves these behind when a download dies mid-flight.
    (tmp_path / "video_abc123.mp4").write_bytes(b"")

    assert find_cached_video("abc123", tmp_path) is None


def test_an_upscaled_render_is_not_the_source(tmp_path: Path):
    """The card describes the source visual; the UI derives what upscaling
    would add. Treating upscaled_<id>.mp4 as the source would double-count it."""
    (tmp_path / "upscaled_abc123.mp4").write_bytes(b"x")

    assert find_cached_video("abc123", tmp_path) is None


def test_a_missing_cache_directory_is_simply_not_cached(tmp_path: Path):
    assert find_cached_video("abc123", tmp_path / "absent") is None


# ---------------------------------------------------------------------------
# Measured path (cached file)
# ---------------------------------------------------------------------------


def test_a_cached_file_is_measured_not_listed(tmp_path: Path):
    (tmp_path / "video_abc123.webm").write_bytes(b"x")

    with patch(
        "yt_audio_filter.ffmpeg.get_video_info",
        return_value={"width": 640, "height": 360, "codec": "vp9", "fps": 25.0},
    ) as mock_probe:
        info = probe_source_quality("abc123", URL, tmp_path)

    assert mock_probe.call_args.args[0].name == "video_abc123.webm"
    assert info == SourceQualityInfo(kind="measured", width=640, height=360, fps=25.0, codec="vp9")


def test_an_unreadable_cached_file_falls_through_to_the_listing(tmp_path: Path):
    (tmp_path / "video_abc123.mp4").write_bytes(b"not really a video")

    with (
        patch("yt_audio_filter.ffmpeg.get_video_info", return_value={}),
        patch.object(
            source_probe, "_best_listed_format", return_value={"height": 1080, "width": 1920}
        ),
    ):
        info = probe_source_quality("abc123", URL, tmp_path)

    assert info.kind == "listed"
    assert info.height == 1080


# ---------------------------------------------------------------------------
# Listed path (nothing cached)
# ---------------------------------------------------------------------------


def test_the_listing_takes_the_tallest_video_format(tmp_path: Path):
    formats = [
        {"height": 360, "width": 640, "vcodec": "avc1", "fps": 25},
        {"height": 1080, "width": 1920, "vcodec": "vp9", "fps": 30},
        {"height": 720, "width": 1280, "vcodec": "avc1", "fps": 30},
        # Audio-only and resolution-less formats say nothing about quality.
        {"vcodec": "none", "acodec": "opus"},
        {"vcodec": "avc1", "height": None},
    ]

    with patch("yt_dlp.YoutubeDL", _fake_ydl({"formats": formats})):
        info = probe_source_quality("abc123", URL, tmp_path)

    assert info.kind == "listed"
    assert (info.width, info.height, info.fps) == (1920, 1080, 30.0)
    # A listed format's codec is not what SABR will actually deliver.
    assert info.codec is None


def test_the_listing_disables_the_bgutil_deno_cold_start(tmp_path: Path):
    """Without this the plugin downloads npm deps on first use and the probe
    stalls for ~15 s — the same guard yt_metadata.fetch_yt_metadata carries."""
    factory = _fake_ydl({"formats": [{"height": 720, "width": 1280, "vcodec": "avc1"}]})

    with patch("yt_dlp.YoutubeDL", factory):
        probe_source_quality("abc123", URL, tmp_path)

    opts = factory.call_args.args[0]
    assert opts["skip_download"] is True
    assert opts["extractor_args"]["youtubepot-bgutilscript"] == {"script_path": ["__disabled__"]}
    assert opts["extractor_args"]["youtube"]["player_client"] == [
        "tv_embedded",
        "ios",
        "web_embedded",
        "android",
    ]


@pytest.mark.parametrize(
    "info",
    [
        {"formats": []},
        {"formats": [{"vcodec": "none", "acodec": "opus"}]},
        None,
    ],
)
def test_a_listing_without_video_formats_is_an_all_null_result(tmp_path: Path, info):
    with patch("yt_dlp.YoutubeDL", _fake_ydl(info)):
        assert probe_source_quality("abc123", URL, tmp_path) == SourceQualityInfo(kind="listed")


def test_a_failed_listing_is_an_all_null_result_not_an_exception(tmp_path: Path):
    """Bot detection is routine here. The job must still complete: the card
    reads a null height as "unknown" and nothing downstream is blocked."""
    with patch("yt_dlp.YoutubeDL", side_effect=RuntimeError("Sign in to confirm")):
        assert probe_source_quality("abc123", URL, tmp_path) == SourceQualityInfo(kind="listed")


def test_a_non_youtube_url_does_not_raise_out_of_the_probe(tmp_path: Path):
    assert probe_source_quality("abc123", "https://vimeo.com/1", tmp_path).height is None


# ---------------------------------------------------------------------------
# ffmpeg.get_video_info
# ---------------------------------------------------------------------------


def test_get_video_info_parses_the_fractional_frame_rate():
    payload = {
        "streams": [
            {
                "width": 1920,
                "height": 1080,
                "codec_name": "h264",
                "avg_frame_rate": "24000/1001",
            }
        ]
    }

    with patch("subprocess.run", return_value=_ffprobe_result(payload)):
        info = get_video_info(Path("video_abc123.mp4"))

    assert info["width"] == 1920
    assert info["height"] == 1080
    assert info["codec"] == "h264"
    assert info["fps"] == pytest.approx(23.976, abs=0.001)


@pytest.mark.parametrize("rate", ["0/0", "N/A", "", None])
def test_get_video_info_survives_an_untimed_stream(rate):
    """ffprobe reports 0/0 for streams it could not time; dividing by that
    denominator would take out the whole probe."""
    payload = {"streams": [{"width": 640, "height": 360, "avg_frame_rate": rate}]}

    with patch("subprocess.run", return_value=_ffprobe_result(payload)):
        info = get_video_info(Path("video_abc123.mp4"))

    assert "fps" not in info
    assert info["height"] == 360


def test_get_video_info_returns_empty_for_a_file_with_no_video_stream():
    with patch("subprocess.run", return_value=_ffprobe_result({"streams": []})):
        assert get_video_info(Path("audio_abc123.m4a")) == {}


def test_get_video_info_is_fail_soft_like_get_audio_info():
    with patch("subprocess.run", return_value=_ffprobe_result({}, returncode=1)):
        assert get_video_info(Path("missing.mp4")) == {}

    with patch("subprocess.run", side_effect=FileNotFoundError("ffprobe")):
        assert get_video_info(Path("missing.mp4")) == {}


# ------------------------------------------- combined downloads are cached too


def test_find_cached_video_matches_the_combined_download(tmp_path) -> None:
    """Music removal downloads with ``mode="video+audio"``, which
    ``download_stream`` names ``full_<id>.<ext>``; overlay renders fetch
    video-only and get ``video_<id>.<ext>``.

    Both hold a real video stream ffprobe can measure. Checking only the
    ``video_`` prefix made every music-removal probe fall through to a slow
    yt-dlp format listing for a file already sitting on disk.
    """
    from yt_audio_filter.source_probe import find_cached_video

    combined = tmp_path / "full_abc12345xyz.mp4"
    combined.write_bytes(b"\x00" * 64)

    assert find_cached_video("abc12345xyz", tmp_path) == combined


def test_video_only_still_wins_when_both_exist(tmp_path) -> None:
    """Overlay renders use the video-only file, so it is the one whose
    properties describe what the render will actually consume."""
    from yt_audio_filter.source_probe import find_cached_video

    (tmp_path / "full_abc12345xyz.mp4").write_bytes(b"\x00" * 64)
    video_only = tmp_path / "video_abc12345xyz.mp4"
    video_only.write_bytes(b"\x00" * 64)

    assert find_cached_video("abc12345xyz", tmp_path) == video_only


def test_zero_byte_combined_file_is_skipped(tmp_path) -> None:
    """download_stream leaves zero-byte files behind when a download dies
    mid-flight; ffprobe would learn nothing from one."""
    from yt_audio_filter.source_probe import find_cached_video

    (tmp_path / "full_abc12345xyz.mp4").write_bytes(b"")

    assert find_cached_video("abc12345xyz", tmp_path) is None


def test_video_only_wins_even_with_a_later_extension(tmp_path) -> None:
    """Regression: prefix must outrank extension.

    YouTube's high-resolution video-only formats are VP9/webm, while the
    combined download music removal fetches is usually format 18 — mp4 at 360p.
    Iterating extensions first let ``full_<id>.mp4`` win over
    ``video_<id>.webm`` purely because mp4 is checked before webm, so the probe
    reported 360p for a render that would actually use the 1080p file — telling
    the user to drop to a lower preset than their source supports.
    """
    from yt_audio_filter.source_probe import find_cached_video

    video_only = tmp_path / "video_abc12345xyz.webm"
    video_only.write_bytes(b"\x00" * 64)
    (tmp_path / "full_abc12345xyz.mp4").write_bytes(b"\x00" * 64)

    assert find_cached_video("abc12345xyz", tmp_path) == video_only
