"""A rendered title must fit YouTube's 100-character limit on its own.

Reported from the field: ten consecutive surahs (105-114, the closing run of
the Qur'an) produced

    'Al-Fil + Quraysh + Al-Maun + Al-Kawthar + Al-Kafirun + An-Nasr +
     Al-Masad + Al-Ikhlas + Al-Falaq + An-Nas - Saad Al-Ghamdi | ...'

at 148 characters. The upload guard caught it, so nothing broken was
published — but only after the render and the upload attempt had already
happened, and the user was left with a finished video and no way to publish
it.

``_compact_consecutive_duplicates`` compacts *repeats of one surah*
(``[1,1,1]`` -> ``"Al-Fatiha (x3)"``). It does nothing for a *run of
consecutive surah numbers*, where every entry is different.
"""

from __future__ import annotations

import pytest

from yt_audio_filter.overlay_pipeline import (
    _build_surah_numbers_auto_vars,
    fit_title,
)

RECITER = "Saad Al-Ghamdi"
CHANNEL = "Muziksiz Cizgi Filimler"
TEMPLATE = "{surah} - " + RECITER + " | " + CHANNEL


def _title_for(numbers: list[int]) -> str:
    auto = _build_surah_numbers_auto_vars(numbers, RECITER, "some cartoon")
    return TEMPLATE.format(surah=auto["detected_surah"])


def test_the_reported_failure_now_fits() -> None:
    """The exact case from the field: surahs 105-114."""
    title = _title_for(list(range(105, 115)))
    assert len(title) <= 100, f"{len(title)} chars: {title!r}"
    # And it still says something useful about *which* surahs.
    assert "Al-Fil" in title
    assert "An-Nas" in title


def test_a_consecutive_run_reads_as_a_range() -> None:
    auto = _build_surah_numbers_auto_vars(list(range(105, 115)), RECITER, "")
    assert auto["detected_surah"] == "Al-Fil → An-Nas (10 surahs)"


def test_short_runs_are_left_alone() -> None:
    """Two or three surahs are perfectly readable spelled out; compacting them
    would lose information for no benefit."""
    auto = _build_surah_numbers_auto_vars([113, 114], RECITER, "")
    assert auto["detected_surah"] == "Al-Falaq + An-Nas"


def test_a_single_surah_is_unchanged() -> None:
    auto = _build_surah_numbers_auto_vars([114], RECITER, "")
    assert auto["detected_surah"] == "An-Nas"


def test_repeats_still_compact_as_before() -> None:
    """The existing duplicate compactor must keep working."""
    auto = _build_surah_numbers_auto_vars([1, 1, 1, 114], RECITER, "")
    assert auto["detected_surah"] == "Al-Fatiha (×3) + An-Nas"


def test_a_long_non_consecutive_set_falls_back_to_a_count() -> None:
    """No range to describe, so name the first and count the rest."""
    numbers = [1, 36, 55, 67, 78, 92, 103, 110, 112, 114]
    auto = _build_surah_numbers_auto_vars(numbers, RECITER, "")
    detected = auto["detected_surah"]
    assert detected == "Al-Fatiha + 9 more"
    assert len(TEMPLATE.format(surah=detected)) <= 100


def test_set_loops_still_work() -> None:
    """Set-loop formatting predates this and must survive it."""
    auto = _build_surah_numbers_auto_vars([1, 112, 114] * 10, RECITER, "")
    assert "set ×10" in auto["detected_surah"]


# --------------------------------------------------------------- fit_title


def test_fit_title_leaves_a_short_title_untouched() -> None:
    assert fit_title("An-Nas - Ghamdi") == "An-Nas - Ghamdi"


def test_fit_title_trims_on_a_word_boundary() -> None:
    """Last-resort safety net for a template we do not control: never emit a
    title that would be rejected, and never cut mid-word."""
    long_title = "word " * 40
    fitted = fit_title(long_title)
    assert len(fitted) <= 100
    assert not fitted.endswith("wor")
    assert fitted.endswith("…")


def test_fit_title_handles_a_single_unbroken_run() -> None:
    """No spaces to break on — it must still come back within the limit."""
    fitted = fit_title("x" * 200)
    assert len(fitted) <= 100


@pytest.mark.parametrize("count", [5, 10, 20, 50, 114])
def test_no_surah_count_can_overflow_the_limit(count: int) -> None:
    """Whatever the user selects, the title fits."""
    numbers = list(range(1, count + 1))
    assert len(_title_for(numbers)) <= 100
