"""A long surah list must collapse to a range in *both* label builders.

`_build_surah_numbers_auto_vars` learned this the hard way: spelling out the
ten closing surahs came to 148 characters and YouTube refused the upload after
the render had already finished. It now spells out up to
`_MAX_SPELLED_OUT_SURAHS` and otherwise says "First → Last (N surahs)".

`_build_surah_auto_vars` — the sibling used when surahs arrive as pasted URLs
rather than numbers — never got the same treatment and always joined with
" + ". Thirteen surahs there produce a ~190-character title, which `fit_title`
then truncates mid-name.

Both paths render the same channel's titles, so the rule lives in one place and
both are tested against it.
"""

from __future__ import annotations

import pytest

from yt_audio_filter.overlay_pipeline import (
    _MAX_SPELLED_OUT_SURAHS,
    collapse_surah_label,
)


def test_a_short_list_is_spelled_out() -> None:
    label = collapse_surah_label(["Al-Fatiha", "Al-Ikhlas"], [1, 112])
    assert label == "Al-Fatiha + Al-Ikhlas"


def test_the_threshold_is_inclusive() -> None:
    names = [f"S{i}" for i in range(_MAX_SPELLED_OUT_SURAHS)]
    numbers = list(range(1, _MAX_SPELLED_OUT_SURAHS + 1))
    assert collapse_surah_label(names, numbers) == " + ".join(names)


def test_a_consecutive_run_becomes_a_range() -> None:
    """The case that prompted this: At-Takathur through An-Nas."""
    names = [
        "At-Takathur", "Al-Asr", "Al-Humazah", "Al-Fil", "Quraysh", "Al-Maun",
        "Al-Kawthar", "Al-Kafirun", "An-Nasr", "Al-Masad", "Al-Ikhlas",
        "Al-Falaq", "An-Nas",
    ]
    label = collapse_surah_label(names, list(range(102, 115)))
    assert label == "At-Takathur → An-Nas (13 surahs)"


def test_a_scattered_list_names_the_first_and_counts_the_rest() -> None:
    names = ["Al-Fatiha", "Ya-Sin", "Al-Mulk", "Al-Kahf", "An-Nas"]
    label = collapse_surah_label(names, [1, 36, 67, 18, 114])
    assert label == "Al-Fatiha + 4 more"


def test_a_collapsed_label_fits_a_youtube_title() -> None:
    """The whole point — the upload used to be refused at 148 characters."""
    names = [f"Surah-Number-{i}" for i in range(102, 115)]
    assert len(collapse_surah_label(names, list(range(102, 115)))) < 100


def test_missing_numbers_fall_back_to_spelling_out() -> None:
    """A pasted URL whose surah could not be detected has no number.

    Guessing a range from a partial list would put a stretch of the Qur'an in
    the title that the video does not contain.
    """
    names = ["Some Video Title", "Al-Asr", "Al-Humazah"]
    label = collapse_surah_label(names, [None, 103, 104])
    assert label == " + ".join(names)


def test_one_surah_is_just_its_name() -> None:
    assert collapse_surah_label(["An-Nas"], [114]) == "An-Nas"


# ---------------------------------------------------- the URL path uses it


def test_the_url_builder_collapses_too() -> None:
    """`_build_surah_auto_vars` is what a `--surah <url>` render goes through."""
    from types import SimpleNamespace
    from unittest import mock

    from yt_audio_filter import overlay_pipeline

    names = [
        "At-Takathur", "Al-Asr", "Al-Humazah", "Al-Fil", "Quraysh", "Al-Maun",
        "Al-Kawthar", "Al-Kafirun", "An-Nasr", "Al-Masad", "Al-Ikhlas",
        "Al-Falaq", "An-Nas",
    ]
    resolved = [SimpleNamespace(title=f"Surah {n} - Salim Bahanan") for n in names]
    numbers = list(range(102, 115))
    matches = [
        SimpleNamespace(name=n, tag=n.replace("-", ""), number=num)
        for n, num in zip(names, numbers)
    ]
    meta = SimpleNamespace(
        title="Surah At Takasur - Salim Bahanan", description="", channel="c", uploader="u"
    )

    with mock.patch.object(overlay_pipeline, "detect_surah", side_effect=matches, create=True):
        auto = overlay_pipeline._build_surah_auto_vars(resolved, resolved[0], meta)

    assert auto["detected_surah"] == "At-Takathur → An-Nas (13 surahs)"
