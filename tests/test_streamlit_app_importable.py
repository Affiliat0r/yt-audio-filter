"""Smoke test for the Streamlit UI module.

Streamlit has no headless test harness that plays nicely with pytest, so
full UI tests are out of scope. This test just confirms the module is
importable (so basic Python errors like NameError / syntax trip CI) and
that ``main`` is callable.

If Streamlit is not installed (the ``[app]`` extra wasn't selected), we
skip rather than fail — the CLI-only install path should still pass
tests.
"""

from __future__ import annotations

import importlib
import pytest


def test_streamlit_app_importable() -> None:
    streamlit = pytest.importorskip("streamlit")
    assert streamlit is not None  # quiet the unused-import warning

    module = importlib.import_module("yt_audio_filter.streamlit_app")
    assert hasattr(module, "main"), "streamlit_app must expose a main() entry point"
    assert callable(module.main), "streamlit_app.main must be callable"
