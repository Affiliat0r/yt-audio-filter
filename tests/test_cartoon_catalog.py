"""Unit tests for yt_audio_filter.cartoon_catalog."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_audio_filter.cartoon_catalog import (
    CATALOG_CACHE_FILENAME,
    THUMBNAIL_SUBDIR,
    CartoonChannel,
    CatalogVideo,
    ensure_thumbnail,
    list_videos,
    load_channels,
)
from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.scraper import VideoInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_CONFIG = REPO_ROOT / "config" / "channels.json"


def _channel(slug: str = "ch1") -> CartoonChannel:
    return CartoonChannel(
        slug=slug,
        handle=f"@{slug}",
        url=f"https://www.youtube.com/@{slug}",
        display_name=slug.title(),
    )


def _video_info(video_id: str) -> VideoInfo:
    return VideoInfo(
        video_id=video_id,
        title=f"Title {video_id}",
        url=f"https://www.youtube.com/watch?v={video_id}",
        duration=300,
        view_count=1234,
        upload_date="20260101",
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    )


# ---------------------------------------------------------------------------
# load_channels
# ---------------------------------------------------------------------------


def test_load_channels_seed_has_five_entries() -> None:
    channels = load_channels(SEED_CONFIG)
    assert len(channels) == 5
    # Toy Factory must be first.
    assert channels[0].slug == "toyfactorycartoon"
    for ch in channels:
        assert ch.slug and ch.handle and ch.url and ch.display_name
        assert ch.handle.startswith("@")
        assert ch.url.startswith("https://www.youtube.com/")


def test_load_channels_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(OverlayError):
        load_channels(missing)


def test_load_channels_rejects_malformed(tmp_path: Path) -> None:
    bad = tmp_path / "channels.json"
    bad.write_text("{not valid json]", encoding="utf-8")
    with pytest.raises(OverlayError):
        load_channels(bad)


def test_load_channels_rejects_missing_fields(tmp_path: Path) -> None:
    bad = tmp_path / "channels.json"
    bad.write_text(
        json.dumps({"channels": [{"slug": "x", "handle": "@x"}]}),
        encoding="utf-8",
    )
    with pytest.raises(OverlayError):
        load_channels(bad)


# ---------------------------------------------------------------------------
# list_videos — caching behaviour
# ---------------------------------------------------------------------------


def test_list_videos_uses_cache_within_ttl(tmp_path: Path) -> None:
    ch = _channel("alpha")
    fake = MagicMock(return_value=iter([_video_info("a1"), _video_info("a2")]))

    with patch("yt_audio_filter.scraper.get_channel_videos", fake):
        first = list_videos(
            channels=[ch], max_per_channel=10, cache_dir=tmp_path, ttl_seconds=3600
        )
        # Fresh generator each call in case impl exhausts it.
        fake.return_value = iter([_video_info("a1"), _video_info("a2")])
        second = list_videos(
            channels=[ch], max_per_channel=10, cache_dir=tmp_path, ttl_seconds=3600
        )

    assert [v.video_id for v in first] == ["a1", "a2"]
    assert [v.video_id for v in second] == ["a1", "a2"]
    assert fake.call_count == 1
    # Cache file written after first call.
    assert (tmp_path / CATALOG_CACHE_FILENAME).exists()


def test_list_videos_refreshes_when_stale(tmp_path: Path) -> None:
    ch = _channel("beta")
    cache_path = tmp_path / CATALOG_CACHE_FILENAME
    old_time = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    stale = {
        "generated_at": old_time,
        "channels": {
            "beta": {
                "scraped_at": old_time,
                "videos": [
                    {
                        "video_id": "old1",
                        "url": "https://www.youtube.com/watch?v=old1",
                        "title": "old",
                        "duration": 100,
                        "view_count": 1,
                        "upload_date": "20250101",
                        "thumbnail_url": "https://i.ytimg.com/vi/old1/hqdefault.jpg",
                        "channel_slug": "beta",
                    }
                ],
            }
        },
    }
    cache_path.write_text(json.dumps(stale), encoding="utf-8")

    fake = MagicMock(return_value=iter([_video_info("b1")]))
    with patch("yt_audio_filter.scraper.get_channel_videos", fake):
        result = list_videos(
            channels=[ch], max_per_channel=10, cache_dir=tmp_path, ttl_seconds=3600
        )

    assert fake.call_count == 1
    assert [v.video_id for v in result] == ["b1"]


def test_list_videos_merges_multiple_channels(tmp_path: Path) -> None:
    ch1 = _channel("alpha")
    ch2 = _channel("beta")

    responses = {
        ch1.url: [_video_info("a1"), _video_info("a2")],
        ch2.url: [_video_info("b1"), _video_info("b2")],
    }

    def fake_scrape(channel_url: str, max_videos=None, include_shorts=False):
        return iter(responses[channel_url])

    with patch("yt_audio_filter.scraper.get_channel_videos", side_effect=fake_scrape):
        result = list_videos(
            channels=[ch1, ch2], max_per_channel=10, cache_dir=tmp_path, ttl_seconds=3600
        )

    assert [v.video_id for v in result] == ["a1", "a2", "b1", "b2"]
    assert [v.channel_slug for v in result] == ["alpha", "alpha", "beta", "beta"]


def test_list_videos_dedupes_across_channels(tmp_path: Path) -> None:
    ch1 = _channel("alpha")
    ch2 = _channel("beta")

    responses = {
        ch1.url: [_video_info("shared"), _video_info("a2")],
        ch2.url: [_video_info("shared"), _video_info("b2")],
    }

    def fake_scrape(channel_url: str, max_videos=None, include_shorts=False):
        return iter(responses[channel_url])

    with patch("yt_audio_filter.scraper.get_channel_videos", side_effect=fake_scrape):
        result = list_videos(
            channels=[ch1, ch2], max_per_channel=10, cache_dir=tmp_path, ttl_seconds=3600
        )

    ids = [v.video_id for v in result]
    assert ids == ["shared", "a2", "b2"]
    # First appearance wins, so "shared" stays tagged to alpha.
    assert result[0].channel_slug == "alpha"


# ---------------------------------------------------------------------------
# ensure_thumbnail
# ---------------------------------------------------------------------------


def _catalog_video(video_id: str = "vid1") -> CatalogVideo:
    return CatalogVideo(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title="t",
        duration=100,
        view_count=0,
        upload_date="20260101",
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        channel_slug="alpha",
    )


def test_ensure_thumbnail_downloads_once(tmp_path: Path) -> None:
    video = _catalog_video("abc")

    fake_resp = MagicMock()
    fake_resp.read.return_value = b"\xff\xd8\xff\xe0jpegbytes"
    fake_resp.__enter__ = MagicMock(return_value=fake_resp)
    fake_resp.__exit__ = MagicMock(return_value=False)

    with patch(
        "yt_audio_filter.cartoon_catalog.urlopen", return_value=fake_resp
    ) as mock_urlopen:
        first = ensure_thumbnail(video, cache_dir=tmp_path)
        second = ensure_thumbnail(video, cache_dir=tmp_path)

    assert first == second == tmp_path / THUMBNAIL_SUBDIR / "abc.jpg"
    assert first.exists()
    assert first.read_bytes() == b"\xff\xd8\xff\xe0jpegbytes"
    assert mock_urlopen.call_count == 1


def test_ensure_thumbnail_skips_existing_file(tmp_path: Path) -> None:
    video = _catalog_video("def")
    dest_dir = tmp_path / THUMBNAIL_SUBDIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "def.jpg"
    dest.write_bytes(b"precached")

    with patch("yt_audio_filter.cartoon_catalog.urlopen") as mock_urlopen:
        result = ensure_thumbnail(video, cache_dir=tmp_path)

    assert result == dest
    assert dest.read_bytes() == b"precached"
    mock_urlopen.assert_not_called()
