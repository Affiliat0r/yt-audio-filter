"""Unit tests for yt_audio_filter.uploader.

The Google API client is heavyweight and must never be hit in tests; every
test here mocks ``authenticate_youtube`` (and therefore the API service
object) so no network or OAuth calls happen.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_audio_filter import uploader
from yt_audio_filter.uploader import (
    add_to_playlist,
    upload_with_explicit_metadata,
)


def _fake_video_file(tmp_path: Path) -> Path:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake-mp4-bytes")
    return video


def _make_upload_request_mock(video_id: str = "vid123") -> MagicMock:
    """Build a mock that mimics ``youtube.videos().insert(...)`` semantics.

    The real code does ``request.next_chunk()`` in a while-loop until the
    response is non-None. Returning ``(None, {"id": ...})`` on the first call
    exits the loop immediately.
    """
    request = MagicMock(name="UploadRequest")
    request.next_chunk.return_value = (None, {"id": video_id})
    return request


def _make_youtube_mock(video_id: str = "vid123") -> MagicMock:
    """Build a mock youtube service object with videos() and playlistItems()."""
    youtube = MagicMock(name="YouTubeService")
    youtube.videos().insert.return_value = _make_upload_request_mock(video_id)
    # ``execute`` succeeds by default; tests override for failure cases.
    youtube.playlistItems().insert().execute.return_value = {"id": "pi-abc"}
    # Reset call records so test-side assertions only see the calls under test.
    youtube.videos.reset_mock()
    youtube.playlistItems.reset_mock()
    return youtube


def test_upload_with_playlist_calls_playlist_insert(tmp_path: Path) -> None:
    video = _fake_video_file(tmp_path)
    youtube = _make_youtube_mock(video_id="abc123")

    with patch.object(uploader, "find_youtubeuploader_binary", return_value=None), patch.object(
        uploader, "check_upload_dependencies", return_value=True
    ), patch.object(uploader, "authenticate_youtube", return_value=youtube), patch(
        "googleapiclient.http.MediaFileUpload"
    ) as media_mock:
        media_mock.return_value = MagicMock()
        result = upload_with_explicit_metadata(
            video_path=video,
            title="t",
            description="d",
            tags=["x"],
            playlist_id="PL_target",
        )

    assert result == "abc123"
    # videos().insert should have fired exactly once.
    youtube.videos.assert_called()
    youtube.videos.return_value.insert.assert_called_once()
    # playlistItems().insert should have fired with the expected body.
    youtube.playlistItems.assert_called()
    insert_call = youtube.playlistItems.return_value.insert
    insert_call.assert_called_once()
    kwargs = insert_call.call_args.kwargs
    assert kwargs["part"] == "snippet"
    body = kwargs["body"]
    assert body["snippet"]["playlistId"] == "PL_target"
    assert body["snippet"]["resourceId"] == {
        "kind": "youtube#video",
        "videoId": "abc123",
    }


def test_upload_without_playlist_skips_playlist_call(tmp_path: Path) -> None:
    video = _fake_video_file(tmp_path)
    youtube = _make_youtube_mock(video_id="zzz999")

    with patch.object(uploader, "find_youtubeuploader_binary", return_value=None), patch.object(
        uploader, "check_upload_dependencies", return_value=True
    ), patch.object(uploader, "authenticate_youtube", return_value=youtube), patch(
        "googleapiclient.http.MediaFileUpload"
    ) as media_mock:
        media_mock.return_value = MagicMock()
        result = upload_with_explicit_metadata(
            video_path=video,
            title="t",
            description="d",
            tags=["x"],
            playlist_id=None,
        )

    assert result == "zzz999"
    # playlistItems().insert must not have been invoked when no playlist_id.
    youtube.playlistItems.assert_not_called()


def test_upload_with_empty_playlist_id_skips_playlist_call(tmp_path: Path) -> None:
    """An empty-string playlist_id is falsy and must be treated as 'no playlist'."""
    video = _fake_video_file(tmp_path)
    youtube = _make_youtube_mock(video_id="vid_empty")

    with patch.object(uploader, "find_youtubeuploader_binary", return_value=None), patch.object(
        uploader, "check_upload_dependencies", return_value=True
    ), patch.object(uploader, "authenticate_youtube", return_value=youtube), patch(
        "googleapiclient.http.MediaFileUpload"
    ) as media_mock:
        media_mock.return_value = MagicMock()
        result = upload_with_explicit_metadata(
            video_path=video,
            title="t",
            description="d",
            tags=["x"],
            playlist_id="",
        )

    assert result == "vid_empty"
    youtube.playlistItems.assert_not_called()


def test_playlist_add_failure_does_not_fail_upload(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If playlistItems.insert raises, the upload's video_id must still come back.

    We log a warning rather than re-raising — a successful upload must never
    be rolled back over a follow-on playlist failure.
    """
    video = _fake_video_file(tmp_path)
    youtube = _make_youtube_mock(video_id="upload_ok")
    # Wire playlistItems().insert().execute() to raise. The call chain in
    # production is ``youtube.playlistItems().insert(part=..., body=...).execute()``,
    # so we must override the *return value of insert*'s execute.
    failing_request = MagicMock(name="FailingPlaylistRequest")
    failing_request.execute.side_effect = RuntimeError("403 Forbidden")
    youtube.playlistItems.return_value.insert.return_value = failing_request

    with patch.object(uploader, "find_youtubeuploader_binary", return_value=None), patch.object(
        uploader, "check_upload_dependencies", return_value=True
    ), patch.object(uploader, "authenticate_youtube", return_value=youtube), patch(
        "googleapiclient.http.MediaFileUpload"
    ) as media_mock:
        media_mock.return_value = MagicMock()
        with caplog.at_level("WARNING", logger="yt_audio_filter"):
            result = upload_with_explicit_metadata(
                video_path=video,
                title="t",
                description="d",
                tags=["x"],
                playlist_id="PL_doomed",
            )

    # The upload itself succeeded — we still get the video_id back.
    assert result == "upload_ok"
    # And we logged a warning (not an error / exception).
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("playlist add failed" in r.getMessage() for r in warnings), (
        f"Expected a 'playlist add failed' warning, got: {[r.getMessage() for r in warnings]}"
    )


def test_add_to_playlist_invokes_playlistitems_insert() -> None:
    """Direct unit test of the helper — it must call the API with the right body."""
    youtube = MagicMock(name="YouTubeService")
    add_to_playlist(youtube, video_id="vidX", playlist_id="PL_y")

    insert_call = youtube.playlistItems.return_value.insert
    insert_call.assert_called_once()
    kwargs = insert_call.call_args.kwargs
    assert kwargs["part"] == "snippet"
    assert kwargs["body"]["snippet"]["playlistId"] == "PL_y"
    assert kwargs["body"]["snippet"]["resourceId"] == {
        "kind": "youtube#video",
        "videoId": "vidX",
    }
    insert_call.return_value.execute.assert_called_once()


def test_add_to_playlist_propagates_errors_to_caller() -> None:
    """The helper itself does not swallow API errors — the upload helpers do."""
    youtube = MagicMock(name="YouTubeService")
    failing_request = MagicMock()
    failing_request.execute.side_effect = RuntimeError("403 Forbidden")
    youtube.playlistItems.return_value.insert.return_value = failing_request

    with pytest.raises(RuntimeError, match="403 Forbidden"):
        add_to_playlist(youtube, video_id="v", playlist_id="p")
