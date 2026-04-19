"""Unit tests for the surah-numbers mode of overlay_pipeline.

Covers:
  * surah_detector.get_surah_info canonical lookup.
  * overlay_pipeline.run_overlay_from_surah_numbers happy path + validation.
  * overlay_pipeline.upload_rendered auto-var construction.
  * overlay_cli argparse wiring for the numbers mode.

All network / FFmpeg side effects are mocked; these run offline.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_audio_filter.cartoon_catalog import CatalogVideo
from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.metadata import OverlayMetadata
from yt_audio_filter.overlay_pipeline import (
    run_overlay_from_surah_numbers,
    upload_rendered,
)
from yt_audio_filter.quran_audio_source import Reciter
from yt_audio_filter.surah_detector import SurahInfo, get_surah_info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _metadata(logo: Path | None = None) -> OverlayMetadata:
    return OverlayMetadata(
        title="$detected_surah - $reciter",
        description_template="Surahs: $detected_surah | Reciter: $reciter",
        description_vars={},
        tags=["quran"],
        logo_path=logo,
    )


def _catalog_video(video_id: str = "abc123", title: str = "Fun Cartoon") -> CatalogVideo:
    return CatalogVideo(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=title,
        duration=900,
        view_count=1000,
        upload_date="20260101",
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        channel_slug="toys",
    )


def _reciter() -> Reciter:
    return Reciter(
        slug="alafasy",
        display_name="Mishary Rashid Alafasy",
        sample_url="https://example.com/sample.mp3",
        url_pattern="https://example.com/alafasy/{num:03d}.mp3",
    )


# ---------------------------------------------------------------------------
# get_surah_info
# ---------------------------------------------------------------------------


def test_get_surah_info_returns_canonical() -> None:
    info = get_surah_info(1)
    assert isinstance(info, SurahInfo)
    assert info.name == "Al-Fatiha"
    assert info.tag == "AlFatiha"
    assert info.number == 1

    info_last = get_surah_info(114)
    assert info_last.name == "An-Nas"
    assert info_last.tag == "AnNas"
    assert info_last.number == 114

    info_at_tin = get_surah_info(95)
    assert info_at_tin.name == "At-Tin"
    assert info_at_tin.tag == "AtTin"


@pytest.mark.parametrize("bad", [0, 115, -1, 200])
def test_get_surah_info_invalid_raises(bad: int) -> None:
    with pytest.raises(ValueError):
        get_surah_info(bad)


def test_get_surah_info_rejects_non_int() -> None:
    with pytest.raises(ValueError):
        get_surah_info("1")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# run_overlay_from_surah_numbers — happy path + sequencing
# ---------------------------------------------------------------------------


@patch("yt_audio_filter.overlay_pipeline.render_overlay")
@patch("yt_audio_filter.overlay_pipeline.download_stream")
@patch("yt_audio_filter.overlay_pipeline.concat_audio")
@patch("yt_audio_filter.cartoon_catalog.list_videos")
@patch("yt_audio_filter.quran_audio_source.download_surah")
@patch("yt_audio_filter.quran_audio_source.get_reciter")
def test_run_overlay_from_surah_numbers_happy_path(
    mock_get_reciter: MagicMock,
    mock_download_surah: MagicMock,
    mock_list_videos: MagicMock,
    mock_concat: MagicMock,
    mock_download_stream: MagicMock,
    mock_render: MagicMock,
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Reciter lookup returns a manifest entry.
    mock_get_reciter.return_value = _reciter()

    # download_surah returns actual tmp files so concat_audio path check holds.
    audio_files = []
    for n in (1, 95):
        p = cache_dir / f"audio_surah_{n:03d}_alafasy.mp3"
        p.write_bytes(b"\x00" * 16)
        audio_files.append(p)
    mock_download_surah.side_effect = audio_files

    # Catalog has the requested video_id.
    video = _catalog_video("abc123", "Toy Adventures")
    mock_list_videos.return_value = [video, _catalog_video("other", "Other")]

    # Visual download returns an mp4 path.
    visual_path = cache_dir / "video_only_abc123.mp4"
    visual_path.write_bytes(b"\x00" * 16)
    mock_download_stream.return_value = visual_path

    # Render writes a marker file so the OverlayResult.output_path is real.
    def _fake_render(**kwargs):
        kwargs["output_path"].write_bytes(b"\x00" * 16)
        return kwargs["output_path"]

    mock_render.side_effect = _fake_render

    result = run_overlay_from_surah_numbers(
        surah_numbers=[1, 95],
        reciter_slug="alafasy",
        visual_video_id="abc123",
        metadata=_metadata(),
        cache_dir=cache_dir,
        upload=False,
    )

    # Sequencing: download_surah called once per surah in order.
    assert mock_download_surah.call_count == 2
    first_call_args = mock_download_surah.call_args_list[0].args
    second_call_args = mock_download_surah.call_args_list[1].args
    assert first_call_args[0] == 1
    assert second_call_args[0] == 95

    # concat_audio called because >1 surah.
    assert mock_concat.called
    concat_inputs = mock_concat.call_args.args[0]
    assert len(concat_inputs) == 2

    # Visual download uses catalog's URL + video-only mode.
    download_kwargs = mock_download_stream.call_args.kwargs
    assert download_kwargs["url"] == video.url
    assert download_kwargs["mode"] == "video-only"

    # Render invoked with the mocked visual path + concatenated audio.
    render_kwargs = mock_render.call_args.kwargs
    assert render_kwargs["video_path"] == visual_path

    # output_path defaults to a tempfile under gettempdir when None.
    assert Path(tempfile.gettempdir()) in result.output_path.parents
    assert result.output_path.name.startswith("AlFatiha_+1more_abc123")
    assert result.output_path.name.endswith(".mp4")
    assert result.uploaded_video_id is None
    assert result.video_url == video.url


@patch("yt_audio_filter.overlay_pipeline.render_overlay")
@patch("yt_audio_filter.overlay_pipeline.download_stream")
@patch("yt_audio_filter.cartoon_catalog.list_videos")
@patch("yt_audio_filter.quran_audio_source.download_surah")
@patch("yt_audio_filter.quran_audio_source.get_reciter")
def test_run_overlay_from_surah_numbers_honors_explicit_output_path(
    mock_get_reciter: MagicMock,
    mock_download_surah: MagicMock,
    mock_list_videos: MagicMock,
    mock_download_stream: MagicMock,
    mock_render: MagicMock,
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    output_path = tmp_path / "custom_output" / "result.mp4"

    mock_get_reciter.return_value = _reciter()
    p = cache_dir / "audio_surah_001_alafasy.mp3"
    p.write_bytes(b"\x00" * 16)
    mock_download_surah.return_value = p
    mock_list_videos.return_value = [_catalog_video("abc123")]
    visual = cache_dir / "video_only_abc123.mp4"
    visual.write_bytes(b"\x00" * 16)
    mock_download_stream.return_value = visual

    def _fake_render(**kwargs):
        kwargs["output_path"].write_bytes(b"\x00" * 16)
        return kwargs["output_path"]

    mock_render.side_effect = _fake_render

    result = run_overlay_from_surah_numbers(
        surah_numbers=[1],
        reciter_slug="alafasy",
        visual_video_id="abc123",
        metadata=_metadata(),
        output_path=output_path,
        cache_dir=cache_dir,
    )

    assert result.output_path == output_path
    assert output_path.exists()
    assert output_path.parent.exists()


# ---------------------------------------------------------------------------
# Validation paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, 115, -3])
def test_run_overlay_from_surah_numbers_bad_number_raises(bad: int, tmp_path: Path) -> None:
    with pytest.raises(OverlayError, match="Invalid surah number"):
        run_overlay_from_surah_numbers(
            surah_numbers=[bad],
            reciter_slug="alafasy",
            visual_video_id="abc123",
            metadata=_metadata(),
            cache_dir=tmp_path,
        )


def test_run_overlay_from_surah_numbers_empty_list_raises(tmp_path: Path) -> None:
    with pytest.raises(OverlayError, match="at least one surah"):
        run_overlay_from_surah_numbers(
            surah_numbers=[],
            reciter_slug="alafasy",
            visual_video_id="abc123",
            metadata=_metadata(),
            cache_dir=tmp_path,
        )


@patch("yt_audio_filter.overlay_pipeline.download_stream")
@patch("yt_audio_filter.cartoon_catalog.list_videos")
@patch("yt_audio_filter.quran_audio_source.download_surah")
@patch("yt_audio_filter.quran_audio_source.get_reciter")
def test_run_overlay_from_surah_numbers_unknown_video_id_raises(
    mock_get_reciter: MagicMock,
    mock_download_surah: MagicMock,
    mock_list_videos: MagicMock,
    mock_download_stream: MagicMock,
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    mock_get_reciter.return_value = _reciter()
    p = cache_dir / "audio_surah_001_alafasy.mp3"
    p.write_bytes(b"\x00" * 16)
    mock_download_surah.return_value = p
    # Catalog returns videos whose ids don't include the requested one.
    mock_list_videos.return_value = [
        _catalog_video("id1"),
        _catalog_video("id2"),
        _catalog_video("id3"),
    ]

    with pytest.raises(OverlayError) as excinfo:
        run_overlay_from_surah_numbers(
            surah_numbers=[1],
            reciter_slug="alafasy",
            visual_video_id="missing-id",
            metadata=_metadata(),
            cache_dir=cache_dir,
        )

    err = excinfo.value
    assert "Unknown visual video_id" in err.message
    # Details list at least one of the available ids so the user can recover.
    assert "id1" in err.details


# ---------------------------------------------------------------------------
# upload_rendered auto-var builder
# ---------------------------------------------------------------------------


@patch("yt_audio_filter.uploader.upload_with_explicit_metadata")
@patch("yt_audio_filter.quran_audio_source.get_reciter")
def test_upload_rendered_builds_correct_auto_vars(
    mock_get_reciter: MagicMock,
    mock_upload: MagicMock,
    tmp_path: Path,
) -> None:
    mock_get_reciter.return_value = _reciter()
    mock_upload.return_value = "YT-VIDEO-ID-123"

    rendered = tmp_path / "rendered.mp4"
    rendered.write_bytes(b"\x00" * 16)

    uploaded_id = upload_rendered(
        rendered_path=rendered,
        metadata=_metadata(),
        surah_numbers=[1, 95, 112],
        reciter_slug="alafasy",
        visual_title="Toy Adventures",
    )

    assert uploaded_id == "YT-VIDEO-ID-123"
    kwargs = mock_upload.call_args.kwargs
    # Title: from template "$detected_surah - $reciter"
    assert "Al-Fatiha + At-Tin + Al-Ikhlas" in kwargs["title"]
    assert "Mishary Rashid Alafasy" in kwargs["title"]
    # Description similarly renders the joined names.
    assert "Al-Fatiha + At-Tin + Al-Ikhlas" in kwargs["description"]
    # Tags/category/privacy forwarded.
    assert kwargs["tags"] == ["quran"]
    assert kwargs["video_path"] == rendered


def test_upload_rendered_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(OverlayError, match="not found"):
        upload_rendered(
            rendered_path=tmp_path / "nope.mp4",
            metadata=_metadata(),
            surah_numbers=[1],
            reciter_slug="alafasy",
        )


def test_upload_rendered_empty_surah_list_raises(tmp_path: Path) -> None:
    rendered = tmp_path / "rendered.mp4"
    rendered.write_bytes(b"\x00")
    with pytest.raises(OverlayError, match="at least one surah"):
        upload_rendered(
            rendered_path=rendered,
            metadata=_metadata(),
            surah_numbers=[],
            reciter_slug="alafasy",
        )


# ---------------------------------------------------------------------------
# CLI wiring — argparse mode detection
# ---------------------------------------------------------------------------


def _ns_with(**kwargs):
    import argparse
    base = dict(
        video_url=None, audio_url=None,
        video_channel=None, audio_channel=None,
        surah=None, count=1,
        surah_numbers=None, reciter=None, video_id=None,
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


class _FakeParser:
    def error(self, msg):
        raise SystemExit(msg)


@patch("yt_audio_filter.quran_audio_source.get_reciter")
def test_cli_numbers_mode_validated(mock_get_reciter: MagicMock) -> None:
    from yt_audio_filter.overlay_cli import _validate_source_args

    mock_get_reciter.return_value = _reciter()

    # Happy path: all three numbers-mode flags set.
    args = _ns_with(surah_numbers=[1, 2], reciter="alafasy", video_id="abc123")
    assert _validate_source_args(args, _FakeParser()) == "numbers"

    # Mixing with manual mode errors.
    mixed = _ns_with(
        video_url="u", audio_url="u",
        surah_numbers=[1], reciter="alafasy", video_id="abc",
    )
    with pytest.raises(SystemExit, match="exactly one mode"):
        _validate_source_args(mixed, _FakeParser())

    # Missing --reciter errors.
    no_reciter = _ns_with(surah_numbers=[1], video_id="abc123")
    with pytest.raises(SystemExit, match="--reciter"):
        _validate_source_args(no_reciter, _FakeParser())

    # Missing --video-id errors.
    no_video = _ns_with(surah_numbers=[1], reciter="alafasy")
    with pytest.raises(SystemExit, match="--video-id"):
        _validate_source_args(no_video, _FakeParser())

    # Out-of-range surah number errors.
    bad_num = _ns_with(surah_numbers=[0], reciter="alafasy", video_id="abc")
    with pytest.raises(SystemExit, match="1..114"):
        _validate_source_args(bad_num, _FakeParser())

    # count > 1 errors in numbers mode.
    big_count = _ns_with(
        surah_numbers=[1], reciter="alafasy", video_id="abc", count=3
    )
    with pytest.raises(SystemExit, match="--count"):
        _validate_source_args(big_count, _FakeParser())


def test_cli_numbers_mode_invalid_reciter_errors() -> None:
    from yt_audio_filter.overlay_cli import _validate_source_args

    args = _ns_with(surah_numbers=[1], reciter="not-a-real-reciter", video_id="abc")
    with pytest.raises(SystemExit):
        _validate_source_args(args, _FakeParser())


def test_cli_numbers_mode_parser_accepts_repeated_flags() -> None:
    from yt_audio_filter.overlay_cli import build_parser

    parser = build_parser()
    parsed = parser.parse_args(
        [
            "--surah-number", "1",
            "--surah-number", "95",
            "--reciter", "alafasy",
            "--video-id", "abc123",
            "--metadata", "meta.json",
        ]
    )
    assert parsed.surah_numbers == [1, 95]
    assert parsed.reciter == "alafasy"
    assert parsed.video_id == "abc123"
