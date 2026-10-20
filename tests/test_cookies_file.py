"""``YT_COOKIES_FILE`` reaches yt-dlp, and changes nothing when it is unset.

A GitHub runner gets "Sign in to confirm you're not a bot" from every client
without cookies, so CI downloads depend on this. The worker never sets it, so
the other half matters as much: an unset variable must leave its options alone.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from yt_audio_filter import youtube, yt_metadata


class _RecordingYDL:
    """Stands in for ``yt_dlp.YoutubeDL``, keeping every options dict it is given."""

    def __init__(self, seen: list, output_dir: Path):
        self.seen = seen
        self.output_dir = output_dir

    def __call__(self, opts):
        self.seen.append(opts)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        if download:
            self._path = self.output_dir / "full_U_EhMEOolI0.mp4"
            self._path.write_bytes(b"\x00" * 32)
        return {"id": "U_EhMEOolI0", "ext": "mp4", "title": "t"}

    def prepare_filename(self, info):
        return str(self._path)


@pytest.fixture
def recorded(tmp_path: Path):
    """Run a download and a metadata fetch; return the options each attempt used."""

    def run():
        seen: list = []
        fake = _RecordingYDL(seen, tmp_path)
        with (
            mock.patch.object(youtube, "ensure_ytdlp_available"),
            mock.patch.object(yt_metadata, "ensure_ytdlp_available"),
            mock.patch.dict("sys.modules", {"yt_dlp": SimpleNamespace(YoutubeDL=fake)}),
        ):
            url = "https://www.youtube.com/watch?v=U_EhMEOolI0"
            youtube.download_stream(url, tmp_path, mode="video+audio", use_cache=False)
            yt_metadata.fetch_yt_metadata(url)
        # ``extract_video_id`` builds a YoutubeDL of its own, and needs no
        # cookies: when the bot check refuses it, it reads the id out of the
        # URL instead. Only the download attempt and the metadata fetch carry
        # extractor_args, and only they are of interest.
        return [o for o in seen if "extractor_args" in o]

    return run


def test_unset_leaves_the_options_alone(recorded, monkeypatch) -> None:
    monkeypatch.delenv(youtube.COOKIES_FILE_ENV, raising=False)
    attempts = recorded()
    assert len(attempts) == 2
    assert all("cookiefile" not in o for o in attempts)


def test_set_reaches_the_download_and_the_metadata_fetch(recorded, monkeypatch, tmp_path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setenv(youtube.COOKIES_FILE_ENV, str(cookies))
    attempts = recorded()
    assert len(attempts) == 2
    assert all(o.get("cookiefile") == str(cookies) for o in attempts)


def test_a_missing_file_is_ignored_rather_than_fatal(recorded, monkeypatch, tmp_path) -> None:
    """A stale path in the environment must not stop a render."""
    monkeypatch.setenv(youtube.COOKIES_FILE_ENV, str(tmp_path / "gone.txt"))
    attempts = recorded()
    assert all("cookiefile" not in o for o in attempts)
