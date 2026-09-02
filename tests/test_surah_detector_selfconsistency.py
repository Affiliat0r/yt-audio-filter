"""Every surah must be detectable by the name we call it.

The table in ``surah_detector`` pairs a canonical name with the patterns that
recognise it. Nothing forced the two to agree, and they drifted: At-Takathur's
only pattern was ``at\\W*takaathur`` — a double *a* — so ``detect_surah`` failed
on "At-Takathur", the very string the table calls it.

That failure is silent and expensive. The overlay pipeline labels a render from
whatever ``detect_surah`` returns, and falls back to the source video's *whole
YouTube title* when it returns nothing. A real upload went out reading:

    Surah At Takasur - Salim Bahanan + Al-Asr + Al-Humazah + Al-Fil — ...

which is a broken public title, discovered only by reading it afterwards.

So this file asserts the property the table always implied: a surah's own name
round-trips through its own patterns.
"""

from __future__ import annotations

import pytest

from yt_audio_filter.surah_detector import _SURAHS, detect_surah

#: Every (name, number) the table defines. Entries with no number are aliases
#: or groupings and carry no identity to round-trip.
NAMED = [(name, number) for name, number, _patterns in _SURAHS if number is not None]


def test_the_table_is_not_empty() -> None:
    assert len(NAMED) >= 114


@pytest.mark.parametrize("name,number", NAMED, ids=[n for n, _ in NAMED])
def test_a_surah_is_found_by_its_own_canonical_name(name: str, number: int) -> None:
    """The property that was quietly false for At-Takathur."""
    found = detect_surah(name)
    assert found is not None, f"{name!r} does not match its own pattern"
    assert found.number == number


@pytest.mark.parametrize("name,number", NAMED, ids=[n for n, _ in NAMED])
def test_a_surah_is_found_in_the_way_titles_actually_write_it(name: str, number: int) -> None:
    """Sources write "Surah <name>", and hyphens come and go."""
    for written in (f"Surah {name}", name.replace("-", " "), f"Surah {name.replace('-', ' ')}"):
        found = detect_surah(written)
        assert found is not None, f"{written!r} was not recognised"
        assert found.number == number


# ------------------------------------------------- the spelling that broke it


@pytest.mark.parametrize(
    "written",
    [
        "At-Takathur",
        "At Takathur",
        "AtTakathur",
        "Surah At Takasur - Salim Bahanan",
        "Surah At-Takaathur",
        "at takaasur",
    ],
)
def test_takathur_survives_the_th_versus_s_transliteration(written: str) -> None:
    """Arabic ث is romanised as both *th* and *s*, and the sources use both."""
    found = detect_surah(written)
    assert found is not None and found.number == 102
