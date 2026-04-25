"""Unit tests for the ayah-range mode of overlay_pipeline.

Covers the Phase 2 ``run_overlay_from_ayah_ranges`` orchestrator, which
ties :mod:`ayah_repeater`, :mod:`subtitle_builder`, :mod:`render_presets`,
and the existing visual download / render path together.

All network and FFmpeg side effects are mocked; these run offline.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_audio_filter.cartoon_catalog import CatalogVideo
from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.metadata import OverlayMetadata
from yt_audio_filter.overlay_pipeline import run_overlay_from_ayah_ranges


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


# ---------------------------------------------------------------------------
# Happy-path: build_ayah_audio + render are both invoked, sequencing is right
# ---------------------------------------------------------------------------


@patch("yt_audio_filter.overlay_pipeline.render_overlay")
@patch("yt_audio_filter.overlay_pipeline.download_stream")
@patch("yt_audio_filter.cartoon_catalog.list_videos")
@patch("yt_audio_filter.ayah_repeater.build_ayah_audio")
def test_run_overlay_from_ayah_ranges_happy_path(
    mock_build_audio: MagicMock,
    mock_list_videos: MagicMock,
    mock_download_stream: MagicMock,
    mock_render: MagicMock,
    tmp_path: Path,
) -> None:
    from yt_audio_filter.ayah_repeater import AyahRange

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Catalog has the requested video_id.
    video = _catalog_video("abc123", "Toy Adventures")
    mock_list_videos.return_value = [video, _catalog_video("other", "Other")]

    # build_ayah_audio writes a marker file at the requested output path.
    def _fake_build(ranges, reciter_slug, cache_dir, output, **kw):
        Path(output).write_bytes(b"\x00" * 16)
        return Path(output)

    mock_build_audio.side_effect = _fake_build

    # Visual download returns an mp4 path.
    visual_path = cache_dir / "video_only_abc123.mp4"
    visual_path.write_bytes(b"\x00" * 16)
    mock_download_stream.return_value = visual_path

    # Render writes the marker file.
    def _fake_render(**kwargs):
        kwargs["output_path"].write_bytes(b"\x00" * 16)
        return kwargs["output_path"]

    mock_render.side_effect = _fake_render

    ranges = [AyahRange(surah=1, start=1, end=7, repeats=3, gap_seconds=0.5)]

    result = run_overlay_from_ayah_ranges(
        ranges=ranges,
        reciter_slug="alafasy",
        visual_video_id="abc123",
        metadata=_metadata(),
        cache_dir=cache_dir,
        upload=False,
    )

    # build_ayah_audio called once with the right ranges + reciter.
    assert mock_build_audio.call_count == 1
    call = mock_build_audio.call_args
    # build_ayah_audio is invoked with kwargs (ranges=, reciter_slug=, ...).
    assert list(call.kwargs["ranges"]) == ranges
    assert call.kwargs["reciter_slug"] == "alafasy"

    # Visual download invoked with the catalog URL + video-only mode.
    download_kwargs = mock_download_stream.call_args.kwargs
    assert download_kwargs["url"] == video.url
    assert download_kwargs["mode"] == "video-only"

    # Render received the visual path + the audio file produced upstream.
    render_kwargs = mock_render.call_args.kwargs
    assert render_kwargs["video_path"] == visual_path
    assert render_kwargs["resolution"] == (1920, 1080)
    # No subtitles requested -> subtitles_path is None.
    assert render_kwargs["subtitles_path"] is None

    # Result naming + bookkeeping.
    assert result.output_path.name.startswith("AlFatiha1_7x3")
    assert result.uploaded_video_id is None
    assert result.video_url == video.url


# ---------------------------------------------------------------------------
# Validation paths
# ---------------------------------------------------------------------------


def test_run_overlay_from_ayah_ranges_empty_list_raises(tmp_path: Path) -> None:
    with pytest.raises(OverlayError, match="at least one"):
        run_overlay_from_ayah_ranges(
            ranges=[],
            reciter_slug="alafasy",
            visual_video_id="abc123",
            metadata=_metadata(),
            cache_dir=tmp_path,
        )


@patch("yt_audio_filter.cartoon_catalog.list_videos")
@patch("yt_audio_filter.ayah_repeater.build_ayah_audio")
def test_run_overlay_from_ayah_ranges_unknown_video_id_raises(
    mock_build_audio: MagicMock,
    mock_list_videos: MagicMock,
    tmp_path: Path,
) -> None:
    from yt_audio_filter.ayah_repeater import AyahRange

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    def _fake_build(ranges, reciter_slug, cache_dir, output, **kw):
        Path(output).write_bytes(b"\x00" * 16)
        return Path(output)

    mock_build_audio.side_effect = _fake_build
    mock_list_videos.return_value = [
        _catalog_video("id1"),
        _catalog_video("id2"),
    ]

    with pytest.raises(OverlayError) as excinfo:
        run_overlay_from_ayah_ranges(
            ranges=[AyahRange(1, 1, 7, 1, 0.0)],
            reciter_slug="alafasy",
            visual_video_id="missing-id",
            metadata=_metadata(),
            cache_dir=cache_dir,
        )

    assert "Unknown visual video_id" in excinfo.value.message


# ---------------------------------------------------------------------------
# render_presets integration: preset_slug feeds resolution
# ---------------------------------------------------------------------------


@patch("yt_audio_filter.overlay_pipeline.render_overlay")
@patch("yt_audio_filter.overlay_pipeline.download_stream")
@patch("yt_audio_filter.cartoon_catalog.list_videos")
@patch("yt_audio_filter.ayah_repeater.build_ayah_audio")
def test_run_overlay_from_ayah_ranges_preset_resolution(
    mock_build_audio: MagicMock,
    mock_list_videos: MagicMock,
    mock_download_stream: MagicMock,
    mock_render: MagicMock,
    tmp_path: Path,
) -> None:
    from yt_audio_filter.ayah_repeater import AyahRange

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    video = _catalog_video("abc123")
    mock_list_videos.return_value = [video]

    def _fake_build(ranges, reciter_slug, cache_dir, output, **kw):
        Path(output).write_bytes(b"\x00" * 16)
        return Path(output)

    mock_build_audio.side_effect = _fake_build

    visual_path = cache_dir / "video_only_abc123.mp4"
    visual_path.write_bytes(b"\x00" * 16)
    mock_download_stream.return_value = visual_path

    def _fake_render(**kwargs):
        kwargs["output_path"].write_bytes(b"\x00" * 16)
        return kwargs["output_path"]

    mock_render.side_effect = _fake_render

    run_overlay_from_ayah_ranges(
        ranges=[AyahRange(1, 1, 7, 1, 0.0)],
        reciter_slug="alafasy",
        visual_video_id="abc123",
        metadata=_metadata(),
        cache_dir=cache_dir,
        preset_slug="whatsapp_vertical",
    )

    render_kwargs = mock_render.call_args.kwargs
    assert render_kwargs["resolution"] == (1080, 1920)


# ---------------------------------------------------------------------------
# Auto-vars helper produces the design-doc title shape
# ---------------------------------------------------------------------------


def test_build_ayah_ranges_auto_vars_single_range_with_repeat() -> None:
    from yt_audio_filter.ayah_repeater import AyahRange
    from yt_audio_filter.overlay_pipeline import _build_ayah_ranges_auto_vars

    auto_vars = _build_ayah_ranges_auto_vars(
        ranges=[AyahRange(1, 1, 7, 3, 0.5)],
        reciter_display_name="Mishary Rashid Alafasy",
        visual_title="Toy Adventures",
    )
    # Whole-surah collapses to the surah name (start=1, end=ayah_count).
    assert auto_vars["detected_surah"] == "Al-Fatiha (\u00d73)"
    assert auto_vars["reciter"] == "Mishary Rashid Alafasy"
    # surah_number set when only one range is supplied.
    assert auto_vars["surah_number"] == "1"


def test_build_ayah_ranges_auto_vars_partial_range() -> None:
    from yt_audio_filter.ayah_repeater import AyahRange
    from yt_audio_filter.overlay_pipeline import _build_ayah_ranges_auto_vars

    auto_vars = _build_ayah_ranges_auto_vars(
        ranges=[AyahRange(2, 1, 5, 1, 0.0)],
        reciter_display_name="Sudais",
        visual_title="",
    )
    # Partial range: "Al-Baqarah:1-5"
    assert auto_vars["detected_surah"] == "Al-Baqarah:1-5"


# ---------------------------------------------------------------------------
# Subtitle path is forwarded when burn_subtitles=True
# ---------------------------------------------------------------------------


@patch("yt_audio_filter.overlay_pipeline.render_overlay")
@patch("yt_audio_filter.overlay_pipeline.download_stream")
@patch("yt_audio_filter.cartoon_catalog.list_videos")
@patch("yt_audio_filter.ayah_repeater.build_ayah_audio")
def test_run_overlay_from_ayah_ranges_burns_subtitles(
    mock_build_audio: MagicMock,
    mock_list_videos: MagicMock,
    mock_download_stream: MagicMock,
    mock_render: MagicMock,
    tmp_path: Path,
) -> None:
    from yt_audio_filter.ayah_repeater import AyahRange

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    video = _catalog_video("abc123")
    mock_list_videos.return_value = [video]

    def _fake_build(ranges, reciter_slug, cache_dir, output, **kw):
        Path(output).write_bytes(b"\x00" * 16)
        return Path(output)

    mock_build_audio.side_effect = _fake_build

    visual_path = cache_dir / "video_only_abc123.mp4"
    visual_path.write_bytes(b"\x00" * 16)
    mock_download_stream.return_value = visual_path

    def _fake_render(**kwargs):
        kwargs["output_path"].write_bytes(b"\x00" * 16)
        return kwargs["output_path"]

    mock_render.side_effect = _fake_render

    run_overlay_from_ayah_ranges(
        ranges=[AyahRange(1, 1, 7, 1, 0.0)],
        reciter_slug="alafasy",
        visual_video_id="abc123",
        metadata=_metadata(),
        cache_dir=cache_dir,
        burn_subtitles=True,
    )

    render_kwargs = mock_render.call_args.kwargs
    sub_path = render_kwargs["subtitles_path"]
    assert sub_path is not None
    assert sub_path.exists()
    assert sub_path.suffix == ".ass"
