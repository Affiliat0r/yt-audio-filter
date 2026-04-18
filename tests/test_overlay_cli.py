"""Unit tests for yt_audio_filter.overlay_cli argument parsing."""

import pytest

from yt_audio_filter.overlay_cli import _parse_resolution, build_parser


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
    assert args.resolution == (1920, 1080)
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
