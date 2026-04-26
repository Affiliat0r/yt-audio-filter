"""Unit tests for the music-removal tab helpers in ``streamlit_app``.

The Streamlit tab itself is not unit-testable, but the pure helpers that
classify ``pipeline.process_video`` progress events into the UI's three
progress bars are. ``process_video`` emits stage names like
``"Extract Audio"``, ``"Isolate Vocals"``, ``"Remux Video"``, and a few
chunked-mode variants — the classifier maps each to one of three
buckets so the right bar advances.
"""

from __future__ import annotations

import importlib

import pytest


def _classifier():
    pytest.importorskip("streamlit")
    module = importlib.import_module("yt_audio_filter.streamlit_app")
    assert hasattr(module, "_classify_music_removal_stage"), (
        "streamlit_app must expose _classify_music_removal_stage"
    )
    return module._classify_music_removal_stage


def test_extract_audio_routes_to_extract_bucket() -> None:
    classify = _classifier()
    assert classify("Extract Audio") == "extract"
    assert classify("extract audio") == "extract"  # case-insensitive


def test_isolate_vocals_routes_to_demucs_bucket() -> None:
    classify = _classifier()
    assert classify("Isolate Vocals") == "demucs"
    assert classify("isolating vocals") == "demucs"


def test_remux_routes_to_remux_bucket() -> None:
    classify = _classifier()
    assert classify("Remux Video") == "remux"


def test_chunked_split_routes_to_extract_bucket() -> None:
    """``Split Video`` is the first thing chunked mode does — visually
    it belongs in the same bucket as Extract Audio (both are pre-Demucs
    setup work)."""
    classify = _classifier()
    assert classify("Split Video") == "extract"


def test_process_chunks_routes_to_demucs_bucket() -> None:
    """The expensive chunked Demucs runs report under ``Process Chunks``."""
    classify = _classifier()
    assert classify("Process Chunks") == "demucs"


def test_concatenate_chunks_routes_to_remux_bucket() -> None:
    classify = _classifier()
    assert classify("Concatenate Chunks") == "remux"


def test_unknown_stage_returns_other() -> None:
    """Future stages we don't recognise should map to ``other`` so the
    callback can decide to ignore or log them — but never crash."""
    classify = _classifier()
    assert classify("Some Future Stage") == "other"
    assert classify("") == "other"
