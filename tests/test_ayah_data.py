"""Unit tests for yt_audio_filter.ayah_data.

No network: ``ayah_data`` is a pure data + URL-builder module.
"""

from __future__ import annotations

import pytest

from yt_audio_filter.ayah_data import (
    EVERYAYAH_RECITERS,
    ayah_count,
    everyayah_url,
)


# ---------- ayah_count ----------


def test_ayah_count_known_values() -> None:
    """Spot-check well-known canonical numbers."""
    # Al-Fatiha
    assert ayah_count(1) == 7
    # Al-Baqarah - longest surah
    assert ayah_count(2) == 286
    # Al-Kahf
    assert ayah_count(18) == 110
    # Yaseen
    assert ayah_count(36) == 83
    # Ar-Rahman
    assert ayah_count(55) == 78
    # Al-Ikhlas
    assert ayah_count(112) == 4
    # Al-Falaq
    assert ayah_count(113) == 5
    # An-Nas
    assert ayah_count(114) == 6


def test_ayah_count_total_is_6236() -> None:
    """Canonical total across all 114 surahs."""
    total = sum(ayah_count(n) for n in range(1, 115))
    assert total == 6236


def test_ayah_count_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        ayah_count(0)
    with pytest.raises(ValueError):
        ayah_count(115)
    with pytest.raises(ValueError):
        ayah_count(-1)


def test_ayah_count_rejects_bool_and_non_int() -> None:
    with pytest.raises(ValueError):
        ayah_count(True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ayah_count(False)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ayah_count("1")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ayah_count(1.0)  # type: ignore[arg-type]


# ---------- everyayah_url ----------


def test_everyayah_url_zero_padding() -> None:
    """Surah 36 ayah 1 -> 036001.mp3, single-digit ayah -> NNN001.mp3."""
    url = everyayah_url("Alafasy_128kbps", 36, 1)
    assert url == "https://everyayah.com/data/Alafasy_128kbps/036001.mp3"


def test_everyayah_url_padding_for_double_and_triple_digits() -> None:
    # Al-Fatiha ayah 1
    assert everyayah_url("Alafasy_64kbps", 1, 1).endswith("/001001.mp3")
    # Al-Baqarah ayah 255 (Ayat al-Kursi)
    assert everyayah_url("Alafasy_64kbps", 2, 255).endswith("/002255.mp3")
    # An-Nas ayah 6 (last ayah of the Quran)
    assert everyayah_url("Alafasy_64kbps", 114, 6).endswith("/114006.mp3")


def test_everyayah_url_strips_whitespace_in_slug() -> None:
    url = everyayah_url("  Husary_128kbps  ", 1, 1)
    assert url == "https://everyayah.com/data/Husary_128kbps/001001.mp3"


def test_everyayah_url_rejects_empty_slug() -> None:
    with pytest.raises(ValueError):
        everyayah_url("", 1, 1)
    with pytest.raises(ValueError):
        everyayah_url("   ", 1, 1)
    with pytest.raises(ValueError):
        everyayah_url(None, 1, 1)  # type: ignore[arg-type]


def test_everyayah_url_validates_surah() -> None:
    with pytest.raises(ValueError):
        everyayah_url("Alafasy_128kbps", 0, 1)
    with pytest.raises(ValueError):
        everyayah_url("Alafasy_128kbps", 115, 1)


def test_everyayah_url_validates_ayah() -> None:
    # Al-Fatiha has 7 ayat.
    with pytest.raises(ValueError):
        everyayah_url("Alafasy_128kbps", 1, 0)
    with pytest.raises(ValueError):
        everyayah_url("Alafasy_128kbps", 1, 8)
    # An-Nas has 6 ayat.
    with pytest.raises(ValueError):
        everyayah_url("Alafasy_128kbps", 114, 7)


def test_everyayah_url_rejects_bool_ayah() -> None:
    with pytest.raises(ValueError):
        everyayah_url("Alafasy_128kbps", 1, True)  # type: ignore[arg-type]


# ---------- EVERYAYAH_RECITERS mapping ----------


def test_everyayah_reciters_has_required_keys() -> None:
    """Every entry must have display_name, everyayah_path, quranicaudio_slug."""
    assert len(EVERYAYAH_RECITERS) >= 5
    for slug, entry in EVERYAYAH_RECITERS.items():
        assert isinstance(slug, str) and slug == slug.lower()
        for required in ("display_name", "everyayah_path", "quranicaudio_slug"):
            assert required in entry, f"{slug} missing {required}"
            assert entry[required], f"{slug}.{required} is empty"


def test_everyayah_reciters_includes_top_reciters() -> None:
    """Sanity-check that the most popular reciters made the cut."""
    for slug in ("alafasy", "sudais", "husary", "minshawi", "abdulbasit"):
        assert slug in EVERYAYAH_RECITERS


def test_everyayah_reciters_quranicaudio_slugs_match_manifest() -> None:
    """Each quranicaudio_slug must resolve in the existing reciter manifest."""
    from yt_audio_filter.quran_audio_source import get_reciter

    for slug, entry in EVERYAYAH_RECITERS.items():
        # Must not raise; slug must exist in data/reciters.json.
        reciter = get_reciter(entry["quranicaudio_slug"])
        assert reciter.slug == entry["quranicaudio_slug"]


def test_everyayah_url_with_table_lookup() -> None:
    """End-to-end: short slug -> folder slug -> URL."""
    folder = EVERYAYAH_RECITERS["alafasy"]["everyayah_path"]
    url = everyayah_url(folder, 36, 83)
    assert url.endswith("/036083.mp3")
    assert "Alafasy" in url
