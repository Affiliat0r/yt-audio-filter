"""Unit tests for yt_audio_filter.overlay_cli argument parsing."""

import pytest

from yt_audio_filter.overlay_cli import _parse_resolution, _validate_source_args, build_parser


def _ns_with(**kwargs):
    """Build an argparse-like Namespace with sensible defaults for source args."""
    import argparse
    base = dict(
        video_url=None, audio_url=None,
        video_channel=None, audio_channel=None,
        surah=None, count=1,
        surah_numbers=None, reciter=None, video_id=None,
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


def _capture_parser_error():
    """Helper that returns a fake parser whose .error raises SystemExit."""
    class _P:
        def error(self, msg):
            raise SystemExit(msg)
    return _P()


def test_parse_resolution_valid() -> None:
    assert _parse_resolution("1920x1080") == (1920, 1080)
    assert _parse_resolution("1280X720") == (1280, 720)
    assert _parse_resolution("  640x480 ") == (640, 480)


def test_parse_resolution_invalid_format() -> None:
    with pytest.raises(Exception):
        _parse_resolution("1920-1080")


def test_parse_resolution_non_positive() -> None:
    with pytest.raises(Exception):
        _parse_resolution("0x100")


def test_parser_requires_urls_and_metadata() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--video-url", "https://youtube.com/watch?v=A",
            "--audio-url", "https://youtube.com/watch?v=B",
            "--metadata", "meta.json",
        ]
    )
    assert str(args.cache_dir) == "cache"
    assert str(args.output_dir) == "output"
    # Resolution default is resolved in main() based on --upscale; parser
    # itself leaves it None so main can distinguish "not set" from "set".
    assert args.resolution is None
    assert args.max_duration == 7200.0
    assert args.force is False
    assert args.upload is False
    assert args.logo is None
    assert args.logo_position is None


def test_parser_upload_and_force() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--video-url", "https://youtube.com/watch?v=A",
            "--audio-url", "https://youtube.com/watch?v=B",
            "--metadata", "meta.json",
            "--upload",
            "--force",
            "--logo-position", "bottom-right",
        ]
    )
    assert args.upload is True
    assert args.force is True
    assert args.logo_position == "bottom-right"


def test_parser_accepts_repeated_surah_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--surah", "At-Tin",
            "--surah", "Al-Fatiha",
            "--audio-channel", "@A",
            "--video-channel", "@V",
            "--metadata", "meta.json",
        ]
    )
    assert args.surah == ["At-Tin", "Al-Fatiha"]


def test_validate_manual_mode() -> None:
    args = _ns_with(video_url="u1", audio_url="u2")
    assert _validate_source_args(args, _capture_parser_error()) == "manual"


def test_validate_discovery_mode() -> None:
    args = _ns_with(video_channel="@v", audio_channel="@a")
    assert _validate_source_args(args, _capture_parser_error()) == "discovery"


def test_validate_surah_mode() -> None:
    args = _ns_with(surah=["At-Tin"], video_channel="@v", audio_channel="@a")
    assert _validate_source_args(args, _capture_parser_error()) == "surah"


def test_validate_surah_without_channels_errors() -> None:
    args = _ns_with(surah=["At-Tin"])
    with pytest.raises(SystemExit, match="Surah mode requires"):
        _validate_source_args(args, _capture_parser_error())


def test_validate_mixing_modes_errors() -> None:
    args = _ns_with(video_url="u", audio_url="u", surah=["X"], video_channel="@v", audio_channel="@a")
    with pytest.raises(SystemExit, match="exactly one mode"):
        _validate_source_args(args, _capture_parser_error())


def test_validate_no_mode_errors() -> None:
    args = _ns_with()
    with pytest.raises(SystemExit, match="Must supply one of"):
        _validate_source_args(args, _capture_parser_error())


def test_validate_count_with_surah_errors() -> None:
    args = _ns_with(surah=["X"], video_channel="@v", audio_channel="@a", count=3)
    with pytest.raises(SystemExit, match="--count"):
        _validate_source_args(args, _capture_parser_error())
