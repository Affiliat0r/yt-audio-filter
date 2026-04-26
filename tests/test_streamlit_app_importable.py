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


def test_streamlit_studio_helpers_present() -> None:
    """The unified Studio page consolidates the previous four tabs into
    a single page (gallery → mode selectbox → mode-specific panel). If
    any of those helpers get renamed without updating ``main()``'s
    dispatch, this test catches it before the headless smoke check has
    to. The Phase 2 tab functions (``_render_tab_*``) and the
    Weekly-lesson-plan tab (``_render_tab_lesson``) were intentionally
    removed, so this list reflects the current API.
    """
    pytest.importorskip("streamlit")
    module = importlib.import_module("yt_audio_filter.streamlit_app")
    for name in (
        "_render_studio",
        "_studio_panel_surah",
        "_studio_panel_ayah",
        "_studio_panel_music",
        "_render_music_removal_and_display",
        "_classify_music_removal_stage",
        "_scrub_streamlit_handlers",
        "_prune_stale_channel_filters",
        "_render_ayah_ranges_and_display",
    ):
        assert hasattr(module, name), f"streamlit_app missing helper: {name}"
        assert callable(getattr(module, name)), f"{name} must be callable"

    # Removed-on-purpose helpers — keep them gone so the import stays
    # tight and ``main()`` doesn't accidentally regrow tabs.
    for removed in ("_render_tab_lesson", "_render_tab_simple", "_render_tab_ayah"):
        assert not hasattr(module, removed), (
            f"streamlit_app should NOT expose removed helper {removed!r}"
        )
