"""Unit tests for ``youtube.download_video_with_metadata`` — the shared
download path used by BOTH the music-removal Streamlit tab and the
legacy ``yt-audio-filter`` CLI.

Replaces the legacy ``download_youtube_video`` chain (Invidious / Piped
/ Cobalt / YTDownloader.exe) with the same application-less chain the
``yt-quran-overlay`` tool uses (pytubefix → yt-dlp). The bonus is a
``VideoMetadata`` shape on top, so the auto-SEO upload path
(``upload_to_youtube(original_metadata=...)``) stays unchanged.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from yt_audio_filter.exceptions import YouTubeDownloadError
from yt_audio_filter.youtube import VideoMetadata, download_video_with_metadata
from yt_audio_filter.yt_metadata import YouTubeMetadata


def _fake_yt_meta(video_id: str = "abc123") -> YouTubeMetadata:
    return YouTubeMetadata(
        video_id=video_id,
        title="Test Video Title",
        channel="Test Channel",
        uploader="Test Channel",
        description="Some description text.",
        tags=["tag1", "tag2", "tag3"],
        duration=125,
    )


def test_returns_video_metadata_with_file_path_and_fields(tmp_path: Path) -> None:
    """Happy path: ``download_stream`` returns a Path; ``fetch_yt_metadata``
    returns metadata; the helper merges them into a VideoMetadata."""
    out = tmp_path / "cache"
    out.mkdir()
    fake_file = out / "video_abc123.mp4"
    fake_file.write_bytes(b"\x00" * 16)

    url = "https://www.youtube.com/watch?v=abc123"

    with patch(
        "yt_audio_filter.youtube.download_stream", return_value=fake_file
    ) as mock_dl, patch(
        "yt_audio_filter.youtube.fetch_yt_metadata",
        return_value=_fake_yt_meta(video_id="abc123"),
    ) as mock_meta:
        result = download_video_with_metadata(url, out, use_cache=True)

    # download_stream was called with mode='video+audio'.
    assert mock_dl.call_count == 1
    kwargs = mock_dl.call_args.kwargs
    assert kwargs["mode"] == "video+audio"
    assert kwargs["use_cache"] is True
    assert mock_meta.call_count == 1

    assert isinstance(result, VideoMetadata)
    assert result.file_path == fake_file
    assert result.video_id == "abc123"
    assert result.title == "Test Video Title"
    assert result.channel == "Test Channel"
    assert result.description == "Some description text."
    assert result.tags == ["tag1", "tag2", "tag3"]
    assert result.duration == 125


def test_metadata_failure_does_not_break_download(tmp_path: Path) -> None:
    """If ``fetch_yt_metadata`` fails (e.g. metadata-only API blip), we
    still return a usable VideoMetadata — file_path is set, the metadata
    fields fall back to safe defaults. The downstream music-removal
    pipeline only really needs ``file_path``; the SEO fields are nice-
    to-have."""
    out = tmp_path / "cache"
    out.mkdir()
    # 11-char video id so ``extract_video_id``'s regex fallback can
    # parse it without yt-dlp lookup (which would itself fail in the
    # test env on a junk id).
    fake_file = out / "video_xyz12345abc.mp4"
    fake_file.write_bytes(b"\x00" * 16)

    url = "https://www.youtube.com/watch?v=xyz12345abc"

    with patch(
        "yt_audio_filter.youtube.download_stream", return_value=fake_file
    ), patch(
        "yt_audio_filter.youtube.fetch_yt_metadata",
        side_effect=YouTubeDownloadError("metadata fetch boom"),
    ):
        result = download_video_with_metadata(url, out)

    assert result.file_path == fake_file
    # video_id can be derived from URL even when metadata fetch fails.
    assert result.video_id == "xyz12345abc"
    assert result.title  # non-empty fallback
    # Tags and channel are sensible defaults.
    assert result.tags == []


def test_download_failure_propagates(tmp_path: Path) -> None:
    """If the underlying ``download_stream`` raises, the helper does not
    swallow it — the caller decides how to handle a hard download
    failure (no GUI fallback in this chain)."""
    out = tmp_path / "cache"
    out.mkdir()
    url = "https://www.youtube.com/watch?v=fail999"

    with patch(
        "yt_audio_filter.youtube.download_stream",
        side_effect=YouTubeDownloadError("all backends failed"),
    ), patch(
        "yt_audio_filter.youtube.fetch_yt_metadata",
        return_value=_fake_yt_meta(video_id="fail999"),
    ):
        with pytest.raises(YouTubeDownloadError, match="all backends failed"):
            download_video_with_metadata(url, out)


def test_use_cache_flag_is_forwarded(tmp_path: Path) -> None:
    out = tmp_path / "cache"
    out.mkdir()
    fake_file = out / "video_abc123.mp4"
    fake_file.write_bytes(b"\x00" * 16)
    url = "https://www.youtube.com/watch?v=abc123"

    with patch(
        "yt_audio_filter.youtube.download_stream", return_value=fake_file
    ) as mock_dl, patch(
        "yt_audio_filter.youtube.fetch_yt_metadata",
        return_value=_fake_yt_meta(video_id="abc123"),
    ):
        download_video_with_metadata(url, out, use_cache=False)
    assert mock_dl.call_args.kwargs["use_cache"] is False
