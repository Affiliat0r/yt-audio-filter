"""Which client yt-dlp asks with decides what we can actually download.

Since 2026-11-21 yt-dlp's default clients come first: on a current yt-dlp
(2026.10.26, yt-dlp-ejs 0.8.0) they download real 1080p in full, while the
pinned ``tv_embedded/ios/web_embedded/android`` cascade, which used to be the
only thing that returned bytes (format 18, 360p, while SABR 403'd every
adaptive download), was bot-blocked outright. The pinned cascade stays as the
fallback. See ``youtube.YTDLP_CLIENT_ATTEMPTS`` for the measurements, and
beware ``--test``: it stops after 10 KB and proves nothing about a full download.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from yt_audio_filter import youtube


# ------------------------------------------------------- the attempt order


def test_the_defaults_are_tried_first() -> None:
    """They are what downloads 1080p — see the module docstring."""
    assert youtube.YTDLP_CLIENT_ATTEMPTS[0] is None


def test_the_pinned_cascade_survives_as_a_fallback() -> None:
    """So a downloader whose default clients stop being served tries something
    else rather than failing outright."""
    assert any("tv_embedded" in (c or []) for c in youtube.YTDLP_CLIENT_ATTEMPTS[1:])


def test_there_is_more_than_one_attempt() -> None:
    assert len(youtube.YTDLP_CLIENT_ATTEMPTS) >= 2


# ----------------------------------------------------------- format choice


def test_music_removal_asks_for_separate_streams_before_a_combined_one() -> None:
    """``best[ext=mp4]`` resolves to format 18 for these videos.

    Putting it first would cap every music-removal render at 360p even when
    1080p is downloadable.
    """
    selector = youtube._STREAM_FORMAT_MAP["video+audio"]
    assert selector.index("bestvideo") < selector.index("best[ext=mp4]")


def test_the_combined_360p_format_stays_available_as_a_last_resort() -> None:
    assert youtube._STREAM_FORMAT_MAP["video+audio"].rstrip().endswith("18")


# --------------------------------------------------------- the retry itself


class _FakeYDL:
    """Stands in for ``yt_dlp.YoutubeDL``, recording the clients it was given."""

    def __init__(self, seen: list, failing: set, output_dir: Path, prefix: str):
        self.seen = seen
        self.failing = failing
        self.output_dir = output_dir
        self.prefix = prefix

    def __call__(self, opts):
        clients = tuple(opts.get("extractor_args", {}).get("youtube", {}).get("player_client", ()))
        self.current = clients
        # ``extract_video_id`` builds a YoutubeDL of its own before any
        # download starts. Only options carrying a format selector are a real
        # download attempt, and only those belong in the record.
        if "format" in opts:
            self.seen.append(clients)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        if self.current in self.failing:
            raise RuntimeError(f"Requested format is not available ({self.current})")
        path = self.output_dir / f"{self.prefix}_U_EhMEOolI0.mp4"
        path.write_bytes(b"\x00" * 32)
        self._path = path
        return {"id": "U_EhMEOolI0", "ext": "mp4"}

    def prepare_filename(self, info):
        return str(self._path)


@pytest.fixture
def download(tmp_path: Path):
    """``download_stream`` with yt-dlp faked out, in ``video+audio`` mode.

    That mode skips the pytubefix stage outright, so what is exercised is the
    yt-dlp client loop and nothing else.
    """

    def run(failing_clients: set):
        seen: list = []
        fake = _FakeYDL(seen, failing_clients, tmp_path, "full")
        with mock.patch.object(youtube, "ensure_ytdlp_available"), mock.patch.dict(
            "sys.modules", {"yt_dlp": SimpleNamespace(YoutubeDL=fake)}
        ):
            result = youtube.download_stream(
                "https://www.youtube.com/watch?v=U_EhMEOolI0",
                tmp_path,
                mode="video+audio",
                use_cache=False,
            )
        return result, seen

    return run


CASCADE = ("tv_embedded", "ios", "web_embedded", "android")


def test_the_first_attempt_wins_and_nothing_else_is_tried(download) -> None:
    """The common case, and the reason ordering matters: no wasted round trip."""
    result, seen = download(failing_clients=set())
    assert result.exists()
    assert seen == [()], "the first attempt leaves player_client unset"


def test_a_failed_attempt_falls_through_to_the_pinned_cascade(download) -> None:
    """If YouTube stops serving the default clients, the run must not just die."""
    result, seen = download(failing_clients={()})
    assert result.exists()
    assert seen == [(), CASCADE]


def test_every_attempt_failing_still_raises(download) -> None:
    from yt_audio_filter.exceptions import YouTubeDownloadError

    every = {(), CASCADE}
    with pytest.raises(YouTubeDownloadError):
        download(failing_clients=every)
