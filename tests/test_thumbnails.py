"""Published videos carry the source video's own thumbnail.

A music-removal upload is the same episode as its source, so it should look
like it in a listing. YouTube otherwise picks a frame out of the middle of the
video, which for a cartoon is usually an unreadable smear of motion.

The image comes from YouTube's own thumbnail CDN rather than from the video
file, so what gets published is the picture the original channel chose — not a
re-derived approximation of it.

Two failure modes shape the design:

* **A thumbnail is cosmetic, an upload is not.** Setting one happens *after*
  the video exists, and anything going wrong there is reported and swallowed.
  Losing a finished render over a 404 on a JPEG would be absurd.
* **Custom thumbnails are a gated feature.** An unverified channel gets a 403
  from the API, so the failure has to say that rather than look like a bug.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest import mock

import pytest

from yt_audio_filter import uploader


# ------------------------------------------------------------- which image


def test_the_largest_thumbnail_is_preferred() -> None:
    """``maxresdefault`` is 1280x720; ``hqdefault`` is 480x360 and would be
    published as a visibly softer image than the original's."""
    assert uploader.THUMBNAIL_PREFERENCE[0] == "maxresdefault"
    assert uploader.THUMBNAIL_PREFERENCE[-1] == "hqdefault"


def test_every_candidate_is_a_real_youtube_thumbnail_name() -> None:
    assert set(uploader.THUMBNAIL_PREFERENCE) <= {
        "maxresdefault",
        "sddefault",
        "hqdefault",
        "mqdefault",
        "default",
    }


def _opener(available: dict):
    """Stands in for ``urlopen``, serving only the names in ``available``."""

    def open_url(url, timeout=None):  # noqa: ARG001
        for name, payload in available.items():
            if f"/{name}.jpg" in url:
                return io.BytesIO(payload)
        raise OSError(f"404 for {url}")

    return open_url


def test_the_first_available_size_wins(tmp_path: Path) -> None:
    got = uploader.fetch_source_thumbnail(
        "abc12345xyz", tmp_path, opener=_opener({"maxresdefault": b"BIG", "hqdefault": b"small"})
    )
    assert got is not None and got.read_bytes() == b"BIG"


def test_it_falls_back_when_the_largest_is_missing(tmp_path: Path) -> None:
    """Plenty of older uploads have no maxres version at all."""
    got = uploader.fetch_source_thumbnail(
        "abc12345xyz", tmp_path, opener=_opener({"hqdefault": b"small"})
    )
    assert got is not None and got.read_bytes() == b"small"


def test_no_thumbnail_at_all_is_not_an_error(tmp_path: Path) -> None:
    """The caller then leaves YouTube's auto-generated one alone."""
    assert uploader.fetch_source_thumbnail("abc12345xyz", tmp_path, opener=_opener({})) is None


def test_an_oversized_image_is_refused(tmp_path: Path) -> None:
    """The API rejects anything over 2 MB, and finding that out at upload time
    wastes the round trip."""
    huge = b"x" * (uploader.MAX_THUMBNAIL_BYTES + 1)
    got = uploader.fetch_source_thumbnail(
        "abc12345xyz", tmp_path, opener=_opener({"maxresdefault": huge, "hqdefault": b"ok"})
    )
    assert got is not None and got.read_bytes() == b"ok", "should skip to a smaller size"


def test_an_empty_response_is_skipped(tmp_path: Path) -> None:
    """YouTube serves a 0-byte body for some missing sizes rather than a 404."""
    got = uploader.fetch_source_thumbnail(
        "abc12345xyz", tmp_path, opener=_opener({"maxresdefault": b"", "hqdefault": b"ok"})
    )
    assert got is not None and got.read_bytes() == b"ok"


# --------------------------------------------------------------- setting it


def _service():
    service = mock.MagicMock()
    service.thumbnails.return_value.set.return_value.execute.return_value = {}
    return service


def test_setting_uploads_the_file_against_the_video(tmp_path: Path) -> None:
    image = tmp_path / "t.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    service = _service()

    with mock.patch("googleapiclient.http.MediaFileUpload", mock.MagicMock()):
        assert uploader.set_thumbnail(service, "uploaded-id", image) is True

    assert service.thumbnails.return_value.set.call_args.kwargs["videoId"] == "uploaded-id"


def test_a_failure_is_reported_but_not_raised(tmp_path: Path) -> None:
    """The video is already published by the time this runs."""
    image = tmp_path / "t.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    service = _service()
    service.thumbnails.return_value.set.return_value.execute.side_effect = RuntimeError("403")

    with mock.patch("googleapiclient.http.MediaFileUpload", mock.MagicMock()):
        assert uploader.set_thumbnail(service, "uploaded-id", image) is False


def test_a_missing_file_is_refused_without_calling_the_api(tmp_path: Path) -> None:
    service = _service()
    assert uploader.set_thumbnail(service, "uploaded-id", tmp_path / "absent.jpg") is False
    service.thumbnails.assert_not_called()


