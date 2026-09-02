"""A Quran render must land in a playlist, not loose on the channel.

The cartoon path files every upload through `workflow_runner.attach_to_playlist`.
The overlay path had no equivalent: `upload_with_explicit_metadata` accepts a
`playlist_id` and `overlay_pipeline` forwards one for ayah-range renders, but
`run_overlay_surahs` never took the argument and `overlay_cli` never supplied
it. So every `yt-quran-overlay --upload` published a video and left it
unfiled — invisible next to the eighty-odd videos already on the channel.

Filing is deliberately not allowed to fail the run. By the time it happens the
video is public; a playlist problem is worth reporting, never worth pretending
the upload did not happen.
"""

from __future__ import annotations

from unittest import mock

import pytest

from yt_audio_filter import overlay_cli


# ------------------------------------------------------- resolving the name


def test_an_existing_playlist_is_reused() -> None:
    """Creating a second "Quran" beside the real one splits the series."""
    playlists = [{"id": "PL-quran", "title": "Quran", "itemCount": 14}]
    with mock.patch("yt_audio_filter.uploader.list_playlists", return_value=playlists), \
         mock.patch("yt_audio_filter.uploader.create_playlist") as create:
        assert overlay_cli.resolve_playlist_id("Quran") == "PL-quran"
    create.assert_not_called()


def test_a_spelling_variant_still_matches() -> None:
    """Same folded matching the cartoon path uses, so "quran" finds "Quran"."""
    playlists = [{"id": "PL-quran", "title": "Quran", "itemCount": 14}]
    with mock.patch("yt_audio_filter.uploader.list_playlists", return_value=playlists):
        assert overlay_cli.resolve_playlist_id("quran") == "PL-quran"


def test_a_missing_playlist_is_created() -> None:
    with mock.patch("yt_audio_filter.uploader.list_playlists", return_value=[]), \
         mock.patch("yt_audio_filter.uploader.create_playlist", return_value="PL-new") as create:
        assert overlay_cli.resolve_playlist_id("Quran") == "PL-new"
    assert create.call_args.kwargs["title"] == "Quran"


def test_no_name_means_no_playlist() -> None:
    assert overlay_cli.resolve_playlist_id("") is None
    assert overlay_cli.resolve_playlist_id(None) is None


def test_a_lookup_failure_does_not_stop_the_render() -> None:
    """No credentials, or a flaky API, must not cost a finished render."""
    with mock.patch(
        "yt_audio_filter.uploader.list_playlists", side_effect=RuntimeError("no creds")
    ):
        assert overlay_cli.resolve_playlist_id("Quran") is None


# ------------------------------------------------------- threaded through


def test_the_surah_render_forwards_the_playlist_to_the_upload(tmp_path) -> None:
    """The whole point: the id has to reach `upload_with_explicit_metadata`."""
    from yt_audio_filter import overlay_pipeline
    import inspect

    signature = inspect.signature(overlay_pipeline.run_overlay_surahs)
    assert "playlist_id" in signature.parameters, (
        "run_overlay_surahs must accept a playlist so the CLI can file the upload"
    )


def test_the_upload_helper_still_accepts_a_playlist() -> None:
    """Guards the far end of the same wire."""
    from yt_audio_filter import uploader
    import inspect

    assert "playlist_id" in inspect.signature(uploader.upload_with_explicit_metadata).parameters


# ---------------------------------------------------------------- the flag


def test_the_cli_offers_a_playlist_flag() -> None:
    parser = overlay_cli.build_parser()
    action = {a.dest: a for a in parser._actions}.get("playlist")
    assert action is not None, "--playlist must exist"


def test_quran_renders_default_to_the_quran_playlist() -> None:
    """Every surah render belongs there; a per-surah playlist is unusable."""
    parser = overlay_cli.build_parser()
    args = parser.parse_args(
        ["--surah", "An-Nas", "--video-channel", "@v", "--audio-channel", "@a",
         "--metadata", "m.json"]
    )
    assert args.playlist == "Quran"


def test_the_default_can_be_turned_off() -> None:
    parser = overlay_cli.build_parser()
    args = parser.parse_args(
        ["--surah", "An-Nas", "--video-channel", "@v", "--audio-channel", "@a",
         "--metadata", "m.json", "--playlist", ""]
    )
    assert args.playlist == ""


# ------------------------------------------------- every mode, not just one


@pytest.mark.parametrize(
    "argv,target",
    [
        (
            ["--surah", "https://youtu.be/aaaaaaaaaaa", "--video-channel", "@v",
             "--audio-channel", "@a"],
            "run_overlay_surahs",
        ),
        (
            ["--surah-number", "114", "--reciter", "ghamdi", "--video-id", "vid11chars"],
            "run_overlay_from_surah_numbers",
        ),
        (
            ["--video-url", "https://youtu.be/bbbbbbbbbbb",
             "--audio-url", "https://youtu.be/ccccccccccc"],
            "run_overlay",
        ),
    ],
    ids=["surah", "numbers", "manual"],
)
def test_every_mode_files_its_upload(argv, target, tmp_path) -> None:
    """The wiring was added to one branch and quietly missing from the others.

    A 13-surah render published and stayed unfiled, because the call that got
    the argument was the manual-mode one three branches above it. Asserting per
    mode is the only thing that catches that.
    """
    from types import SimpleNamespace
    from unittest import mock

    metadata = tmp_path / "m.json"
    metadata.write_text(
        '{"title": "t", "description_template": "d", "tags": [], "privacy_status": "private"}',
        encoding="utf-8",
    )
    seen = {}

    def capture(*args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(output_path=tmp_path / "o.mp4", uploaded_video_id="up-id")

    with mock.patch.object(overlay_cli, target, side_effect=capture), mock.patch.object(
        overlay_cli, "resolve_playlist_id", return_value="PL-quran"
    ):
        overlay_cli.main(argv + ["--metadata", str(metadata), "--upload"])

    assert seen.get("playlist_id") == "PL-quran", f"{target} was not given a playlist"
