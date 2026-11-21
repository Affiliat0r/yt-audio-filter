"""An HD source is used as it is: no Real-ESRGAN pass.

Since 2026-11-21 the downloader gets real 1080p (see
``youtube.YTDLP_CLIENT_ATTEMPTS``). Sharpening 2x is for the 360p sources the
SABR wall used to leave us; on a 1080p source it would build a 4K file over
hours, only for the render to scale it back down.
"""

from pathlib import Path
from unittest import mock

from yt_audio_filter import upscale


def test_an_hd_source_is_not_sharpened(tmp_path: Path) -> None:
    src = tmp_path / "full_x.mp4"
    src.write_bytes(b"\0")
    with mock.patch.object(upscale, "_probe_size", return_value=(1920, 1080, 25.0)), \
            mock.patch.object(upscale, "upscale_preserving_audio") as sharpen:
        assert upscale.get_or_create_sharpened(src, "x", tmp_path) == src
    sharpen.assert_not_called()


def test_an_hd_visual_is_not_upscaled(tmp_path: Path) -> None:
    src = tmp_path / "video_x.mp4"
    src.write_bytes(b"\0")
    with mock.patch.object(upscale, "_probe_size", return_value=(1280, 720, 25.0)), \
            mock.patch.object(upscale, "upscale_video") as up:
        assert upscale.get_or_create_upscaled(src, "x", tmp_path) == src
    up.assert_not_called()


def test_a_360p_source_is_still_sharpened(tmp_path: Path) -> None:
    src = tmp_path / "full_y.mp4"
    src.write_bytes(b"\0")
    with mock.patch.object(upscale, "_probe_size", return_value=(640, 360, 25.0)), \
            mock.patch.object(upscale, "upscale_preserving_audio", return_value=tmp_path / "sharp_y.mp4") as sharpen:
        upscale.get_or_create_sharpened(src, "y", tmp_path)
    sharpen.assert_called_once()
