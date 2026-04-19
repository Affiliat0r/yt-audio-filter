"""Unit tests for yt_audio_filter.surah_resolver."""

from unittest.mock import patch

import pytest

from yt_audio_filter.channel_discovery import Candidate
from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.surah_resolver import resolve_surahs


def _cand(vid: str, title: str, duration: int = 600) -> Candidate:
    return Candidate(
        video_id=vid,
        url=f"https://youtu.be/{vid}",
        title=title,
        duration=duration,
        view_count=0,
    )


def test_happy_path_three_surahs_returned_in_requested_order() -> None:
    # Channel order (newest first): Ar-Rahman, Al-Fatiha, At-Tin.
    channel = [
        _cand("v1", "Surah Ar-Rahman - Salim Bahanan"),
        _cand("v2", "Surah Al-Fatiha - Salim Bahanan"),
        _cand("v3", "Surah At-Tin - Salim Bahanan"),
    ]
    with patch(
        "yt_audio_filter.surah_resolver.fetch_candidates",
        return_value=channel,
    ):
        result = resolve_surahs(
            ["At-Tin", "Al-Fatiha", "Ar-Rahman"],
            "@fake",
        )
    # Returned in requested order, not channel order.
    assert [c.video_id for c in result] == ["v3", "v2", "v1"]


def test_case_insensitive_matching() -> None:
    channel = [_cand("v1", "Surah Ar-Rahman - Beautiful recitation")]
    with patch(
        "yt_audio_filter.surah_resolver.fetch_candidates",
        return_value=channel,
    ):
        result = resolve_surahs(["AR-RAHMAN"], "@fake")
    assert len(result) == 1
    assert result[0].video_id == "v1"


def test_case_insensitive_matching_lowercase() -> None:
    channel = [_cand("v1", "Surah Ar-Rahman - Beautiful recitation")]
    with patch(
        "yt_audio_filter.surah_resolver.fetch_candidates",
        return_value=channel,
    ):
        result = resolve_surahs(["ar-rahman"], "@fake")
    assert len(result) == 1
    assert result[0].video_id == "v1"


def test_newest_wins_on_duplicate_surah() -> None:
    # Channel returns newest first; two videos match Al-Fatiha.
    channel = [
        _cand("newest", "Surah Al-Fatiha 2026 edition"),
        _cand("older", "Surah Al-Fatiha 2020 edition"),
    ]
    with patch(
        "yt_audio_filter.surah_resolver.fetch_candidates",
        return_value=channel,
    ):
        result = resolve_surahs(["Al-Fatiha"], "@fake")
    assert len(result) == 1
    assert result[0].video_id == "newest"


def test_missing_surah_raises_overlay_error_with_available_sample() -> None:
    channel = [
        _cand("v1", "Surah Al-Fatiha"),
        _cand("v2", "Surah Ar-Rahman"),
        _cand("v3", "Surah At-Tin"),
    ]
    with patch(
        "yt_audio_filter.surah_resolver.fetch_candidates",
        return_value=channel,
    ):
        with pytest.raises(OverlayError) as exc_info:
            resolve_surahs(["Al-Baqarah", "Al-Fatiha"], "@fake")
    err = exc_info.value
    # Missing surah name appears in the message.
    assert "Al-Baqarah" in err.message
    # Available surah names appear in details.
    assert "Al-Fatiha" in err.details
    assert "Ar-Rahman" in err.details
    assert "At-Tin" in err.details


def test_missing_surah_lists_all_missing_names() -> None:
    channel = [_cand("v1", "Surah Al-Fatiha")]
    with patch(
        "yt_audio_filter.surah_resolver.fetch_candidates",
        return_value=channel,
    ):
        with pytest.raises(OverlayError) as exc_info:
            resolve_surahs(["Al-Baqarah", "Ar-Rahman"], "@fake")
    err = exc_info.value
    assert "Al-Baqarah" in err.message
    assert "Ar-Rahman" in err.message


def test_empty_input_raises_overlay_error() -> None:
    with pytest.raises(OverlayError) as exc_info:
        resolve_surahs([], "@fake")
    assert "No surahs requested" in exc_info.value.message


