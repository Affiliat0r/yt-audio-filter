"""Unit tests for the full-CUDA video pipeline added on top of
``ffmpeg_overlay``. The CUDA path keeps frames on the GPU end-to-end:
``-hwaccel cuda -hwaccel_output_format cuda`` for decode, ``scale_cuda``
for scaling, ``overlay_cuda`` for the logo, ``hwupload_cuda`` to bring
the logo into GPU memory, and NVENC for encode."""

from pathlib import Path

import pytest

from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.ffmpeg_overlay import (
    LoudnormMeasurements,
    build_cuda_filter_graph,
    build_render_command,
)


def _fake_measurements() -> LoudnormMeasurements:
    return LoudnormMeasurements(
        input_i="-18.20",
        input_tp="-2.10",
        input_lra="7.50",
        input_thresh="-28.30",
        target_offset="-0.10",
    )


# -- build_cuda_filter_graph -------------------------------------------------


def test_cuda_filter_graph_without_logo_uses_scale_cuda() -> None:
    graph = build_cuda_filter_graph(
        resolution=(1920, 1080),
        measurements=_fake_measurements(),
        logo=None,
    )
    # GPU-side scaling, no CPU scale.
    assert "scale_cuda=1920:1080" in graph
    assert "scale=1920:1080" not in graph
    # No logo branch present.
    assert "[2:v]" not in graph
    assert "overlay" not in graph
    # Audio loudnorm chain still runs on CPU.
    assert "loudnorm=" in graph
    assert "measured_I=-18.20" in graph
    assert "[vout]" in graph
    assert "[aout]" in graph


def test_cuda_filter_graph_with_logo_uses_overlay_cuda_and_hwupload(tmp_path: Path) -> None:
    logo = tmp_path / "logo.png"
    logo.touch()
    graph = build_cuda_filter_graph(
        resolution=(1920, 1080),
        measurements=_fake_measurements(),
        logo=(logo, "bottom-right"),
    )
    # Logo branch must hit the GPU before the overlay.
    assert "hwupload_cuda" in graph
    assert "overlay_cuda" in graph
    assert "[2:v]" in graph
    # Main video still scaled on GPU.
    assert "scale_cuda=1920:1080" in graph
    # Position from _logo_overlay_xy("bottom-right") survives.
    assert "main_w-overlay_w-20" in graph
    assert "main_h-overlay_h-20" in graph
    assert "[vout]" in graph
    assert "[aout]" in graph


def test_cuda_filter_graph_rejects_subtitles(tmp_path: Path) -> None:
    """Subtitles burn-in goes through libass which only consumes CPU
    frames. Doing the round-trip wipes out the GPU win, so the CUDA
    builder refuses and the caller falls back to the CPU graph."""
    subs = tmp_path / "track.ass"
    subs.touch()
    with pytest.raises(OverlayError, match="subtitles"):
        build_cuda_filter_graph(
            resolution=(1920, 1080),
            measurements=_fake_measurements(),
            logo=None,
            subtitles_path=subs,
        )


def test_cuda_filter_graph_rejects_fill_mode() -> None:
    """``crop`` has no CUDA variant in the bundled FFmpeg, so ``fill``
    requires hwdownload + crop + hwupload — same bridge issue. Refuse
    so the caller falls back to CPU for vertical/square presets."""
    with pytest.raises(OverlayError, match="scale_mode"):
        build_cuda_filter_graph(
            resolution=(1080, 1920),
            measurements=_fake_measurements(),
            logo=None,
            scale_mode="fill",
        )


# -- build_render_command (use_cuda flag) -----------------------------------


def test_render_command_cuda_inserts_hwaccel_before_video_input(tmp_path: Path) -> None:
    cmd = build_render_command(
        video_path=Path("v.mp4"),
        audio_path=Path("a.m4a"),
        output_path=tmp_path / "out.mp4",
        duration_seconds=12.34,
        measurements=_fake_measurements(),
        resolution=(1920, 1080),
        logo=None,
        force=True,
        use_cuda=True,
    )
    # -hwaccel cuda -hwaccel_output_format cuda must come BEFORE the
    # first -i (the video) and AFTER -stream_loop -1.
    i_video = cmd.index("-i")
    assert "-hwaccel" in cmd[:i_video]
    assert "cuda" in cmd[:i_video]
    assert "-hwaccel_output_format" in cmd[:i_video]
    # Belt-and-braces: the hwaccel pair must precede the first -i, in order.
    hw_idx = cmd.index("-hwaccel")
    fmt_idx = cmd.index("-hwaccel_output_format")
    assert hw_idx < fmt_idx < i_video
    # NVENC encoder still selected.
    assert "h264_nvenc" in cmd
    # CUDA path must NOT include ``-pix_fmt yuv420p`` — that triggers an
    # implicit hwdownload back to system memory and undoes the GPU win.
    assert "-pix_fmt" not in cmd


