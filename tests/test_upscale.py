"""Unit tests for yt_audio_filter.upscale (no real GPU work)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from yt_audio_filter.exceptions import OverlayError, PrerequisiteError
from yt_audio_filter.upscale import (
    check_realesrgan_available,
    ensure_realesrgan_available,
    get_or_create_upscaled,
)


def test_check_realesrgan_available_matches_filesystem() -> None:
    # The binary is checked in under tools/realesrgan/; whatever the current
    # repo state says should match the check.
    from yt_audio_filter.upscale import REALESRGAN_BIN

    assert check_realesrgan_available() == REALESRGAN_BIN.exists()


def test_ensure_realesrgan_raises_when_missing(tmp_path, monkeypatch) -> None:
    fake_bin = tmp_path / "does_not_exist.exe"
    monkeypatch.setattr("yt_audio_filter.upscale.REALESRGAN_BIN", fake_bin)
    with pytest.raises(PrerequisiteError, match="realesrgan-ncnn-vulkan"):
        ensure_realesrgan_available()


def test_get_or_create_upscaled_returns_cached(tmp_path) -> None:
    # If the cache already has upscaled_<id>.mp4 with content, return it without
    # invoking the expensive pipeline.
    cache = tmp_path / "cache"
    cache.mkdir()
    cached = cache / "upscaled_abc.mp4"
    cached.write_bytes(b"\x00" * 1024)  # non-empty

    visual = tmp_path / "src.mp4"
    visual.write_bytes(b"\x00" * 16)

    # Patch upscale_video to fail loudly if called — we expect the cache path.
    with patch("yt_audio_filter.upscale.upscale_video") as mock_up:
        result = get_or_create_upscaled(visual, "abc", cache)
    mock_up.assert_not_called()
    assert result == cached


def test_get_or_create_upscaled_invokes_upscale_on_miss(tmp_path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    visual = tmp_path / "src.mp4"
    visual.write_bytes(b"\x00" * 16)

    expected_dst = cache / "upscaled_xyz.mp4"

    def _fake_upscale(src, dst, *a, **kw):
        Path(dst).write_bytes(b"\x00" * 32)
        return Path(dst)

    with patch("yt_audio_filter.upscale.upscale_video", side_effect=_fake_upscale) as mock_up:
        result = get_or_create_upscaled(visual, "xyz", cache)
    mock_up.assert_called_once()
    assert result == expected_dst
    assert result.exists()


def test_get_or_create_upscaled_ignores_zero_byte_cache(tmp_path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "upscaled_zero.mp4").write_bytes(b"")  # 0 bytes — treat as missing

    visual = tmp_path / "src.mp4"
    visual.write_bytes(b"\x00" * 16)

    def _fake_upscale(src, dst, *a, **kw):
        Path(dst).write_bytes(b"\x00" * 64)
        return Path(dst)

    with patch("yt_audio_filter.upscale.upscale_video", side_effect=_fake_upscale) as mock_up:
        get_or_create_upscaled(visual, "zero", cache)
    mock_up.assert_called_once()


def test_upscale_video_raises_on_missing_source(tmp_path) -> None:
    from yt_audio_filter.upscale import upscale_video

    missing = tmp_path / "nope.mp4"
    with pytest.raises(OverlayError, match="not found"):
        upscale_video(missing, tmp_path / "out.mp4")
