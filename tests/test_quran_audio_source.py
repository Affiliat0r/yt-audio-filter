"""Unit tests for yt_audio_filter.quran_audio_source.

Real-network integration is intentionally excluded from this suite. To spot
check the live source manually, run (from the repo root):

    python -c "from pathlib import Path; \
               from yt_audio_filter.quran_audio_source import download_surah; \
               print(download_surah(1, 'alafasy', Path('cache')))"
"""

from __future__ import annotations

import io
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_audio_filter.exceptions import OverlayError, YouTubeDownloadError
from yt_audio_filter.quran_audio_source import (
    Reciter,
    download_surah,
    get_reciter,
    get_surah_url,
    list_reciters,
)


# ---------- manifest ----------


def test_list_reciters_returns_twenty_entries() -> None:
    reciters = list_reciters()
    assert len(reciters) == 20
    slugs = [r.slug for r in reciters]
    # Slugs must be unique.
    assert len(set(slugs)) == 20
    for r in reciters:
        assert isinstance(r, Reciter)
        assert r.slug and r.slug.strip() == r.slug
        assert r.display_name
        assert r.sample_url.startswith("https://")
        assert "{num" in r.url_pattern
        assert r.url_pattern.startswith("https://")


def test_list_reciters_is_stable_between_calls() -> None:
    # Same objects each call (lru_cache); callers get independent lists though.
    a = list_reciters()
    b = list_reciters()
    assert a == b
    # Mutating the returned list must not poison the cache.
    a.clear()
    assert len(list_reciters()) == 20


# ---------- get_reciter ----------


def test_get_reciter_by_slug() -> None:
    r = get_reciter("alafasy")
    assert r.slug == "alafasy"
    assert "Alafasy" in r.display_name


def test_get_reciter_is_case_insensitive() -> None:
    assert get_reciter("ALAFASY").slug == "alafasy"
    assert get_reciter("AlAfAsY").slug == "alafasy"
    assert get_reciter("  alafasy  ").slug == "alafasy"


def test_get_reciter_unknown_raises() -> None:
    with pytest.raises(OverlayError) as exc_info:
        get_reciter("nonesuch-reciter")
    err = exc_info.value
    assert "nonesuch-reciter" in err.message
    # Helpful details list the available slugs.
    assert "alafasy" in err.details


def test_get_reciter_empty_raises() -> None:
    with pytest.raises(OverlayError):
        get_reciter("")
    with pytest.raises(OverlayError):
        get_reciter("   ")


# ---------- get_surah_url ----------


def test_get_surah_url_builds_from_pattern() -> None:
    # alafasy's quranicaudio path is mishaari_raashid_al_3afaasee.
    expected_base = (
        "https://download.quranicaudio.com/quran/mishaari_raashid_al_3afaasee/"
    )
    assert get_surah_url(1, "alafasy") == expected_base + "001.mp3"
    assert get_surah_url(36, "alafasy") == expected_base + "036.mp3"
    assert get_surah_url(114, "alafasy") == expected_base + "114.mp3"


def test_get_surah_url_accepts_reciter_instance() -> None:
    r = get_reciter("sudais")
    url = get_surah_url(2, r)
    assert url.endswith("/002.mp3")
    assert "sudays" in url


def test_get_surah_url_out_of_range_raises() -> None:
    with pytest.raises(OverlayError):
        get_surah_url(0, "alafasy")
    with pytest.raises(OverlayError):
        get_surah_url(115, "alafasy")
    with pytest.raises(OverlayError):
        get_surah_url(-1, "alafasy")


def test_get_surah_url_rejects_bool_and_non_int() -> None:
    with pytest.raises(OverlayError):
        get_surah_url(True, "alafasy")  # type: ignore[arg-type]
    with pytest.raises(OverlayError):
        get_surah_url("1", "alafasy")  # type: ignore[arg-type]


def test_get_surah_url_unknown_reciter_raises() -> None:
    with pytest.raises(OverlayError):
        get_surah_url(1, "not-a-real-slug")


# ---------- download_surah ----------


def _mock_response(body: bytes) -> MagicMock:
    """Build a urlopen() context-manager return value yielding ``body``."""
    cm = MagicMock()
    cm.__enter__.return_value = io.BytesIO(body)
    cm.__exit__.return_value = False
    return cm


def test_download_surah_writes_file_and_returns_path(tmp_path: Path) -> None:
    body = b"fakemp3bytes" * 100
    with patch(
        "yt_audio_filter.quran_audio_source.urllib.request.urlopen",
        return_value=_mock_response(body),
    ) as mocked:
        result = download_surah(1, "alafasy", tmp_path)

    assert result.exists()
    assert result.read_bytes() == body
    assert result.name == "audio_surah_001_alafasy.mp3"
    assert mocked.call_count == 1


def test_download_surah_hits_cache_on_second_call(tmp_path: Path) -> None:
    body = b"fakemp3bytes" * 100
    with patch(
        "yt_audio_filter.quran_audio_source.urllib.request.urlopen",
        return_value=_mock_response(body),
    ) as mocked:
        first = download_surah(1, "alafasy", tmp_path)
    assert mocked.call_count == 1

    # Second call: no mock -> real urlopen would blow up. If caching works
    # we never reach the network at all.
    with patch(
        "yt_audio_filter.quran_audio_source.urllib.request.urlopen",
        side_effect=AssertionError("should not hit network on cache hit"),
    ) as mocked2:
        second = download_surah(1, "alafasy", tmp_path)
    assert mocked2.call_count == 0
    assert second == first
    assert second.read_bytes() == body


def test_download_surah_raises_on_http_error(tmp_path: Path) -> None:
    http_err = urllib.error.HTTPError(
        url="https://example/404.mp3",
        code=404,
        msg="Not Found",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )
    with patch(
        "yt_audio_filter.quran_audio_source.urllib.request.urlopen",
        side_effect=http_err,
    ):
        with pytest.raises(YouTubeDownloadError) as exc_info:
            download_surah(1, "alafasy", tmp_path)

    err = exc_info.value
    assert "alafasy" in err.message
    assert "404" in err.details
    # No partial file left behind.
    assert not (tmp_path / "audio_surah_001_alafasy.mp3").exists()
    assert not (tmp_path / "audio_surah_001_alafasy.mp3.part").exists()


def test_download_surah_raises_on_url_error(tmp_path: Path) -> None:
    with patch(
        "yt_audio_filter.quran_audio_source.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        with pytest.raises(YouTubeDownloadError):
            download_surah(1, "alafasy", tmp_path)


def test_download_surah_raises_on_empty_response(tmp_path: Path) -> None:
    with patch(
        "yt_audio_filter.quran_audio_source.urllib.request.urlopen",
        return_value=_mock_response(b""),
    ):
        with pytest.raises(YouTubeDownloadError):
            download_surah(1, "alafasy", tmp_path)
    # Cleanup: no stale partial file.
    assert not (tmp_path / "audio_surah_001_alafasy.mp3").exists()


def test_download_surah_validates_surah_number(tmp_path: Path) -> None:
    with pytest.raises(OverlayError):
        download_surah(0, "alafasy", tmp_path)
    with pytest.raises(OverlayError):
        download_surah(115, "alafasy", tmp_path)


def test_download_surah_creates_cache_dir(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    body = b"fakemp3bytes"
    with patch(
        "yt_audio_filter.quran_audio_source.urllib.request.urlopen",
        return_value=_mock_response(body),
    ):
        result = download_surah(2, "sudais", nested)
    assert result.parent == nested
    assert nested.is_dir()