# -- _should_use_cuda dispatch decision -------------------------------------


def test_should_use_cuda_default_is_off() -> None:
    """Auto-default is OFF (opt-in via prefer=True). Empirical benchmark
    on a typical 720p/1080p ~8 minute render showed ~1.0x speedup
    because the bottleneck is NVENC + loudnorm, not the CPU filter
    chain. Defaulting to ON would add code-path complexity for no
    measurable win, so the safer behaviour is opt-in only."""
    from yt_audio_filter.ffmpeg_overlay import _should_use_cuda
    assert _should_use_cuda(
        prefer=None,
        has_subtitles=False,
        scale_mode="fit",
        probe=lambda: True,
    ) is False


def test_should_use_cuda_returns_true_only_when_explicitly_requested() -> None:
    from yt_audio_filter.ffmpeg_overlay import _should_use_cuda
    assert _should_use_cuda(
        prefer=True,
        has_subtitles=False,
        scale_mode="fit",
        probe=lambda: True,
    ) is True


def test_should_use_cuda_falls_back_when_subtitles_present() -> None:
    """Subtitles force a CPU bridge; even prefer=True should pick CPU."""
    from yt_audio_filter.ffmpeg_overlay import _should_use_cuda
    assert _should_use_cuda(
        prefer=True,
        has_subtitles=True,
        scale_mode="fit",
        probe=lambda: True,
    ) is False


def test_should_use_cuda_falls_back_when_scale_mode_fill() -> None:
    """``fill`` needs crop after scale; no crop_cuda exists."""
    from yt_audio_filter.ffmpeg_overlay import _should_use_cuda
    assert _should_use_cuda(
        prefer=True,
        has_subtitles=False,
        scale_mode="fill",
        probe=lambda: True,
    ) is False


def test_should_use_cuda_returns_false_when_probe_fails() -> None:
    from yt_audio_filter.ffmpeg_overlay import _should_use_cuda
    assert _should_use_cuda(
        prefer=True,
        has_subtitles=False,
        scale_mode="fit",
        probe=lambda: False,
    ) is False


def test_should_use_cuda_explicit_off_overrides_auto() -> None:
    """``prefer=False`` always wins, even if everything would be compatible."""
    from yt_audio_filter.ffmpeg_overlay import _should_use_cuda
    assert _should_use_cuda(
        prefer=False,
        has_subtitles=False,
        scale_mode="fit",
        probe=lambda: True,
    ) is False


def test_should_use_cuda_explicit_on_still_blocked_by_incompatibility() -> None:
    """Even with ``prefer=True``, if subtitles/fill/probe make CUDA unsafe,
    fall back to CPU rather than producing a broken render."""
    from yt_audio_filter.ffmpeg_overlay import _should_use_cuda
    assert _should_use_cuda(
        prefer=True,
        has_subtitles=True,
        scale_mode="fit",
        probe=lambda: True,
    ) is False
    assert _should_use_cuda(
        prefer=True,
        has_subtitles=False,
        scale_mode="fill",
        probe=lambda: True,
    ) is False
    assert _should_use_cuda(
        prefer=True,
        has_subtitles=False,
        scale_mode="fit",
        probe=lambda: False,
    ) is False


def test_render_command_cpu_path_unchanged_byte_for_byte(tmp_path: Path) -> None:
    """Existing call sites that don't pass ``use_cuda`` must produce the
    same argv as before — CUDA is opt-in."""
    common = dict(
        video_path=Path("v.mp4"),
        audio_path=Path("a.m4a"),
        output_path=tmp_path / "out.mp4",
        duration_seconds=12.34,
        measurements=_fake_measurements(),
        resolution=(1920, 1080),
        logo=None,
        force=True,
    )
    default = build_render_command(**common)
    explicit_off = build_render_command(**common, use_cuda=False)
    assert default == explicit_off
    # And the CPU path keeps -pix_fmt yuv420p.
    assert "-pix_fmt" in default
    assert "yuv420p" in default
    # And does NOT add hwaccel.
    assert "-hwaccel" not in default
    assert "-hwaccel_output_format" not in default