def test_unrelated_videos_are_silently_skipped() -> None:
    # Some videos have no detectable surah — should not error, should not
    # appear in results.
    channel = [
        _cand("junk1", "Cooking tutorial: making biryani"),
        _cand("v1", "Surah At-Tin - Full recitation"),
        _cand("junk2", "Channel announcement and update"),
        _cand("v2", "Surah Al-Fatiha"),
        _cand("junk3", "Random vlog footage"),
    ]
    with patch(
        "yt_audio_filter.surah_resolver.fetch_candidates",
        return_value=channel,
    ):
        result = resolve_surahs(["Al-Fatiha", "At-Tin"], "@fake")
    assert [c.video_id for c in result] == ["v2", "v1"]


def test_available_sample_capped_at_ten() -> None:
    # Create 15 distinct surahs on the channel, request one that's missing.
    titles = [
        "Surah Al-Fatiha",
        "Surah Al-Baqarah",
        "Surah Al-Imran",
        "Surah An-Nisa",
        "Surah Al-Maidah",
        "Surah Al-Anam",
        "Surah Al-Araf",
        "Surah Al-Anfal",
        "Surah At-Tawbah",
        "Surah Yunus",
        "Surah Hud",
        "Surah Yusuf",
        "Surah Ar-Rad",
        "Surah Ibrahim",
        "Surah Al-Hijr",
    ]
    channel = [_cand(f"v{i}", t) for i, t in enumerate(titles)]
    with patch(
        "yt_audio_filter.surah_resolver.fetch_candidates",
        return_value=channel,
    ):
        with pytest.raises(OverlayError) as exc_info:
            resolve_surahs(["Ar-Rahman"], "@fake")
    details = exc_info.value.details
    # Indicator that the sample was truncated.
    assert "more" in details


def test_prefers_single_surah_over_compilation() -> None:
    # Compilation appears FIRST (newer) in channel order, standalone second.
    # New scoring must still pick the standalone for An-Nas because it has
    # only one detected surah and is shorter.
    channel = [
        _cand("comp", "Juz 30 - Surah Adh Dhuha - Surah An Naas", duration=1291),
        _cand("standalone", "Surah An Naas - Salim Bahanan", duration=66),
    ]
    with patch(
        "yt_audio_filter.surah_resolver.fetch_candidates",
        return_value=channel,
    ):
        result = resolve_surahs(["An-Nas"], "@fake")
    assert result[0].video_id == "standalone"


def test_falls_back_to_compilation_when_no_standalone_exists() -> None:
    # Only a compilation mentions Al-Fil → the compilation IS the best we can do.
    channel = [
        _cand("comp", "Surah Al Fatiha & Surah Al Fil - Salim Bahanan", duration=180),
    ]
    with patch(
        "yt_audio_filter.surah_resolver.fetch_candidates",
        return_value=channel,
    ):
        result = resolve_surahs(["Al-Fil"], "@fake")
    assert result[0].video_id == "comp"


def test_shorter_wins_among_single_surah_candidates() -> None:
    # Two clean single-surah titles; shorter one wins.
    channel = [
        _cand("long", "Surah Al-Fatiha - 10 minute edition", duration=600),
        _cand("short", "Surah Al-Fatiha", duration=90),
    ]
    with patch(
        "yt_audio_filter.surah_resolver.fetch_candidates",
        return_value=channel,
    ):
        result = resolve_surahs(["Al-Fatiha"], "@fake")
    assert result[0].video_id == "short"


def test_duplicate_requested_names_both_resolve_to_same_candidate() -> None:
    # If the user asks for the same surah twice, we return the same
    # Candidate twice (let the caller decide what that means).
    channel = [_cand("v1", "Surah Al-Fatiha")]
    with patch(
        "yt_audio_filter.surah_resolver.fetch_candidates",
        return_value=channel,
    ):
        result = resolve_surahs(["Al-Fatiha", "Al-Fatiha"], "@fake")
    assert len(result) == 2
    assert result[0].video_id == "v1"
    assert result[1].video_id == "v1"