# ------------------------------------------------------------ the two joined


def test_applying_fetches_then_sets(tmp_path: Path) -> None:
    service = _service()
    with mock.patch.object(
        uploader, "fetch_source_thumbnail", return_value=tmp_path / "t.jpg"
    ), mock.patch.object(uploader, "set_thumbnail", return_value=True) as setter:
        assert uploader.apply_source_thumbnail(service, "up-id", "src-id", tmp_path) is True
    assert setter.call_args[0][1] == "up-id"


def test_applying_stops_when_there_is_nothing_to_set(tmp_path: Path) -> None:
    service = _service()
    with mock.patch.object(uploader, "fetch_source_thumbnail", return_value=None), mock.patch.object(
        uploader, "set_thumbnail"
    ) as setter:
        assert uploader.apply_source_thumbnail(service, "up-id", "src-id", tmp_path) is False
    setter.assert_not_called()


def test_applying_never_raises(tmp_path: Path) -> None:
    """Called straight after a successful upload; it must not undo it."""
    service = _service()
    with mock.patch.object(
        uploader, "fetch_source_thumbnail", side_effect=RuntimeError("network gone")
    ):
        assert uploader.apply_source_thumbnail(service, "up-id", "src-id", tmp_path) is False


# ------------------------------------------------------ wired into the upload


def test_a_music_removal_upload_sets_the_source_thumbnail(tmp_path: Path) -> None:
    """The end-to-end path: publishing an episode should leave it looking like
    the episode."""
    video = tmp_path / "out.mp4"
    video.write_bytes(b"\x00" * 16)

    meta = mock.MagicMock()
    meta.title, meta.description, meta.channel, meta.tags = "T", "d", "c", []
    meta.video_id = "src11chars0"

    request = mock.MagicMock()
    request.next_chunk.return_value = (None, {"id": "uploaded-id"})
    videos = mock.MagicMock()
    videos.insert.return_value = request
    service = mock.MagicMock()
    service.videos.return_value = videos

    with mock.patch.object(uploader, "find_youtubeuploader_binary", lambda: None), mock.patch.object(
        uploader, "check_upload_dependencies", lambda: True
    ), mock.patch.object(uploader, "authenticate_youtube", lambda: service), mock.patch(
        "googleapiclient.http.MediaFileUpload", mock.MagicMock()
    ), mock.patch.object(
        uploader, "apply_source_thumbnail", return_value=True
    ) as applied:
        uploader.upload_to_youtube(video, original_metadata=meta, privacy="public")

    assert applied.called, "the upload should carry the source's thumbnail across"
    args = applied.call_args[0]
    assert args[1] == "uploaded-id" and args[2] == "src11chars0"


def test_the_upload_survives_a_thumbnail_failure(tmp_path: Path) -> None:
    video = tmp_path / "out.mp4"
    video.write_bytes(b"\x00" * 16)

    meta = mock.MagicMock()
    meta.title, meta.description, meta.channel, meta.tags = "T", "d", "c", []
    meta.video_id = "src11chars0"

    request = mock.MagicMock()
    request.next_chunk.return_value = (None, {"id": "uploaded-id"})
    videos = mock.MagicMock()
    videos.insert.return_value = request
    service = mock.MagicMock()
    service.videos.return_value = videos

    with mock.patch.object(uploader, "find_youtubeuploader_binary", lambda: None), mock.patch.object(
        uploader, "check_upload_dependencies", lambda: True
    ), mock.patch.object(uploader, "authenticate_youtube", lambda: service), mock.patch(
        "googleapiclient.http.MediaFileUpload", mock.MagicMock()
    ), mock.patch.object(
        uploader, "apply_source_thumbnail", side_effect=RuntimeError("boom")
    ):
        assert uploader.upload_to_youtube(video, original_metadata=meta, privacy="public") == (
            "uploaded-id"
        )


# ------------------------------------------- the description must be readable back


def test_the_description_records_the_source_video() -> None:
    """The duplicate check reads this line back off the channel.

    ``_extract_source_video_id`` looks for ``Original: <url>``, and for a long
    time ``generate_seo_description`` did not write one — so every
    music-removal upload was invisible to ``get_uploaded_source_ids`` and the
    only thing stopping a republish was a state file local to one machine. The
    user runs workers on more than one.
    """
    description = uploader.generate_seo_description(
        original_title="T", original_description="d",
        original_channel="Some Channel", original_video_id="abc12345xyz",
    )
    assert uploader._extract_source_video_id(description) == "abc12345xyz"


def test_the_round_trip_holds_for_any_id() -> None:
    """Ids contain - and _, which a sloppier pattern would cut short."""
    for source in ("U_EhMEOolI0", "itBhCjx-6fc", "_EJXu4QMSew", "OuDp-C9w-vQ"):
        description = uploader.generate_seo_description("t", "d", "c", source)
        assert uploader._extract_source_video_id(description) == source
