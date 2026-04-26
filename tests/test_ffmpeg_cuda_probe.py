"""Unit tests for ``check_cuda_filters_available`` — the FFmpeg probe
that gates the full-CUDA render path."""

from __future__ import annotations

from unittest.mock import patch

from yt_audio_filter.ffmpeg import check_cuda_filters_available


class _FakeResult:
    def __init__(self, stdout: str = "", stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = 0


def test_cuda_filters_available_when_all_three_present() -> None:
    """Need scale_cuda + overlay_cuda + hwupload_cuda + cuda hwaccel.

    When the FFmpeg build advertises all three filters AND the cuda
    hwaccel, the probe returns True.
    """
    filters_out = (
        " .. scale_cuda      V->V       GPU accelerated video resizer\n"
        " T. overlay_cuda    VV->V      Overlay one video on top of another using CUDA\n"
        " .. hwupload_cuda   V->V       Upload a system memory frame to a CUDA device.\n"
    )
    hwaccels_out = "Hardware acceleration methods:\ncuda\nvulkan\n"

    def fake_run(cmd, *args, **kwargs):
        if "-filters" in cmd:
            return _FakeResult(stdout=filters_out)
        if "-hwaccels" in cmd:
            return _FakeResult(stdout=hwaccels_out)
        return _FakeResult()

    with patch("subprocess.run", side_effect=fake_run):
        assert check_cuda_filters_available() is True


def test_cuda_filters_unavailable_when_overlay_cuda_missing() -> None:
    filters_out = (
        " .. scale_cuda      V->V       GPU accelerated video resizer\n"
        " .. hwupload_cuda   V->V       Upload a system memory frame to a CUDA device.\n"
    )
    hwaccels_out = "Hardware acceleration methods:\ncuda\n"

    def fake_run(cmd, *args, **kwargs):
        if "-filters" in cmd:
            return _FakeResult(stdout=filters_out)
        if "-hwaccels" in cmd:
            return _FakeResult(stdout=hwaccels_out)
        return _FakeResult()

    with patch("subprocess.run", side_effect=fake_run):
        assert check_cuda_filters_available() is False


def test_cuda_filters_unavailable_when_hwaccel_missing() -> None:
    """Filters are listed but the build doesn't expose cuda hwaccel —
    common when the FFmpeg binary was compiled without ``--enable-cuda``
    headers but happens to ship the filter strings. Better safe than
    triggering a runtime crash inside ffmpeg."""
    filters_out = (
        " .. scale_cuda      V->V       GPU accelerated video resizer\n"
        " T. overlay_cuda    VV->V      Overlay one video on top of another using CUDA\n"
        " .. hwupload_cuda   V->V       Upload a system memory frame to a CUDA device.\n"
    )
    hwaccels_out = "Hardware acceleration methods:\nvaapi\nvulkan\n"

    def fake_run(cmd, *args, **kwargs):
        if "-filters" in cmd:
            return _FakeResult(stdout=filters_out)
        if "-hwaccels" in cmd:
            return _FakeResult(stdout=hwaccels_out)
        return _FakeResult()

    with patch("subprocess.run", side_effect=fake_run):
        assert check_cuda_filters_available() is False


def test_cuda_filters_returns_false_on_subprocess_error() -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("no ffmpeg")

    with patch("subprocess.run", side_effect=fake_run):
        assert check_cuda_filters_available() is False
