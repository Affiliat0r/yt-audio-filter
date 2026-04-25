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


def test_filter_graph_default_scale_mode_is_fit() -> None:
    """Backwards-compat: omitting scale_mode produces the original ``scale=W:H`` chain."""
    graph = build_filter_graph(
        resolution=(1920, 1080), measurements=_fake_measurements(), logo=None
    )
    # The plain scale form (no force_original_aspect_ratio, no crop) is the
    # historical fit behaviour.
    assert "scale=1920:1080" in graph
    assert "force_original_aspect_ratio" not in graph
    assert "crop=" not in graph


def test_filter_graph_fill_mode_uses_scale_increase_and_crop() -> None:
    """Vertical / square presets pass scale_mode='fill' to crop-cover the frame."""
    graph = build_filter_graph(
        resolution=(1080, 1920),
        measurements=_fake_measurements(),
        logo=None,
        scale_mode="fill",
    )
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in graph
    assert "crop=1080:1920" in graph


def test_filter_graph_invalid_scale_mode_raises() -> None:
    with pytest.raises(OverlayError):
        build_filter_graph(
            resolution=(1920, 1080),
            measurements=_fake_measurements(),
            logo=None,
            scale_mode="bogus",
        )


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
    # Either NVENC (GPU) or libx264 (CPU) — both are valid depending on env.
    assert ("-c:v h264_nvenc" in joined) or ("-c:v libx264" in joined)
    if "h264_nvenc" in joined:
        assert "-cq 19" in joined
    else:
        assert "-crf 18" in joined
    assert "-c:a aac" in joined
    assert "-b:a 192k" in joined
    assert "-movflags +faststart" in joined


def test_video_encoder_args_uses_nvenc_when_available(monkeypatch) -> None:
    from yt_audio_filter import ffmpeg_overlay

    monkeypatch.setattr(ffmpeg_overlay, "check_nvenc_available", lambda: True, raising=False)
    # Patch the lazy import target too.
    import yt_audio_filter.ffmpeg as ffmpeg_module
    monkeypatch.setattr(ffmpeg_module, "check_nvenc_available", lambda: True)

    args = ffmpeg_overlay._video_encoder_args()
    assert args[:2] == ["-c:v", "h264_nvenc"]
    assert "-cq" in args


def test_video_encoder_args_falls_back_to_libx264(monkeypatch) -> None:
    from yt_audio_filter import ffmpeg_overlay
    import yt_audio_filter.ffmpeg as ffmpeg_module
    monkeypatch.setattr(ffmpeg_module, "check_nvenc_available", lambda: False)

    args = ffmpeg_overlay._video_encoder_args()
    assert args[:2] == ["-c:v", "libx264"]
    assert "-crf" in args


# ---------------------------------------------------------------------------
# Subtitle burn-in (T1 / V1 follow-up).
# ---------------------------------------------------------------------------


def test_filter_graph_with_subtitles_appends_subtitles_filter(tmp_path: Path) -> None:
    subs = tmp_path / "subs.ass"
    subs.write_text("[Script Info]\n", encoding="utf-8")
    graph = build_filter_graph(
        resolution=(1920, 1080),
        measurements=_fake_measurements(),
        logo=None,
        subtitles_path=subs,
    )
    assert "subtitles=" in graph
    # Path uses forward slashes (libass / Windows-safe).
    assert subs.as_posix() in graph
    # Subtitles run after the scale chain, before the [vout] tag.
    scale_idx = graph.index("scale=1920:1080")
    subs_idx = graph.index("subtitles=")
    vout_idx = graph.index("[vout]")
    assert scale_idx < subs_idx < vout_idx


def test_filter_graph_without_subtitles_unchanged() -> None:
    """Default behaviour: no subtitle filter, byte-identical to before."""
    graph = build_filter_graph(
        resolution=(1920, 1080),
        measurements=_fake_measurements(),
        logo=None,
    )
    assert "subtitles=" not in graph


def test_filter_graph_with_subtitles_and_logo(tmp_path: Path) -> None:
    """Subtitle clause must come *after* the overlay, not inside the [vscaled] node."""
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    subs = tmp_path / "subs.ass"
    subs.write_text("[Script Info]\n", encoding="utf-8")
    graph = build_filter_graph(
        resolution=(1920, 1080),
        measurements=_fake_measurements(),
        logo=(logo, "top-left"),
        subtitles_path=subs,
    )
    overlay_idx = graph.index("overlay=")
    subs_idx = graph.index("subtitles=")
    vout_idx = graph.index("[vout]")
    assert overlay_idx < subs_idx < vout_idx


def test_render_command_with_subtitles_filter(tmp_path: Path) -> None:
    """The full render argv must contain the subtitles= clause when supplied."""
    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.m4a"
    out = tmp_path / "out.mp4"
    subs = tmp_path / "subs.ass"
    subs.write_text("[Script Info]\n", encoding="utf-8")
    cmd = build_render_command(
        video_path=video,
        audio_path=audio,
        output_path=out,
        duration_seconds=10.0,
        measurements=_fake_measurements(),
        logo=None,
        subtitles_path=subs,
    )
    fc_idx = cmd.index("-filter_complex")
    filter_str = cmd[fc_idx + 1]
    assert "subtitles=" in filter_str
    assert subs.as_posix() in filter_str


def test_render_command_without_subtitles_unchanged(tmp_path: Path) -> None:
    """Omitting subtitles_path produces the same argv as before the feature."""
    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.m4a"
    out = tmp_path / "out.mp4"
    cmd_with_default = build_render_command(
        video_path=video,
        audio_path=audio,
        output_path=out,
        duration_seconds=10.0,
        measurements=_fake_measurements(),
        logo=None,
    )
    cmd_explicit_none = build_render_command(
        video_path=video,
        audio_path=audio,
        output_path=out,
        duration_seconds=10.0,
        measurements=_fake_measurements(),
        logo=None,
        subtitles_path=None,
    )
    assert cmd_with_default == cmd_explicit_none
    fc_idx = cmd_with_default.index("-filter_complex")
    filter_str = cmd_with_default[fc_idx + 1]
    assert "subtitles=" not in filter_str


def test_format_subtitles_filter_escapes_single_quotes(tmp_path: Path) -> None:
    """Paths containing ' must survive the filter parser and libass."""
    from yt_audio_filter.ffmpeg_overlay import _format_subtitles_filter

    weird = tmp_path / "o'brien" / "subs.ass"
    weird.parent.mkdir(parents=True, exist_ok=True)
    weird.write_text("[Script Info]\n", encoding="utf-8")
    clause = _format_subtitles_filter(weird)
    # Escaped form: '\''
    assert r"'\''" in clause
    assert clause.startswith("subtitles=filename='")
    assert clause.endswith("'")
