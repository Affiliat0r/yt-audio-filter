"""Uploads carry the source video's own title.

The uploader used to mint a "transformative" title —
``"Music Removed - keyword keyword keyword"`` — built from keywords scraped out
of the original. The user wants the published video findable under the name
people actually search for, which is the original one.

The 100-character limit still applies, and a title YouTube rejects fails the
upload after the whole render has already happened, so trimming is done here
rather than left to the API.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from yt_audio_filter.uploader import resolve_upload_title


def test_the_source_title_is_used_as_is() -> None:
    title = "Niloya - 2023 / 30 dakika / 5 bolum bir arada"
    assert resolve_upload_title(title) == title


def test_unicode_and_emoji_survive() -> None:
    """Turkish characters and emoji are normal in these titles."""
    title = "Niloya - YENİ BÖLÜM \U0001f60a"
    assert resolve_upload_title(title) == title


def test_an_explicit_override_wins() -> None:
    assert resolve_upload_title("source", explicit="mine") == "mine"


def test_a_too_long_title_is_trimmed_on_a_word_boundary() -> None:
    long_title = " ".join(["word"] * 40)
    out = resolve_upload_title(long_title)
    assert len(out) <= 100
    assert not out.endswith("wor")


def test_a_trimmed_title_is_marked_as_shortened() -> None:
    out = resolve_upload_title("word " * 40)
    assert out.endswith("…")


def test_an_unbroken_run_still_fits() -> None:
    assert len(resolve_upload_title("x" * 300)) <= 100


def test_angle_brackets_are_stripped() -> None:
    """YouTube rejects < and > outright."""
    out = resolve_upload_title("a <b> c")
    assert "<" not in out and ">" not in out


def test_an_empty_source_title_falls_back() -> None:
    """An empty title is rejected by YouTube; a filename beats a 400."""
    assert resolve_upload_title("", fallback="my_render.mp4").strip()


def test_whitespace_is_collapsed() -> None:
    assert resolve_upload_title("a   \n  b") == "a b"


def test_upload_to_youtube_uses_the_source_title(tmp_path) -> None:
    """The end-to-end path, not just the helper.

    The youtubeuploader binary branch needs a ``request.token`` that is not
    present here, so the upload goes through the Python API — which is the path
    in real use on this machine too.
    """
    from yt_audio_filter import uploader

    video = tmp_path / "out.mp4"
    video.write_bytes(b"\x00" * 16)

    meta = MagicMock()
    meta.title = "Niloya - 25 DAKİKA"
    meta.description = "d"
    meta.channel = "c"
    meta.tags = []
    meta.video_id = "abc12345xyz"

    seen = {}
    request = MagicMock()
    request.next_chunk.return_value = (None, {"id": "uploaded-id"})
    videos = MagicMock()

    def capture(part=None, body=None, media_body=None):  # noqa: ARG001
        seen["title"] = body["snippet"]["title"]
        return request

    videos.insert.side_effect = capture
    youtube = MagicMock()
    youtube.videos.return_value = videos

    with patch.object(uploader, "find_youtubeuploader_binary", lambda: None), patch.object(
        uploader, "check_upload_dependencies", lambda: True
    ), patch.object(uploader, "authenticate_youtube", lambda: youtube), patch(
        "googleapiclient.http.MediaFileUpload", MagicMock()
    ):
        uploader.upload_to_youtube(video, original_metadata=meta, privacy="public")

    assert seen["title"] == "Niloya - 25 DAKİKA"
