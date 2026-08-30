"""Playlists are matched on a folded name, not an exact one.

Matching exactly meant `hophop baykus` never found the existing
`hop hop baykus`, so the command made a second playlist for the same series and
split five videos across two lists. Turkish spelling makes it worse: the real
title is `Hop Hop Baykuş`, and the `ş` alone was enough to miss.
"""

from __future__ import annotations

import pytest

from yt_audio_filter.workflow_runner import playlist_key, pick_playlist


@pytest.mark.parametrize(
    "a,b",
    [
        ("hop hop baykus", "hophop baykus"),
        ("Hop Hop Baykus", "hop hop baykus"),
        ("HopHop-Baykus", "hop hop baykus"),
        ("Abc Alfabet", "abc  alfabet"),
        ("Quran", "quran"),
    ],
)
def test_names_that_should_meet(a: str, b: str) -> None:
    assert playlist_key(a) == playlist_key(b)


@pytest.mark.parametrize(
    "turkish,plain",
    [
        ("Hop Hop Baykuş", "hop hop baykus"),
        ("Niloya YENİ BÖLÜM", "niloya yeni bolum"),
        ("Çizgi Film", "cizgi film"),
        ("Kırmızı", "kirmizi"),
    ],
)
def test_turkish_spelling_folds_to_the_plain_form(turkish: str, plain: str) -> None:
    """The channel names its playlists in Turkish; requests get typed in ASCII."""
    assert playlist_key(turkish) == playlist_key(plain)


@pytest.mark.parametrize("a,b", [("Riko", "Niloya"), ("Quran", "Quraan"), ("abc", "abcd")])
def test_genuinely_different_names_stay_apart(a: str, b: str) -> None:
    """Folding must not become fuzzy matching — Quran and Quraan are not the
    same playlist, unlike surah names where doubled vowels are one word."""
    assert playlist_key(a) != playlist_key(b)


def test_an_empty_name_has_no_key() -> None:
    assert playlist_key("") == ""
    assert playlist_key("   ") == ""


# ------------------------------------------------------------ choosing


def _pl(pid: str, title: str, count: int) -> dict:
    return {"id": pid, "title": title, "itemCount": count}


def test_an_exact_match_wins() -> None:
    existing = [_pl("A", "hop hop baykus", 4), _pl("B", "Hophop Baykus", 2)]
    assert pick_playlist(existing, "hop hop baykus")["id"] == "A"


def test_a_folded_match_is_found_when_no_exact_one_exists() -> None:
    existing = [_pl("A", "hop hop baykus", 4)]
    assert pick_playlist(existing, "hophop baykus")["id"] == "A"


def test_the_fullest_wins_when_several_fold_together() -> None:
    """Duplicates already exist on the channel; adding to the fullest keeps the
    split from getting worse."""
    existing = [_pl("A", "hop hop baykus", 4), _pl("B", "HopHop Baykus", 2)]
    assert pick_playlist(existing, "Hop Hop Baykuş")["id"] == "A"


def test_nothing_matching_returns_none_so_the_caller_creates_one() -> None:
    assert pick_playlist([_pl("A", "Riko", 3)], "Niloya") is None
