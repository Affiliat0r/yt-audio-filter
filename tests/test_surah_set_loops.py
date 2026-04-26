"""Unit tests for ``_expand_surah_selection`` — the pure helper behind
the Streamlit ``_surah_picker`` that turns (selected_surahs,
per_surah_repeats, set_loops) into the final concat-order list."""

from __future__ import annotations

import importlib

import pytest


def _load_helper():
    pytest.importorskip("streamlit")
    module = importlib.import_module("yt_audio_filter.streamlit_app")
    assert hasattr(module, "_expand_surah_selection"), (
        "streamlit_app must expose _expand_surah_selection"
    )
    return module._expand_surah_selection


def test_no_repeats_no_loops_returns_selection_as_is() -> None:
    expand = _load_helper()
    assert expand([1, 112, 114], [1, 1, 1], 1) == [1, 112, 114]


def test_per_surah_repeats_concat_in_order() -> None:
    expand = _load_helper()
    # Fatiha 10x → Ikhlas 3x → Nas 5x, no set-loop.
    assert expand([1, 112, 114], [10, 3, 5], 1) == (
        [1] * 10 + [112] * 3 + [114] * 5
    )


def test_set_loops_alternates_full_selection() -> None:
    expand = _load_helper()
    # Set-loop 3 with all per-surah repeats=1 → [F, I, N] x 3 alternating.
    assert expand([1, 112, 114], [1, 1, 1], 3) == [
        1, 112, 114,
        1, 112, 114,
        1, 112, 114,
    ]


def test_set_loops_combines_with_per_surah_repeats() -> None:
    expand = _load_helper()
    # Per-surah block (F, F, I, N) repeated 2x via set-loop.
    assert expand([1, 112, 114], [2, 1, 1], 2) == [
        1, 1, 112, 114,
        1, 1, 112, 114,
    ]


def test_empty_selection_returns_empty() -> None:
    expand = _load_helper()
    assert expand([], [], 5) == []


def test_zero_or_negative_set_loops_treated_as_one() -> None:
    expand = _load_helper()
    # UI clamps via min_value=1, but the helper should be defensive
    # so a stale session-state value can't return an empty list.
    assert expand([1], [1], 0) == [1]
    assert expand([1], [1], -3) == [1]


def test_repeats_length_must_match_selection_length() -> None:
    expand = _load_helper()
    with pytest.raises(ValueError):
        expand([1, 2, 3], [1, 1], 1)
