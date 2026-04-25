"""Tests for yt_audio_filter.quran_text."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest

from yt_audio_filter import quran_text
from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.quran_text import (
    AyahText,
    DUTCH_SHIPPED_ID,
    get_ayah_count,
    get_ayah_text,
    get_surah_texts,
)


@pytest.fixture(autouse=True)
def _clear_caches() -> Iterator[None]:
    """Reset module-level lru_caches between tests so disk fixtures don't leak."""
    quran_text._load_arabic.cache_clear()
    quran_text._load_sahih.cache_clear()
    quran_text._load_extra_translation.cache_clear()
    quran_text._canonical_verse_keys.cache_clear()
    yield
    quran_text._load_arabic.cache_clear()
    quran_text._load_sahih.cache_clear()
    quran_text._load_extra_translation.cache_clear()
    quran_text._canonical_verse_keys.cache_clear()


def test_get_ayah_count_known_surahs() -> None:
    assert get_ayah_count(1) == 7
    assert get_ayah_count(2) == 286
    assert get_ayah_count(114) == 6


def test_get_ayah_count_invalid_surah_raises() -> None:
    with pytest.raises(OverlayError):
        get_ayah_count(0)
    with pytest.raises(OverlayError):
        get_ayah_count(115)


def test_get_ayah_text_returns_arabic_and_english(tmp_path: Path) -> None:
    a = get_ayah_text(1, 1, cache_dir=tmp_path)
    assert isinstance(a, AyahText)
    assert a.surah == 1 and a.ayah == 1
    assert "بِسْمِ" in a.arabic
    assert "Merciful" in a.translation_en
    assert a.translation_extra is None


def test_get_ayah_text_invalid_ayah_raises(tmp_path: Path) -> None:
    with pytest.raises(OverlayError):
        get_ayah_text(1, 8, cache_dir=tmp_path)
    with pytest.raises(OverlayError):
        get_ayah_text(0, 1, cache_dir=tmp_path)


def test_get_surah_texts_orders_by_ayah_number(tmp_path: Path) -> None:
    out = get_surah_texts(1, cache_dir=tmp_path)
    assert len(out) == 7
    assert [a.ayah for a in out] == [1, 2, 3, 4, 5, 6, 7]
    # And every entry's surah field is correctly 1.
    assert all(a.surah == 1 for a in out)
    # Sanity-check that the Arabic strings are non-empty and distinct.
    arabics = [a.arabic for a in out]
    assert all(arabics)
    assert len(set(arabics)) == 7


def test_get_ayah_text_caches_locally(tmp_path: Path) -> None:
    """Second call with extra_translation_id must not hit the network."""
    fake_payload = {
        "translations": [
            {"resource_id": 9999, "text": f"verse-{i}"} for i in range(6236)
        ]
    }

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    fake_bytes = json.dumps(fake_payload).encode("utf-8")
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        return FakeResp(fake_bytes)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        a1 = get_ayah_text(1, 1, cache_dir=tmp_path, extra_translation_id=9999)
    assert call_count["n"] == 1
    assert a1.translation_extra == "verse-0"

    # Disk cache file must exist.
    cached_path = tmp_path / "translation_9999.json"
    assert cached_path.exists()

    # Clear the in-process cache so the second call has to read from disk
    # (or, if disk fails, hit the network — which we forbid via the patch).
    quran_text._load_extra_translation.cache_clear()

    def fail_urlopen(req, timeout=None):
        raise AssertionError("Second call should not hit network")

    with patch("urllib.request.urlopen", side_effect=fail_urlopen):
        a2 = get_ayah_text(1, 2, cache_dir=tmp_path, extra_translation_id=9999)
    assert a2.translation_extra == "verse-1"


def test_extra_translation_falls_back_silently_on_network_error(tmp_path: Path) -> None:
    """A failed third-language fetch must not break the primary render."""
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.URLError("simulated DNS failure")

    with patch("urllib.request.urlopen", side_effect=boom):
        a = get_ayah_text(1, 1, cache_dir=tmp_path, extra_translation_id=9999)
    assert a.arabic
    assert a.translation_en
    assert a.translation_extra is None


def test_dutch_shipped_id_constant_is_documented() -> None:
    """DUTCH_SHIPPED_ID is part of the public API and must stay an int."""
    assert isinstance(DUTCH_SHIPPED_ID, int)
    assert DUTCH_SHIPPED_ID > 0
