"""Which client yt-dlp asks with decides what we can actually download.

The pinned ``tv_embedded/ios/web_embedded/android`` cascade is only ever
*offered* format 18 — a combined 640x360 stream — so every cartoon this project
publishes comes down at 360p. The obvious reaction is to drop the pin, because
yt-dlp's default clients list formats up to 1080p and 4K.

That listing is a mirage, and this file exists so nobody spends an afternoon
rediscovering it:

* ``yt-dlp -F`` with the defaults shows 137/399 (1080p).
* ``yt-dlp -f 137 --test`` succeeds — but ``--test`` stops after 10 KB, and the
  first range request is served.
* A real download of the same format returns ``HTTP Error 403``, sometimes at
  once and sometimes after ~10 MB. That is SABR (yt-dlp issue #12482).

Measured 2026-08-30 across nine real sources from this channel: every adaptive
format 403'd on a full download, on all nine, with and without the bgutil PO
Token plugin. So the pin is not what caps quality — it is what makes the
download work at all, and it goes first. The defaults stay behind it as a
fallback, and move to the front on the day yt-dlp ships SABR support.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from yt_audio_filter import youtube


# ------------------------------------------------------- the attempt order


def test_the_working_cascade_is_tried_first() -> None:
    """Anything else costs a failed attempt on every render — see the module
    docstring for the measurement."""
    assert "tv_embedded" in (youtube.YTDLP_CLIENT_ATTEMPTS[0] or [])


def test_the_defaults_survive_as_a_fallback() -> None:
    """So a downloader whose pinned clients stop being served tries something
    else rather than failing outright."""
    assert None in youtube.YTDLP_CLIENT_ATTEMPTS[1:]


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
    assert seen == [CASCADE]


def test_a_failed_attempt_falls_through_to_the_defaults(download) -> None:
    """If YouTube stops serving the pinned clients, the run must not just die."""
    result, seen = download(failing_clients={CASCADE})
    assert result.exists()
    assert len(seen) == 2
    assert seen[1] == (), "the fallback attempt leaves player_client unset"


def test_every_attempt_failing_still_raises(download) -> None:
    from yt_audio_filter.exceptions import YouTubeDownloadError

    every = {(), CASCADE}
    with pytest.raises(YouTubeDownloadError):
        download(failing_clients=every)
