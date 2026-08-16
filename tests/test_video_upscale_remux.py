"""Optional GPU upscaling during the music-removal remux.

Music removal copies the video stream untouched (``-c:v copy``), which is why
it is fast and why the picture is bit-identical to the source. That also means
a 360p source stays 360p.

Real-ESRGAN cannot help on long videos: a 2-hour cartoon is 172,800 frames and
roughly 194 GB of PNG scratch. Plain scaling can — a few minutes on NVENC, no
scratch space — and it is worth doing despite being interpolation rather than
reconstruction, because YouTube gives 720p uploads a materially better bitrate
ladder than 360p ones.

The distinction the code must preserve: ``upscale`` invents detail and is for
short clips; ``scale_height`` improves delivery and works at any length.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_audio_filter.ffmpeg import build_remux_command


def _cmd(**kwargs) -> list:
    return build_remux_command(
        video_path=Path("in.mp4"),
        audio_path=Path("voice.m4a"),
        output_path=Path("out.mp4"),
        **kwargs,
    )


def test_default_still_copies_the_video_stream() -> None:
    """The fast path must not regress: no scaling asked for, nothing re-encoded."""
    cmd = _cmd()
    assert "-c:v" in cmd
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert not any(a.startswith("scale") for a in cmd)


def test_scaling_replaces_the_copy_with_a_real_encode() -> None:
    cmd = _cmd(scale_height=720)
    assert cmd[cmd.index("-c:v") + 1] != "copy"
    graph = cmd[cmd.index("-vf") + 1]
    # Height is pinned; width follows the source aspect and is forced even so
    # an odd result cannot break h264's 2x2 chroma grid.
    assert "720" in graph
    assert "-2" in graph


def test_scaling_preserves_aspect_ratio() -> None:
    """Never hardcode 1280x720 — a 4:3 source must not be stretched to 16:9."""
    graph = _cmd(scale_height=720)[_cmd(scale_height=720).index("-vf") + 1]
    assert "1280" not in graph


def test_a_source_already_tall_enough_is_not_touched() -> None:
    """Upscaling 1080p to 720p would be a downgrade, and re-encoding an
    already-adequate source throws away quality for nothing."""
    cmd = build_remux_command(
        video_path=Path("in.mp4"),
        audio_path=Path("voice.m4a"),
        output_path=Path("out.mp4"),
        scale_height=720,
        source_height=1080,
    )
    assert cmd[cmd.index("-c:v") + 1] == "copy"


def test_equal_height_is_also_left_alone() -> None:
    cmd = build_remux_command(
        video_path=Path("in.mp4"),
        audio_path=Path("voice.m4a"),
        output_path=Path("out.mp4"),
        scale_height=720,
        source_height=720,
    )
    assert cmd[cmd.index("-c:v") + 1] == "copy"


def test_audio_is_always_re_encoded() -> None:
    """The whole point is the new vocal-only track; it is never copied."""
    for kwargs in ({}, {"scale_height": 720}):
        cmd = _cmd(**kwargs)
        assert cmd[cmd.index("-c:a") + 1] == "aac"


def test_watermark_and_scaling_compose() -> None:
    """Both build a filter graph; asking for both must not drop one."""
    cmd = _cmd(scale_height=720, watermark=True)
    graph = cmd[cmd.index("-vf") + 1]
    assert "720" in graph
    assert "drawbox" in graph or "drawtext" in graph


def test_only_the_first_video_stream_is_mapped() -> None:
    """Cover art rides along as a second video stream; mapping it produces a
    file whose 'video' is a still image."""
    for kwargs in ({}, {"scale_height": 720}):
        cmd = _cmd(**kwargs)
        assert "0:v:0" in cmd


@pytest.mark.parametrize("height", [720, 1080])
def test_nvenc_is_used_when_available(height: int) -> None:
    with patch("yt_audio_filter.ffmpeg.check_nvenc_available", return_value=True):
        cmd = _cmd(scale_height=height)
    assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc"


def test_falls_back_to_libx264_without_a_gpu() -> None:
    with patch("yt_audio_filter.ffmpeg.check_nvenc_available", return_value=False):
        cmd = _cmd(scale_height=720)
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
