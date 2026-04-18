"""Unit tests for yt_audio_filter.ffmpeg_overlay command construction."""

from pathlib import Path

import pytest

from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.ffmpeg_overlay import (
    LoudnormMeasurements,
    _logo_overlay_xy,
    build_filter_graph,
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


def test_logo_overlay_xy_corners() -> None:
    assert _logo_overlay_xy("top-left") == ("20", "20")
    assert _logo_overlay_xy("top-right") == ("main_w-overlay_w-20", "20")
    assert _logo_overlay_xy("bottom-left") == ("20", "main_h-overlay_h-20")
    assert _logo_overlay_xy("bottom-right") == (
        "main_w-overlay_w-20",
        "main_h-overlay_h-20",
    )


def test_logo_overlay_xy_invalid() -> None:
    with pytest.raises(OverlayError):
        _logo_overlay_xy("middle")


def test_filter_graph_without_logo_has_no_overlay_node() -> None:
    graph = build_filter_graph(
        resolution=(1920, 1080), measurements=_fake_measurements(), logo=None
    )
    assert "[2:v]" not in graph
    assert "overlay=" not in graph
    assert "scale=1920:1080" in graph
    assert "[vout]" in graph
    assert "[aout]" in graph
    assert "loudnorm=" in graph
    assert "measured_I=-18.20" in graph
    assert "linear=true" in graph


def test_filter_graph_with_logo_includes_overlay(tmp_path: Path) -> None:
    logo_path = tmp_path / "logo.png"
    logo_path.write_bytes(b"fake")
    graph = build_filter_graph(
        resolution=(1280, 720),
        measurements=_fake_measurements(),
        logo=(logo_path, "top-left"),
    )
    assert "scale=1280:720" in graph
    assert "[2:v]scale=w=iw*0.3:h=-1[logo]" in graph
    assert "[vscaled][logo]overlay=x=20:y=20" in graph


def test_render_command_stream_loop_appears_before_video_input(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.m4a"
    out = tmp_path / "out.mp4"
    cmd = build_render_command(
        video_path=video,
        audio_path=audio,
        output_path=out,
        duration_seconds=123.456,
        measurements=_fake_measurements(),
        logo=None,
    )
    assert cmd[0] == "ffmpeg"

    stream_loop_idx = cmd.index("-stream_loop")
    first_i_idx = cmd.index("-i")
    assert stream_loop_idx < first_i_idx, "-stream_loop must precede the first -i input"
    assert cmd[stream_loop_idx + 1] == "-1"
    assert cmd[first_i_idx + 1] == str(video)


def test_render_command_duration_flag_uses_audio_length(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.m4a"
    out = tmp_path / "out.mp4"
    cmd = build_render_command(
        video_path=video,
        audio_path=audio,
        output_path=out,
        duration_seconds=42.0,
        measurements=_fake_measurements(),
        logo=None,
    )
    t_idx = cmd.index("-t")
    assert cmd[t_idx + 1] == "42.000"
    assert "-shortest" not in cmd


def test_render_command_force_flag_toggles_y_vs_n(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.m4a"
    out = tmp_path / "out.mp4"
    cmd_force = build_render_command(
        video, audio, out, 10.0, _fake_measurements(), force=True
    )
    cmd_no_force = build_render_command(
        video, audio, out, 10.0, _fake_measurements(), force=False
    )
    assert "-y" in cmd_force
    assert "-n" not in cmd_force
    assert "-n" in cmd_no_force
    assert "-y" not in cmd_no_force


def test_render_command_with_logo_adds_third_input(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.m4a"
    out = tmp_path / "out.mp4"
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")

    cmd = build_render_command(
        video_path=video,
        audio_path=audio,
        output_path=out,
        duration_seconds=30.0,
        measurements=_fake_measurements(),
        logo=(logo, "bottom-right"),
    )
    input_positions = [i for i, arg in enumerate(cmd) if arg == "-i"]
    assert len(input_positions) == 3, "Expected 3 inputs: video, audio, logo"
    assert cmd[input_positions[2] + 1] == str(logo)


def test_render_command_without_logo_has_two_inputs(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.m4a"
    out = tmp_path / "out.mp4"
    cmd = build_render_command(
        video_path=video,
        audio_path=audio,
        output_path=out,
        duration_seconds=30.0,
        measurements=_fake_measurements(),
        logo=None,
    )
    input_positions = [i for i, arg in enumerate(cmd) if arg == "-i"]
    assert len(input_positions) == 2


def test_render_command_contains_encoding_defaults(tmp_path: Path) -> None:
    cmd = build_render_command(
        video_path=tmp_path / "v.mp4",
        audio_path=tmp_path / "a.m4a",
        output_path=tmp_path / "o.mp4",
        duration_seconds=1.0,
        measurements=_fake_measurements(),
        logo=None,
    )
    joined = " ".join(cmd)
    assert "-c:v libx264" in joined
    assert "-crf 18" in joined
    assert "-c:a aac" in joined
    assert "-b:a 192k" in joined
    assert "-movflags +faststart" in joined
