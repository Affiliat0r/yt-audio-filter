"""Streamlit UI for yt-quran-overlay.

Three-tab layout (Phase 2):

* **Surah render** — the original surah-multiselect + reciter +
  thumbnail-gallery + Render flow. Unchanged behaviour, nested under a
  tab.
* **Ayah range (memorization)** — wishlist M2/M3: pick (surah, start..end,
  repeats, gap_seconds) blocks and render via
  ``overlay_pipeline.run_overlay_from_ayah_ranges``. Reciter list is
  filtered to those with EveryAyah coverage.
* **Weekly lesson plan** — wishlist C1: load a JSON lesson plan and run
  every lesson back-to-back via ``lesson_planner.render_plan``.

Sidebar (visible across all tabs): output preset, trilingual-subtitles
toggle, optional YouTube playlist id, plus the existing metadata-path /
upscale toggle.

The module is designed to be executed via ``streamlit run``, but
``main()`` is importable so a smoke test can verify the module loads.
Top-level code runs on every Streamlit rerun; keep it cheap and
idempotent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# Streamlit imports are kept at module level so ``streamlit run`` picks
# them up on rerun. The smoke test tolerates an ImportError because the
# `[app]` extra is optional; see tests/test_streamlit_app_importable.py.
try:  # pragma: no cover - exercised by the smoke test
    import streamlit as st
except ImportError:  # pragma: no cover
    st = None  # type: ignore[assignment]

# Absolute imports: `streamlit run streamlit_app.py` executes this file as a
# script, not as part of the yt_audio_filter package, so relative imports
# (from .foo import bar) fail with "no known parent package". The same
# imports under absolute form work in both run-as-script and
# import-as-module contexts.
from yt_audio_filter import ayah_data, render_presets
from yt_audio_filter.cartoon_catalog import (
    CatalogVideo,
    DEFAULT_CACHE_DIR,
    DEFAULT_CHANNELS_PATH,
    CATALOG_CACHE_FILENAME,
    CartoonChannel,
    ensure_thumbnail,
    list_videos,
    load_channels,
)
from yt_audio_filter.cartoon_search import (
    SEARCH_CHANNEL_SLUG,
    add_pick_to_catalog,
    search_videos,
)
from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.metadata import OverlayMetadata, load_metadata
from yt_audio_filter.quran_audio_source import Reciter, is_surah_cached, list_reciters
from yt_audio_filter.surah_detector import _SURAHS


# ---------------------------------------------------------------------------
# Constants & small data holders
# ---------------------------------------------------------------------------

DEFAULT_METADATA_PATH = "examples/metadata-surah-arrahman.json"
DEFAULT_LESSON_PLAN_PATH = "examples/lesson-plan-week.json"
LOG_BUFFER_MAX_LINES = 40
PAGE_SIZE = 24
DEFAULT_OUTPUT_DIR = Path("output")


@dataclass(frozen=True)
class SurahEntry:
    """``(number, display_name)`` pair for the surah multiselect."""

    number: int
    name: str

    @property
    def label(self) -> str:
        return f"{self.number}. {self.name}"


def _surah_entries() -> List[SurahEntry]:
    """Build the 114-entry surah list from ``surah_detector._SURAHS``.

    We drop the named-passage rows (``number is None``) and sort by the
    canonical surah number.
    """
    out: List[SurahEntry] = []
    for name, number, _patterns in _SURAHS:
        if number is None:
            continue
        out.append(SurahEntry(number=number, name=name))
    out.sort(key=lambda e: e.number)
    return out


# ---------------------------------------------------------------------------
# Logging capture for the st.status block
# ---------------------------------------------------------------------------


class _StreamlitLogBuffer(logging.Handler):
    """Logging handler that keeps the last N formatted records in memory.

    Streamlit doesn't capture Python's ``logging`` by default. We attach
    this handler to the project logger for the duration of a render, then
    drain ``lines`` into an ``st.code`` block between records.
    """

    def __init__(self, max_lines: int = LOG_BUFFER_MAX_LINES) -> None:
        super().__init__()
        self.max_lines = max_lines
        self.lines: List[str] = []
        self.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # pragma: no cover - formatter failure is benign
            msg = record.getMessage()
        self.lines.append(msg)
        if len(self.lines) > self.max_lines:
            # Keep the tail; bound memory.
            self.lines[:] = self.lines[-self.max_lines :]

    def snapshot(self) -> str:
        return "\n".join(self.lines)


def _scrub_streamlit_handlers(project_logger: logging.Logger) -> None:
    """Remove any leftover ``_StreamlitLogBuffer`` / ``_FlushingHandler`` handlers.

    Streamlit reruns can leave handlers attached if a previous render
    crashed mid-flight or the user navigated away — see review fix
    "Logging-handler leak across reruns". We scrub by class name (not
    ``isinstance``) so the inner ``_FlushingHandler`` defined inside
    ``_render_and_display`` is recognised across reruns despite living
    in different closures each time.
    """
    target_names = ("_StreamlitLogBuffer", "_FlushingHandler")
    leftover = [
        h
        for h in list(project_logger.handlers)
        if type(h).__name__ in target_names
    ]
    for h in leftover:
        project_logger.removeHandler(h)


# ---------------------------------------------------------------------------
# Cached helpers — keep rerun cost low
# ---------------------------------------------------------------------------


def _cache_data(func: Callable):
    """Decorator that calls ``st.cache_data`` when Streamlit is importable.

    Falls back to the raw function outside of ``streamlit run`` so the
    smoke test can import the module without a Streamlit runtime.
    """
    if st is None:  # pragma: no cover
        return func
    return st.cache_data(show_spinner=False)(func)


@_cache_data
def _load_channels_cached(path_str: str) -> List[CartoonChannel]:
    return load_channels(Path(path_str))


@_cache_data
def _list_videos_cached(
    channels_path_str: str,
    cache_dir_str: str,
    cache_bust: int,
) -> List[CatalogVideo]:
    """List cartoon videos, keyed by (channels_path, cache_dir, cache_bust).

    ``cache_bust`` is driven by a session-state counter so the "Refresh
    catalog" toggle can invalidate the Streamlit cache without touching
    the on-disk JSON cache (which has its own 24 h TTL).
    """
    channels = _load_channels_cached(channels_path_str)
    return list_videos(channels=channels, cache_dir=Path(cache_dir_str))


def _search_videos_cached(
    query: str, max_results: int, cache_dir_str: str
) -> List[CatalogVideo]:
    """Thin wrapper around ``cartoon_search.search_videos``.

    The on-disk per-query JSON cache (1h TTL) is the source of truth; we
    don't add a Streamlit-layer cache because the disk read is cheap and
    using ``st.cache_data`` would freeze stale results until the session
    ends.
    """
    return search_videos(query, max_results, cache_dir=Path(cache_dir_str))


@_cache_data
def _ensure_thumbnail_cached(
    video_id: str, thumbnail_url: str, cache_dir_str: str
) -> Optional[str]:
    """Return the local thumbnail path as a string, or ``None`` on failure."""
    stub = CatalogVideo(
        video_id=video_id,
        url="",
        title="",
        duration=0,
        view_count=0,
        upload_date="",
        thumbnail_url=thumbnail_url,
        channel_slug="",
    )
    try:
        return str(ensure_thumbnail(stub, cache_dir=Path(cache_dir_str)))
    except Exception:
        # Don't take the whole page down for one broken thumbnail.
        return None


@_cache_data
def _load_reciters_cached() -> List[Reciter]:
    return list_reciters()


@_cache_data
def _cached_surah_status(
    reciter_slug: str, cache_dir_str: str, cache_bust: int
) -> List[bool]:
    """Return ``[is_cached_for_surah_1, ..., is_cached_for_surah_114]``."""
    cache_dir = Path(cache_dir_str)
    return [is_surah_cached(n, reciter_slug, cache_dir) for n in range(1, 115)]


@_cache_data
def _load_metadata_cached(
    path_str: str, mtime_ns: int
) -> Tuple[bool, str, Optional[OverlayMetadata]]:
    """Return ``(ok, message, metadata_or_none)`` for the sidebar badge.

    On failure, ``message`` is rendered by ``_format_metadata_error`` so
    ``OverlayError.details`` is surfaced separately from ``message`` —
    plain ``repr(exc)`` for any unexpected exception type.
    """
    del mtime_ns  # only used for cache invalidation
    try:
        meta = load_metadata(Path(path_str))
    except OverlayError as exc:
        msg = exc.message
        if exc.details:
            msg = f"{msg}\n\n{exc.details}"
        return False, msg, None
    except Exception as exc:  # noqa: BLE001 - surface anything else as repr
        return False, repr(exc), None
    return True, f"Loaded: {meta.title}", meta


def _metadata_mtime_ns(path_str: str) -> int:
    """Return the file's mtime_ns or 0 when missing (so the cache key is stable)."""
    try:
        return Path(path_str).stat().st_mtime_ns
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Small UI helpers
# ---------------------------------------------------------------------------


def _format_duration(seconds: int) -> str:
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "?"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def _channel_display_name(channels: List[CartoonChannel], slug: str) -> str:
    if slug == "__search__":
        return "YouTube search"
    for c in channels:
        if c.slug == slug:
            return c.display_name
    return slug


def _init_session_state() -> None:
    """Create our session-state keys once per session."""
    assert st is not None
    ss = st.session_state
    ss.setdefault("rendered_path", None)
    ss.setdefault("rendered_title_vars", {})
    ss.setdefault("rendered_kind", None)  # "surah" | "ayah" | None
    ss.setdefault("selected_visual_id", None)
    ss.setdefault("catalog_cache_bust", 0)
    ss.setdefault("audio_cache_bust", 0)
    ss.setdefault("ayah_ranges", [])  # List[dict]
    ss.setdefault("lesson_plan_path", DEFAULT_LESSON_PLAN_PATH)
    ss.setdefault("lesson_plan_validated", False)
    ss.setdefault("lesson_plan_error", "")
    ss.setdefault("lesson_results", [])  # List[Tuple[day, path]]
    ss.setdefault("lesson_errors", [])  # List[Tuple[day, message]]


def _sidebar(
    surahs: List[SurahEntry],
) -> Tuple[Optional[OverlayMetadata], str, bool, str, bool, Optional[str]]:
    """Render the sidebar.

    Returns:
        (metadata_or_none, metadata_path, upscale, preset_slug,
        burn_subtitles, playlist_id_or_none).
    """
    assert st is not None
    st.sidebar.title("yt-quran-overlay")

    # ------------------------------------------------------------------
    # New cross-tab controls (above the legacy metadata input).
    # ------------------------------------------------------------------
    presets = render_presets.list_presets()
    preset_names = [p.display_name for p in presets]
    # Sticky default that flips when the user toggles --upscale below.
    # We stash the previously-chosen slug so picking explicitly survives.
    prev_preset_slug = st.session_state.get("preset_slug")
    if prev_preset_slug is None:
        # First boot: pick a sensible default (we don't know upscale yet
        # at this point in the render order — but the legacy code reads
        # the toggle below, so we default conservatively to landscape).
        default_slug = "youtube_landscape"
    else:
        default_slug = prev_preset_slug
    default_idx = next(
        (i for i, p in enumerate(presets) if p.slug == default_slug),
        0,
    )
    chosen_preset_name = st.sidebar.selectbox(
        "Output preset",
        options=preset_names,
        index=default_idx,
        help=(
            "Resolution + scale-mode for the rendered MP4. "
            "Vertical = WhatsApp/status; square = Instagram feed."
        ),
        key="preset_select",
    )
    chosen_preset = next(
        p for p in presets if p.display_name == chosen_preset_name
    )
    st.session_state["preset_slug"] = chosen_preset.slug

    burn_subtitles = st.sidebar.toggle(
        "Burn trilingual subtitles",
        value=False,
        help=(
            "Hard-burn ASS subtitles into the output. "
            "Used by the ayah-range tab."
        ),
        key="burn_subtitles_toggle",
    )
    st.sidebar.caption(
        "Arabic + English; drop `data/translations/dutch.json` to enable a "
        "third line."
    )

    playlist_id = st.sidebar.text_input(
        "YouTube playlist id (optional)",
        value=st.session_state.get("playlist_id_input", ""),
        help=(
            "When set, uploaded videos are appended to this playlist "
            "(unlisted by default)."
        ),
        key="playlist_id_input",
    ).strip()

    st.sidebar.divider()

    # ------------------------------------------------------------------
    # Legacy controls (unchanged behaviour).
    # ------------------------------------------------------------------
    metadata_path = st.sidebar.text_input(
        "Metadata JSON path",
        value=DEFAULT_METADATA_PATH,
        help="Path to the publish-metadata JSON. Used for the upload title/description template.",
    )
    ok, msg, meta = _load_metadata_cached(
        metadata_path, _metadata_mtime_ns(metadata_path)
    )
    if ok:
        st.sidebar.success(f"Metadata OK: {msg}")
    else:
        st.sidebar.error(f"Metadata error: {msg}")

    upscale = st.sidebar.toggle(
        "Upscale visual (Real-ESRGAN)",
        value=False,
        help="First run downloads the Real-ESRGAN model weights (~65 MB) and is slow.",
    )
    st.sidebar.caption(
        "First run of upscale downloads Real-ESRGAN weights (~65 MB)."
    )

    # If the user just flipped --upscale ON and is still on the default
    # 1080p preset, hint at the 720p preset for upscale workflows. We
    # don't auto-flip; sticky preference wins.
    if upscale and chosen_preset.slug == "youtube_landscape":
        st.sidebar.caption(
            "Tip: pair --upscale with 'YouTube (720p landscape)' to upscale "
            "*to* 720p (cheaper than upscaling to 1080p)."
        )

    with st.sidebar.expander("About"):
        try:
            channels = _load_channels_cached(str(DEFAULT_CHANNELS_PATH))
        except Exception as e:
            channels = []
            st.warning(f"channels.json error: {e}")
        try:
            reciters = _load_reciters_cached()
        except Exception as e:
            reciters = []
            st.warning(f"reciters.json error: {e}")
        try:
            videos = _list_videos_cached(
                str(DEFAULT_CHANNELS_PATH),
                str(DEFAULT_CACHE_DIR),
                st.session_state.get("catalog_cache_bust", 0),
            )
        except Exception:
            videos = []
        st.write(f"Surahs available: **{len(surahs)}**")
        st.write(f"Reciters available: **{len(reciters)}**")
        st.write(f"Cartoon channels: **{len(channels)}**")
        st.write(f"Cached visuals: **{len(videos)}**")

    return (
        meta,
        metadata_path,
        upscale,
        chosen_preset.slug,
        burn_subtitles,
        playlist_id or None,
    )


def _expand_surah_selection(
    selected_numbers: List[int],
    per_surah_repeats: List[int],
    set_loops: int,
) -> List[int]:
    """Build the final concat-order surah list.

    The user picks N surahs, sets a repeat count for each, then optionally
    loops the WHOLE set M times. Order = per-surah block repeated, then
    the whole concatenation repeated ``set_loops`` times.

    Example: selection [1, 112, 114] with repeats [2, 1, 1] and set_loops=2
    → [1, 1, 112, 114, 1, 1, 112, 114].
    """
    if len(selected_numbers) != len(per_surah_repeats):
        raise ValueError(
            f"per_surah_repeats length ({len(per_surah_repeats)}) must match "
            f"selected_numbers length ({len(selected_numbers)})"
        )
    loops = max(1, int(set_loops))
    block: List[int] = []
    for n, r in zip(selected_numbers, per_surah_repeats):
        block.extend([n] * max(1, int(r)))
    return block * loops


def _surah_picker(surahs: List[SurahEntry]) -> List[int]:
    """Return the selected surah numbers expanded by per-surah repeats."""
    assert st is not None
    st.subheader("Surahs")
    by_label = {e.label: e.number for e in surahs}
    label_by_number = {e.number: e.label for e in surahs}
    selected_labels = st.multiselect(
        "Surahs",
        options=list(by_label.keys()),
        default=[],
        key="surahs_multiselect",
        label_visibility="collapsed",
    )
    st.caption(
        "Use the arrow keys after typing to filter. Order matters — audios "
        "are concatenated in the order you pick."
    )

    selected_numbers = [by_label[label] for label in selected_labels]
    if not selected_numbers:
        return []

    st.caption("Audio plays in this order; the visual loops underneath.")
    per_surah_repeats: List[int] = []
    for idx, number in enumerate(selected_numbers):
        col_label, col_count = st.columns([3, 1])
        with col_label:
            st.write(label_by_number.get(number, str(number)))
        with col_count:
            repeats = st.number_input(
                f"Repeat × for surah {number}",
                min_value=1,
                max_value=99,
                value=1,
                step=1,
                key=f"surah_repeat_{idx}_{number}",
                label_visibility="collapsed",
            )
        per_surah_repeats.append(int(repeats))

    set_loops = 1
    if len(selected_numbers) > 1:
        set_loops = int(
            st.number_input(
                "Loop the whole set ×",
                min_value=1,
                max_value=99,
                value=1,
                step=1,
                key="surah_set_loops",
                help=(
                    "Plays the entire selection N times in order, in addition "
                    "to per-surah repeats. Example: selection [Fatiha, Ikhlas, "
                    "Nas] with per-surah repeats all 1 and set-loop 10 → "
                    "10x alternating (F, I, N, F, I, N, …)."
                ),
            )
        )

    return _expand_surah_selection(selected_numbers, per_surah_repeats, set_loops)


def _reciter_picker(
    *,
    only_everyayah: bool = False,
    key: str = "reciter_select",
) -> Optional[Reciter]:
    """Reciter selectbox + sample-audio preview.

    When ``only_everyayah=True`` we filter out reciters that don't have an
    EveryAyah folder (ayah-mode requires per-ayah MP3s). The filtered-out
    set is documented inline so the user understands why their favorite
    qari may be missing.
    """
    assert st is not None
    st.subheader("Reciter")
    try:
        reciters = _load_reciters_cached()
    except Exception as e:
        st.error(f"Failed to load reciters: {e}")
        return None

    if not reciters:
        st.error("No reciters configured.")
        return None

    if only_everyayah:
        # Drop reciters listed in ``RECITERS_WITHOUT_EVERYAYAH`` and any
        # quranicaudio slug that doesn't appear in the EveryAyah map.
        valid_qa_slugs = {
            entry["quranicaudio_slug"]
            for entry in ayah_data.EVERYAYAH_RECITERS.values()
        }
        excluded = list(ayah_data.RECITERS_WITHOUT_EVERYAYAH)
        filtered = [
            r
            for r in reciters
            if r.slug in valid_qa_slugs and r.slug not in excluded
        ]
        if not filtered:
            st.error("No EveryAyah-compatible reciters available.")
            return None
        st.caption(
            "Some reciters from the surah-render tab are hidden here because "
            "they have no per-ayah audio on EveryAyah.com (needed for "
            f"ayah-level repetition): {', '.join(excluded)}."
        )
        reciters = filtered

    names = [r.display_name for r in reciters]
    chosen_name = st.selectbox("Reciter", options=names, index=0, key=key)
    chosen = next((r for r in reciters if r.display_name == chosen_name), None)
    if chosen is None:
        return None

    # Sample preview. We hand the remote URL directly to st.audio.
    st.audio(chosen.sample_url)
    st.caption(f"Slug: `{chosen.slug}`  —  sample: Al-Fatiha")

    if not only_everyayah:
        _cached_audio_panel(chosen)
    return chosen


def _cached_audio_panel(reciter: Reciter) -> None:
    """Collapsible panel showing which surahs are already cached on disk."""
    assert st is not None
    cache_bust = st.session_state.get("audio_cache_bust", 0)
    statuses = _cached_surah_status(reciter.slug, str(DEFAULT_CACHE_DIR), cache_bust)
    cached_count = sum(1 for s in statuses if s)

    expander_label = f"Cached audio for this reciter ({cached_count} / 114)"
    with st.expander(expander_label, expanded=False):
        st.caption(
            f"{cached_count} / 114 surahs cached for "
            f"**{reciter.display_name}**. Surahs not yet cached will "
            f"download on render."
        )

        if st.button(
            "🗑 Refresh cached-audio counts",
            key=f"audio_cache_refresh_{reciter.slug}",
            help=(
                "Re-stat the cache directory. Use this if you've manually "
                "moved/deleted files outside the app."
            ),
        ):
            st.session_state["audio_cache_bust"] = (
                st.session_state.get("audio_cache_bust", 0) + 1
            )
            st.rerun()

        surahs = _surah_entries()
        cols = st.columns(4)
        for i, entry in enumerate(surahs):
            mark = "✅" if statuses[entry.number - 1] else "·"
            with cols[i % 4]:
                st.markdown(
                    f"{mark} `{entry.number:03d}` {entry.name}",
                )


@_cache_data
def _visual_state_index(cache_dir_str: str, cache_bust: int) -> dict:
    """Scan ``cache_dir`` once and return ``{video_id: state}``."""
    import os

    cache_dir = Path(cache_dir_str)
    if not cache_dir.is_dir():
        return {}
    mapping: dict = {}
    for entry in os.scandir(cache_dir):
        if not entry.is_file():
            continue
        name = entry.name
        if name.startswith("upscaled_") and name.endswith(".mp4"):
            vid = name[len("upscaled_") : -len(".mp4")]
            mapping[vid] = "upscaled"
        elif name.startswith("video_"):
            stem, _, _ext = name.rpartition(".")
            if not stem:
                continue
            vid = stem[len("video_") :]
            mapping.setdefault(vid, "downloaded")
    return mapping


def _visual_download_state(
    video_id: str, index: Optional[dict] = None
) -> str:
    """Return ``'upscaled' | 'downloaded' | 'new'`` for ``video_id``."""
    if index is not None:
        return index.get(video_id, "new")
    if (DEFAULT_CACHE_DIR / f"upscaled_{video_id}.mp4").exists():
        return "upscaled"
    for ext in ("mp4", "m4a", "webm", "mkv"):
        if (DEFAULT_CACHE_DIR / f"video_{video_id}.{ext}").exists():
            return "downloaded"
    return "new"


_STATE_BADGE = {
    "upscaled": "🔵 Upscaled (ready, no download needed)",
    "downloaded": "🟢 Downloaded (ready)",
    "new": "⚪ Not cached",
}


def _select_visual_callback(video_id: str) -> None:
    """Click handler for the per-tile Select button. Mutual-exclusion."""
    if st is None:  # pragma: no cover
        return
    if st.session_state.get("selected_visual_id") == video_id:
        st.session_state["selected_visual_id"] = None
    else:
        st.session_state["selected_visual_id"] = video_id


def _select_search_pick_callback(video: CatalogVideo) -> None:
    """Select-button handler for keyword-search results.

    Also persists the pick into ``cartoon_catalog.json`` under the
    ``__search__`` slug so the render pipeline can resolve it at render
    time (``overlay_pipeline._resolve_visual_video`` reads the catalog).
    """
    if st is None:  # pragma: no cover
        return
    try:
        add_pick_to_catalog(video, cache_dir=DEFAULT_CACHE_DIR)
    except Exception as e:  # noqa: BLE001 - never block selection on cache write
        st.warning(f"Could not persist pick to catalog: {e}")
    # Bust the cached list so the curated tab can also see the new pick.
    st.session_state["catalog_cache_bust"] = (
        st.session_state.get("catalog_cache_bust", 0) + 1
    )
    _select_visual_callback(video.video_id)


def _prune_stale_channel_filters(channels: List[CartoonChannel]) -> None:
    """Drop ``ch_<slug>`` keys for slugs that no longer exist in channels.json.

    Review fix: "Channel filter checkbox cleanup". Without this, removing
    a channel from the JSON leaves orphaned ``True`` checkboxes in
    session_state forever.
    """
    if st is None:  # pragma: no cover
        return
    valid_slugs = {c.slug for c in channels}
    stale = [
        k
        for k in list(st.session_state.keys())
        if isinstance(k, str)
        and k.startswith("ch_")
        and k[len("ch_") :] not in valid_slugs
    ]
    for k in stale:
        del st.session_state[k]


def _render_gallery_grid(
    *,
    filtered: List[CatalogVideo],
    full_pool: List[CatalogVideo],
    channels: List[CartoonChannel],
    state_index: dict,
    key_prefix: str,
    page_state_key: str,
    on_select,
    caption: str,
) -> Optional[CatalogVideo]:
    """Render the shared 4-col thumbnail grid + pagination.

    ``on_select`` is a callable taking a ``CatalogVideo``. ``filtered`` is
    the (already-sorted) list to paginate. ``full_pool`` is the unfiltered
    list — used only for resolving the ``Selected: ...`` banner so the
    banner survives a query change that hides the chosen tile.
    """
    assert st is not None

    st.caption(caption)

    if not filtered:
        st.warning("No videos match the current filter/search.")
        return None

    selected_id = st.session_state.get("selected_visual_id")
    # Resolve against full_pool so the banner can render for an item that
    # isn't on the current page. Falls back to filtered if the pool is empty.
    pool = full_pool or filtered
    selected_video: Optional[CatalogVideo] = next(
        (v for v in pool if v.video_id == selected_id), None
    )

    page = st.session_state.get(page_state_key, 0)
    total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages - 1)
    start = page * PAGE_SIZE
    page_videos = filtered[start : start + PAGE_SIZE]

    if selected_video is not None:
        st.success(
            f"Selected: **{selected_video.title}** · "
            f"{_format_duration(selected_video.duration)} · "
            f"{_channel_display_name(channels, selected_video.channel_slug)}"
        )

    cols = st.columns(4)
    for i, v in enumerate(page_videos):
        with cols[i % 4]:
            thumb_path = _ensure_thumbnail_cached(
                v.video_id, v.thumbnail_url, str(DEFAULT_CACHE_DIR)
            )
            if thumb_path:
                st.image(thumb_path, use_container_width=True)
            else:
                st.caption("(thumbnail unavailable)")
            state = state_index.get(v.video_id, "new")
            st.caption(
                f"{_STATE_BADGE[state]}  \n"
                f"**{v.title}**  \n"
                f"{_format_duration(v.duration)} · "
                f"{_channel_display_name(channels, v.channel_slug)}"
            )
            is_active = selected_id == v.video_id
            st.button(
                "✓ Selected (click to deselect)" if is_active else "Select",
                type=("primary" if is_active else "secondary"),
                key=f"{key_prefix}_sel_btn_{v.video_id}",
                on_click=on_select,
                args=(v,),
                use_container_width=True,
            )

    if total_pages > 1:
        pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
        with pcol1:
            if st.button(
                "◀ Previous", disabled=page == 0, key=f"{key_prefix}_prev"
            ):
                st.session_state[page_state_key] = max(0, page - 1)
                st.rerun()
        with pcol2:
            st.caption(f"Page {page + 1} / {total_pages}")
        with pcol3:
            if st.button(
                "Next ▶",
                disabled=page >= total_pages - 1,
                key=f"{key_prefix}_next",
            ):
                st.session_state[page_state_key] = min(total_pages - 1, page + 1)
                st.rerun()

    return selected_video


def _select_curated_callback(video: CatalogVideo) -> None:
    """Adapter so the curated branch can use the same ``on_click`` shape
    (``callable(CatalogVideo)``) as the search branch."""
    _select_visual_callback(video.video_id)


def _gallery_channel_mode(
    channels: List[CartoonChannel], key_prefix: str
) -> Optional[CatalogVideo]:
    """Curated-channels filter pane + grid."""
    assert st is not None

    col_refresh, col_search = st.columns([1, 3])
    with col_refresh:
        if st.button(
            "Refresh catalog",
            help="Rescrape channels, then relist.",
            key=f"{key_prefix}_refresh_catalog",
        ):
            catalog_cache = DEFAULT_CACHE_DIR / CATALOG_CACHE_FILENAME
            if catalog_cache.exists():
                try:
                    catalog_cache.unlink()
                    st.info(f"Removed {catalog_cache}")
                except OSError as e:
                    st.warning(f"Could not remove {catalog_cache}: {e}")
            st.session_state["catalog_cache_bust"] = (
                st.session_state.get("catalog_cache_bust", 0) + 1
            )
            st.session_state["visual_cache_bust"] = (
                st.session_state.get("visual_cache_bust", 0) + 1
            )
    with col_search:
        search = (
            st.text_input(
                "Filter by title",
                value="",
                placeholder="e.g. train, bus, dinosaur",
                key=f"{key_prefix}_search",
                help="Narrows the curated list. For broader results, switch to YouTube search above.",
            )
            .strip()
            .lower()
        )

    try:
        videos = _list_videos_cached(
            str(DEFAULT_CHANNELS_PATH),
            str(DEFAULT_CACHE_DIR),
            st.session_state.get("catalog_cache_bust", 0),
        )
    except Exception as e:
        st.error(f"Catalog load failed: {e}")
        return None
    if not videos:
        st.warning(
            "No cartoon videos cached. Click 'Refresh catalog' or check "
            "config/channels.json."
        )
        return None

    # Review fix: stale ``selected_visual_id`` after a catalog refresh.
    persisted_id = st.session_state.get("selected_visual_id")
    if persisted_id is not None and not any(
        v.video_id == persisted_id for v in videos
    ):
        st.session_state["selected_visual_id"] = None
        for k in [
            k
            for k in list(st.session_state.keys())
            if isinstance(k, str) and k.startswith("sel_")
        ]:
            video_id = k[len("sel_") :].split("_btn_")[-1]
            if not any(v.video_id == video_id for v in videos):
                del st.session_state[k]

    # Channel filter — only over channels in config (skip the synthetic
    # ``__search__`` slug that holds picks from the YouTube-search tab).
    visible_channel_slugs = [c.slug for c in channels]
    for v in videos:
        if v.channel_slug == SEARCH_CHANNEL_SLUG:
            continue
        if v.channel_slug not in visible_channel_slugs:
            visible_channel_slugs.append(v.channel_slug)
    with st.expander(
        f"Filter by channel ({len(visible_channel_slugs)} total)", expanded=False
    ):
        active_slugs = set()
        cols = st.columns(min(len(visible_channel_slugs), 5))
        for i, slug in enumerate(visible_channel_slugs):
            with cols[i % len(cols)]:
                if st.checkbox(
                    _channel_display_name(channels, slug),
                    value=True,
                    key=f"{key_prefix}_ch_{slug}",
                ):
                    active_slugs.add(slug)

    sort_mode = st.selectbox(
        "Sort",
        options=(
            "Downloaded first",
            "Longest first",
            "Shortest first",
            "Most viewed",
            "Newest",
            "Title A-Z",
        ),
        index=0,
        key=f"{key_prefix}_sort",
    )

    filter_signature = (search, sort_mode, frozenset(active_slugs))
    last_signature = st.session_state.get("gallery_filter_signature")
    if last_signature is not None and last_signature != filter_signature:
        st.session_state["gallery_page"] = 0
    st.session_state["gallery_filter_signature"] = filter_signature

    state_index = _visual_state_index(
        str(DEFAULT_CACHE_DIR),
        st.session_state.get("visual_cache_bust", 0),
    )

    filtered = [
        v
        for v in videos
        if v.channel_slug in active_slugs
        and v.channel_slug != SEARCH_CHANNEL_SLUG
        and (not search or search in v.title.lower())
    ]

    rank_state = {"upscaled": 0, "downloaded": 1, "new": 2}
    if sort_mode == "Downloaded first":
        filtered.sort(
            key=lambda v: (rank_state[state_index.get(v.video_id, "new")], -v.view_count)
        )
    elif sort_mode == "Longest first":
        filtered.sort(key=lambda v: -v.duration)
    elif sort_mode == "Shortest first":
        filtered.sort(key=lambda v: v.duration)
    elif sort_mode == "Most viewed":
        filtered.sort(key=lambda v: -v.view_count)
    elif sort_mode == "Newest":
        filtered.sort(key=lambda v: v.upload_date or "", reverse=True)
    elif sort_mode == "Title A-Z":
        filtered.sort(key=lambda v: v.title.lower())

    n_total = sum(1 for v in videos if v.channel_slug != SEARCH_CHANNEL_SLUG)
    n_cached = sum(
        1
        for v in videos
        if v.channel_slug != SEARCH_CHANNEL_SLUG
        and state_index.get(v.video_id, "new") != "new"
    )
    caption = (
        f"Showing **{len(filtered)}** of **{n_total}** curated videos · "
        f"{n_cached} already cached (🟢/🔵) · "
        f"{n_total - n_cached} would download on render."
    )

    return _render_gallery_grid(
        filtered=filtered,
        full_pool=videos,
        channels=channels,
        state_index=state_index,
        key_prefix=key_prefix,
        page_state_key="gallery_page",
        on_select=_select_curated_callback,
        caption=caption,
    )


def _gallery_search_mode(
    channels: List[CartoonChannel], key_prefix: str
) -> Optional[CatalogVideo]:
    """Live YouTube keyword search pane + grid."""
    assert st is not None

    with st.form(key=f"{key_prefix}_search_form", clear_on_submit=False):
        col_q, col_n, col_btn = st.columns([5, 1, 1])
        with col_q:
            query_in = st.text_input(
                "Keyword search",
                value=st.session_state.get(f"{key_prefix}_search_query", ""),
                placeholder="e.g. police car, dinosaur, fire truck",
                help="Live YouTube search; results cached 1h per query.",
                label_visibility="visible",
            )
        with col_n:
            max_results = st.selectbox(
                "Results",
                options=(15, 25, 40),
                index=1,
                help="More results = slower fetch (~2-4 s for 40).",
            )
        with col_btn:
            st.write("")
            st.write("")
            submitted = st.form_submit_button("Search", use_container_width=True)

    query = (query_in or "").strip()
    if submitted:
        st.session_state[f"{key_prefix}_search_query"] = query
        st.session_state[f"{key_prefix}_search_max"] = int(max_results)
        st.session_state["gallery_search_page"] = 0

    active_query = st.session_state.get(f"{key_prefix}_search_query", "").strip()
    active_max = int(st.session_state.get(f"{key_prefix}_search_max", 25))

    if not active_query:
        st.info("Type a keyword and click **Search** to fetch live YouTube results.")
        return None

    try:
        with st.spinner(f"Searching YouTube for {active_query!r}…"):
            results = _search_videos_cached(
                active_query, active_max, str(DEFAULT_CACHE_DIR)
            )
    except OverlayError as exc:
        msg = exc.message + (f"\n\n{exc.details}" if exc.details else "")
        st.error(msg)
        return None
    except Exception as exc:  # noqa: BLE001 - keep page alive on transient errors
        st.error(f"Search failed: {exc!r}")
        return None

    if not results:
        st.warning(f"No videos found for {active_query!r}.")
        return None

    sort_mode = st.selectbox(
        "Sort",
        options=("Most viewed", "Longest first", "Shortest first", "Newest", "Title A-Z"),
        index=0,
        key=f"{key_prefix}_search_sort",
    )

    if sort_mode == "Most viewed":
        results.sort(key=lambda v: -v.view_count)
    elif sort_mode == "Longest first":
        results.sort(key=lambda v: -v.duration)
    elif sort_mode == "Shortest first":
        results.sort(key=lambda v: v.duration)
    elif sort_mode == "Newest":
        results.sort(key=lambda v: v.upload_date or "", reverse=True)
    elif sort_mode == "Title A-Z":
        results.sort(key=lambda v: v.title.lower())

    state_index = _visual_state_index(
        str(DEFAULT_CACHE_DIR),
        st.session_state.get("visual_cache_bust", 0),
    )

    caption = (
        f"Showing **{len(results)}** results for **{active_query!r}** · "
        f"sorted by {sort_mode}. Click Select to use one as the cartoon visual."
    )

    return _render_gallery_grid(
        filtered=results,
        full_pool=results,
        channels=channels,
        state_index=state_index,
        key_prefix=key_prefix,
        page_state_key="gallery_search_page",
        on_select=_select_search_pick_callback,
        caption=caption,
    )


def _cartoon_gallery(
    channels: List[CartoonChannel], *, key_prefix: str = "gallery"
) -> Optional[CatalogVideo]:
    """Thumbnail picker with two filter modes: curated channels OR live
    YouTube keyword search.

    ``key_prefix`` namespaces every widget key so the same gallery can be
    rendered in multiple tabs (e.g. simple + ayah) on the same page
    without colliding on Streamlit's auto-generated element ids.
    """
    assert st is not None
    st.subheader("Cartoon video")

    mode = st.radio(
        "Source",
        options=("Curated channels", "YouTube search"),
        index=0,
        horizontal=True,
        key=f"{key_prefix}_mode",
        help=(
            "Curated channels: pick from the configured kid-friendly channels. "
            "YouTube search: live keyword search across all of YouTube "
            "(e.g. 'police car', 'dinosaur')."
        ),
    )

    if mode == "Curated channels":
        return _gallery_channel_mode(channels, key_prefix)
    return _gallery_search_mode(channels, key_prefix)

    return selected_video


# ---------------------------------------------------------------------------
# Surah-render handler (tab_simple)
# ---------------------------------------------------------------------------


def _render_and_display(
    surah_numbers: List[int],
    reciter: Reciter,
    visual: CatalogVideo,
    metadata: OverlayMetadata,
    upscale: bool,
) -> None:
    """Invoke the surah-numbers backend, stream logs, store the result."""
    assert st is not None
    try:
        from yt_audio_filter.overlay_pipeline import run_overlay_from_surah_numbers
    except ImportError as exc:
        st.error(
            "Backend function `run_overlay_from_surah_numbers` is not "
            "available.\n\n"
            f"ImportError: {exc}"
        )
        return

    project_logger = logging.getLogger("yt_audio_filter")
    # Review fix: scrub leftover handlers from any previous (possibly
    # crashed) render cycle before we attach our pair.
    _scrub_streamlit_handlers(project_logger)

    buf = _StreamlitLogBuffer()
    previous_level = project_logger.level
    project_logger.setLevel(logging.INFO)
    project_logger.addHandler(buf)

    status = st.status("Rendering...", expanded=True)
    log_placeholder = status.empty()

    class _FlushingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_placeholder.code(buf.snapshot() or "(waiting for logs)")

    flusher = _FlushingHandler()
    project_logger.addHandler(flusher)

    result = None
    try:
        try:
            result = run_overlay_from_surah_numbers(
                surah_numbers=surah_numbers,
                reciter_slug=reciter.slug,
                visual_video_id=visual.video_id,
                metadata=metadata,
                output_path=None,
                cache_dir=DEFAULT_CACHE_DIR,
                upscale=upscale,
                upload=False,
            )
            status.update(label="Render complete", state="complete")
        except Exception as exc:  # noqa: BLE001 - surfacing to the UI
            status.update(label=f"Render failed: {exc}", state="error")
            if isinstance(exc, OverlayError):
                # User-friendly: no traceback for our own validation errors.
                st.error(exc.message)
                if exc.details:
                    st.caption(exc.details)
            else:
                st.exception(exc)
    finally:
        # Review fix: handler removal must always run, even if the
        # ``try`` above raised before assigning ``flusher``.
        project_logger.removeHandler(buf)
        project_logger.removeHandler(flusher)
        project_logger.setLevel(previous_level)

    if result is None:
        return

    output_path = Path(getattr(result, "output_path", ""))
    if not output_path or not output_path.exists():
        st.error(f"Render returned but output file is missing: {output_path!r}")
        return

    st.session_state["rendered_path"] = output_path
    st.session_state["rendered_kind"] = "surah"
    st.session_state["rendered_title_vars"] = {
        "surah_numbers": list(surah_numbers),
        "reciter_slug": reciter.slug,
        "visual_video_id": visual.video_id,
        "visual_title": visual.title,
    }


# ---------------------------------------------------------------------------
# Ayah-range render handler (tab_ayah)
# ---------------------------------------------------------------------------


def _render_ayah_ranges_and_display(
    ranges: List[object],  # List[AyahRange] but lazy-imported
    reciter_slug: str,
    visual: CatalogVideo,
    metadata: OverlayMetadata,
    *,
    upscale: bool,
    preset_slug: str,
    burn_subtitles: bool,
    playlist_id: Optional[str],
) -> None:
    """Invoke the ayah-ranges backend, stream logs, store the result."""
    assert st is not None
    try:
        from yt_audio_filter.overlay_pipeline import run_overlay_from_ayah_ranges
    except ImportError as exc:
        st.error(
            "Backend function `run_overlay_from_ayah_ranges` is not "
            f"available.\n\nImportError: {exc}"
        )
        return

    project_logger = logging.getLogger("yt_audio_filter")
    _scrub_streamlit_handlers(project_logger)

    buf = _StreamlitLogBuffer()
    previous_level = project_logger.level
    project_logger.setLevel(logging.INFO)
    project_logger.addHandler(buf)

    status = st.status("Rendering ayah-range video...", expanded=True)
    log_placeholder = status.empty()

    class _FlushingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_placeholder.code(buf.snapshot() or "(waiting for logs)")

    flusher = _FlushingHandler()
    project_logger.addHandler(flusher)

    result = None
    try:
        try:
            result = run_overlay_from_ayah_ranges(
                ranges=ranges,
                reciter_slug=reciter_slug,
                visual_video_id=visual.video_id,
                metadata=metadata,
                output_path=None,
                cache_dir=DEFAULT_CACHE_DIR,
                upscale=upscale,
                upload=False,
                playlist_id=playlist_id,
                preset_slug=preset_slug,
                burn_subtitles=burn_subtitles,
            )
            status.update(label="Render complete", state="complete")
        except Exception as exc:  # noqa: BLE001
            status.update(label=f"Render failed: {exc}", state="error")
            if isinstance(exc, OverlayError):
                st.error(exc.message)
                if exc.details:
                    st.caption(exc.details)
            else:
                st.exception(exc)
    finally:
        project_logger.removeHandler(buf)
        project_logger.removeHandler(flusher)
        project_logger.setLevel(previous_level)

    if result is None:
        return

    output_path = Path(getattr(result, "output_path", ""))
    if not output_path or not output_path.exists():
        st.error(f"Render returned but output file is missing: {output_path!r}")
        return

    # Build a synthetic surah_numbers list for the upload metadata path —
    # ``upload_rendered`` builds title vars from a list of surah numbers.
    surah_numbers: List[int] = []
    for rng in ranges:
        # rng is an AyahRange dataclass; access by attribute.
        surah_numbers.extend([rng.surah] * rng.repeats)  # type: ignore[attr-defined]

    st.session_state["rendered_path"] = output_path
    st.session_state["rendered_kind"] = "ayah"
    st.session_state["rendered_title_vars"] = {
        "surah_numbers": surah_numbers,
        "reciter_slug": reciter_slug,
        "visual_video_id": visual.video_id,
        "visual_title": visual.title,
    }


# ---------------------------------------------------------------------------
# Preview / download / upload (shared between tab_simple and tab_ayah)
# ---------------------------------------------------------------------------


def _preview_and_download(metadata: OverlayMetadata, playlist_id: Optional[str]) -> None:
    """Show the rendered video inline plus a download + upload button."""
    assert st is not None
    path: Optional[Path] = st.session_state.get("rendered_path")
    if path is None:
        return
    st.success(f"Rendered: {path}")
    try:
        st.video(str(path))
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Inline preview failed ({exc}); use the download button.")

    try:
        data = path.read_bytes()
    except OSError as exc:
        st.error(f"Cannot read rendered file: {exc}")
        return
    st.download_button(
        label="Download MP4",
        data=data,
        file_name=path.name,
        mime="video/mp4",
    )

    if st.button("Upload to YouTube", type="secondary"):
        _upload_and_show(metadata, playlist_id=playlist_id)


def _upload_and_show(
    metadata: OverlayMetadata, *, playlist_id: Optional[str] = None
) -> None:
    """Call upload_rendered and display the resulting URL."""
    assert st is not None
    try:
        from yt_audio_filter.overlay_pipeline import upload_rendered
    except ImportError as exc:
        st.error(
            "Backend function `upload_rendered` is not available.\n\n"
            f"ImportError: {exc}"
        )
        return

    path: Optional[Path] = st.session_state.get("rendered_path")
    vars_ = st.session_state.get("rendered_title_vars", {})
    if path is None or not vars_:
        st.error("No rendered video in session; render first.")
        return

    with st.spinner("Uploading to YouTube..."):
        try:
            url = upload_rendered(
                rendered_path=path,
                metadata=metadata,
                surah_numbers=vars_["surah_numbers"],
                reciter_slug=vars_["reciter_slug"],
                visual_title=vars_.get("visual_title"),
                playlist_id=playlist_id,
            )
        except OverlayError as exc:
            st.error(exc.message)
            if exc.details:
                st.caption(exc.details)
            return
        except Exception as exc:  # noqa: BLE001
            st.exception(exc)
            return

    st.success("Uploaded.")
    st.markdown(f"[Open on YouTube]({url})")


# ---------------------------------------------------------------------------
# Tab: Surah render (legacy flow)
# ---------------------------------------------------------------------------


def _classify_music_removal_stage(stage: str) -> str:
    """Map a ``pipeline.process_video`` stage name to one of three UI
    buckets: ``"extract"``, ``"demucs"``, ``"remux"``, or ``"other"``.

    The Streamlit tab renders three progress bars (one per bucket); the
    callback uses this routing to advance the right one. Chunked-mode
    stages share buckets with their non-chunked equivalents because
    visually they're the same step (split/extract = setup,
    process-chunks = Demucs work, concatenate = remux).
    """
    s = (stage or "").strip().lower()
    if not s:
        return "other"
    if "extract" in s or "split" in s:
        return "extract"
    if "isolat" in s or "demucs" in s or "vocal" in s or "process chunks" in s:
        return "demucs"
    if "remux" in s or "concatenate" in s:
        return "remux"
    return "other"


def _render_music_removal_and_display(
    youtube_url: str,
    privacy: str,
    do_upload: bool,
    playlist_id: Optional[str],
) -> None:
    """Download the YouTube video, run ``pipeline.process_video`` to
    strip background music via Demucs, store the result in session
    state, and (optionally) upload with auto-SEO metadata.

    Streams progress via three ``st.progress`` bars routed through
    :func:`_classify_music_removal_stage`.
    """
    assert st is not None
    try:
        from yt_audio_filter.pipeline import process_video
        from yt_audio_filter.youtube import download_youtube_video, is_youtube_url
    except ImportError as exc:
        st.error(f"Backend imports failed: {exc}")
        return

    if not is_youtube_url(youtube_url):
        st.error("Please enter a valid YouTube URL.")
        return

    cache_dir = DEFAULT_CACHE_DIR / "youtube"
    output_dir = DEFAULT_CACHE_DIR / "music_removed"
    output_dir.mkdir(parents=True, exist_ok=True)

    st.markdown("**1/2 — Downloading source video**")
    with st.spinner("Downloading from YouTube..."):
        try:
            video_metadata = download_youtube_video(
                url=youtube_url,
                output_dir=cache_dir,
                use_cache=True,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Download failed: {exc!r}")
            return

    st.markdown(
        f"**2/2 — Removing music** "
        f"({video_metadata.duration//60}:{video_metadata.duration%60:02d}, "
        f"~30-60s per minute on GPU)"
    )

    bar_extract = st.progress(0, text="Extract audio")
    bar_demucs = st.progress(0, text="Isolate vocals (Demucs)")
    bar_remux = st.progress(0, text="Remux video")

    def on_progress(stage: str, percent: int, _info: Optional[dict] = None) -> None:
        bucket = _classify_music_removal_stage(stage)
        try:
            pct = max(0, min(100, int(percent)))
        except (TypeError, ValueError):
            return
        if bucket == "extract":
            bar_extract.progress(pct, text=f"Extract audio · {pct}%")
        elif bucket == "demucs":
            bar_demucs.progress(pct, text=f"Isolate vocals · {pct}%")
        elif bucket == "remux":
            bar_remux.progress(pct, text=f"Remux video · {pct}%")

    output_path = output_dir / f"music_removed_{video_metadata.video_id}.mp4"
    try:
        process_video(
            input_path=video_metadata.file_path,
            output_path=output_path,
            progress_callback=on_progress,
        )
    except Exception as exc:  # noqa: BLE001
        st.exception(exc)
        return

    st.session_state["rendered_path"] = output_path
    st.session_state["rendered_kind"] = "music_removal"
    st.session_state["music_removal_metadata"] = video_metadata

    st.success(f"Music removed: {output_path}")
    try:
        st.video(str(output_path))
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Inline preview failed ({exc}); use the download button.")
    try:
        data = output_path.read_bytes()
        st.download_button(
            label="Download MP4",
            data=data,
            file_name=output_path.name,
            mime="video/mp4",
        )
    except OSError as exc:
        st.error(f"Cannot read rendered file: {exc}")

    if do_upload:
        from yt_audio_filter.uploader import upload_to_youtube

        st.markdown("**Uploading to YouTube** (auto-SEO metadata)")
        with st.spinner("Uploading..."):
            try:
                video_id = upload_to_youtube(
                    video_path=output_path,
                    original_metadata=video_metadata,
                    privacy=privacy,
                    playlist_id=playlist_id,
                )
            except Exception as exc:  # noqa: BLE001
                st.exception(exc)
                return
        url = f"https://www.youtube.com/watch?v={video_id}"
        st.success(f"Uploaded: {url}")
        st.markdown(f"[Open on YouTube]({url})")


def _render_tab_music_removal(playlist_id: Optional[str]) -> None:
    """The "Music removal" tab — strip background music from a YouTube
    video using Demucs, optionally upload with auto-SEO metadata.

    No metadata template, no surah picker, no cartoon gallery. Single
    URL in, single MP4 out (with optional upload). Reuses the legacy
    music-removal pipeline (`pipeline.process_video`) verbatim — this is
    just the Streamlit-side wiring that the legacy CLI users already
    have via ``yt-audio-filter <url>``.
    """
    assert st is not None
    st.subheader("Music removal — strip background music with Demucs")
    st.caption(
        "Removes music from a YouTube video while preserving vocals / "
        "dialogue. Output is the original video re-muxed with the "
        "isolated vocal track. Uses your GPU (CUDA) when available."
    )

    url = st.text_input(
        "YouTube URL",
        value="",
        placeholder="https://www.youtube.com/watch?v=...",
        key="music_removal_url",
        help="Paste a YouTube URL. Long videos (>30 min) auto-chunk.",
    )

    col_upload, col_privacy = st.columns([1, 1])
    with col_upload:
        do_upload = st.checkbox(
            "Upload to YouTube after processing",
            value=False,
            key="music_removal_upload",
        )
    with col_privacy:
        privacy = st.selectbox(
            "Privacy",
            options=("private", "unlisted", "public"),
            index=0,
            key="music_removal_privacy",
            help="Only used when 'Upload' is checked. Private is safest for first runs.",
            disabled=not do_upload,
        )

    ready = bool(url.strip())
    if not ready:
        st.info("Paste a YouTube URL to enable Process.")

    process_clicked = st.button(
        "Process",
        type="primary",
        disabled=not ready,
        key="music_removal_process",
    )

    if process_clicked and ready:
        _render_music_removal_and_display(
            youtube_url=url.strip(),
            privacy=privacy,
            do_upload=do_upload,
            playlist_id=playlist_id,
        )

    # Allow a one-click upload AFTER an initial render (if the user did
    # not check "Upload" first). Mirrors the surah/ayah-tab pattern.
    rp = st.session_state.get("rendered_path")
    rk = st.session_state.get("rendered_kind")
    if rp and rk == "music_removal" and not process_clicked:
        st.divider()
        st.caption("Last music-removed file is cached in session.")
        if st.button(
            "Upload last result to YouTube",
            type="secondary",
            key="music_removal_upload_last",
        ):
            md = st.session_state.get("music_removal_metadata")
            if md is None:
                st.error("Source metadata is missing from session; rerun Process.")
                return
            from yt_audio_filter.uploader import upload_to_youtube

            with st.spinner("Uploading..."):
                try:
                    video_id = upload_to_youtube(
                        video_path=rp,
                        original_metadata=md,
                        privacy=privacy,
                        playlist_id=playlist_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.exception(exc)
                    return
            url_out = f"https://www.youtube.com/watch?v={video_id}"
            st.success(f"Uploaded: {url_out}")


def _render_tab_simple(
    surahs: List[SurahEntry],
    channels: List[CartoonChannel],
    metadata: Optional[OverlayMetadata],
    upscale: bool,
    playlist_id: Optional[str],
) -> None:
    """The classic surah-render flow, nested in its own tab."""
    assert st is not None
    surah_numbers = _surah_picker(surahs)
    reciter = _reciter_picker(only_everyayah=False, key="reciter_select")
    visual = _cartoon_gallery(channels, key_prefix="simple_gallery")

    ready = (
        metadata is not None
        and bool(surah_numbers)
        and reciter is not None
        and visual is not None
    )

    missing: List[str] = []
    if metadata is None:
        missing.append("valid metadata JSON")
    if not surah_numbers:
        missing.append("at least one surah")
    if reciter is None:
        missing.append("a reciter")
    if visual is None:
        missing.append("exactly one cartoon video")
    if missing:
        st.info("Select: " + ", ".join(missing))

    render_clicked = st.button(
        "Render",
        type="primary",
        disabled=not ready,
        key="render_button",
    )

    if render_clicked and ready:
        assert metadata is not None and reciter is not None and visual is not None
        _render_and_display(
            surah_numbers=surah_numbers,
            reciter=reciter,
            visual=visual,
            metadata=metadata,
            upscale=upscale,
        )

    if (
        metadata is not None
        and st.session_state.get("rendered_kind") == "surah"
    ):
        _preview_and_download(metadata, playlist_id=playlist_id)


# ---------------------------------------------------------------------------
# Tab: Ayah range (memorisation)
# ---------------------------------------------------------------------------


def _add_ayah_range_callback() -> None:
    """Append a fresh blank row to the session-state range list."""
    if st is None:  # pragma: no cover
        return
    rows = list(st.session_state.get("ayah_ranges", []))
    rows.append(
        {
            "surah": 1,
            "start": 1,
            "end": 1,
            "repeats": 3,
            "gap": 0.0,
        }
    )
    st.session_state["ayah_ranges"] = rows


def _remove_ayah_range_callback(idx: int) -> None:
    if st is None:  # pragma: no cover
        return
    rows = list(st.session_state.get("ayah_ranges", []))
    if 0 <= idx < len(rows):
        del rows[idx]
        st.session_state["ayah_ranges"] = rows


def _render_tab_ayah(
    channels: List[CartoonChannel],
    metadata: Optional[OverlayMetadata],
    upscale: bool,
    preset_slug: str,
    burn_subtitles: bool,
    playlist_id: Optional[str],
) -> None:
    """Ayah-range memorisation tab (wishlist M2/M3)."""
    assert st is not None

    st.markdown(
        "Build a memorisation render by listing one or more **(surah, "
        "start..end, repeats, gap)** blocks. The audio plays each block "
        "back-to-back with optional silent gaps between repeats — useful "
        "for sabaq drilling."
    )

    # Initialise with one row when the list is empty so the user has
    # something to fill in immediately.
    if not st.session_state.get("ayah_ranges"):
        _add_ayah_range_callback()

    rows: List[dict] = list(st.session_state.get("ayah_ranges", []))

    # ------------------------------------------------------------------
    # Per-row controls
    # ------------------------------------------------------------------
    new_rows: List[dict] = []
    for i, row in enumerate(rows):
        with st.container(border=True):
            st.caption(f"Range {i + 1}")
            # Layout: surah | from | to | repeats | gap | (delete)
            c_surah, c_from, c_to, c_rep, c_gap, c_del = st.columns(
                [3, 2, 2, 2, 2, 1]
            )
            surah_options = list(range(1, 115))
            label_by_number = {e.number: e.label for e in _surah_entries()}
            with c_surah:
                surah = st.selectbox(
                    "Surah",
                    options=surah_options,
                    index=surah_options.index(row.get("surah", 1)),
                    format_func=lambda n: label_by_number.get(n, str(n)),
                    key=f"ayah_surah_{i}",
                )
            try:
                max_ayah = ayah_data.ayah_count(surah)
            except Exception:
                max_ayah = 286
            with c_from:
                start = st.number_input(
                    "From ayah",
                    min_value=1,
                    max_value=max_ayah,
                    value=min(int(row.get("start", 1) or 1), max_ayah),
                    step=1,
                    key=f"ayah_start_{i}",
                )
            with c_to:
                end_default = min(int(row.get("end", start) or start), max_ayah)
                end_default = max(end_default, int(start))
                end = st.number_input(
                    "To ayah",
                    min_value=int(start),
                    max_value=max_ayah,
                    value=end_default,
                    step=1,
                    key=f"ayah_end_{i}",
                )
            with c_rep:
                repeats = st.number_input(
                    "Repeats",
                    min_value=1,
                    max_value=99,
                    value=int(row.get("repeats", 3) or 3),
                    step=1,
                    key=f"ayah_repeats_{i}",
                )
            with c_gap:
                gap = st.number_input(
                    "Gap (s)",
                    min_value=0.0,
                    max_value=5.0,
                    value=float(row.get("gap", 0.0) or 0.0),
                    step=0.5,
                    key=f"ayah_gap_{i}",
                    help=(
                        "Silent gap between repeats. Use 3-5s for "
                        "self-test prompt mode."
                    ),
                )
            with c_del:
                st.markdown("&nbsp;")  # spacer
                st.button(
                    "✕",
                    key=f"ayah_del_{i}",
                    help="Remove this range",
                    on_click=_remove_ayah_range_callback,
                    args=(i,),
                )
            new_rows.append(
                {
                    "surah": int(surah),
                    "start": int(start),
                    "end": int(end),
                    "repeats": int(repeats),
                    "gap": float(gap),
                }
            )
    # Sync edits back to session state without losing additions/removals.
    if new_rows != rows:
        st.session_state["ayah_ranges"] = new_rows
    rows = new_rows

    st.button(
        "+ Add another range",
        on_click=_add_ayah_range_callback,
        key="ayah_add_range",
    )

    st.divider()

    # ------------------------------------------------------------------
    # Reciter + visual
    # ------------------------------------------------------------------
    reciter = _reciter_picker(only_everyayah=True, key="reciter_select_ayah")
    visual = _cartoon_gallery(channels, key_prefix="ayah_gallery")

    # ------------------------------------------------------------------
    # Validation + Render button
    # ------------------------------------------------------------------
    missing: List[str] = []
    if metadata is None:
        missing.append("valid metadata JSON")
    if not rows:
        missing.append("at least one ayah range")
    if reciter is None:
        missing.append("a reciter")
    if visual is None:
        missing.append("exactly one cartoon video")
    if missing:
        st.info("Select: " + ", ".join(missing))

    ready = not missing

    if st.button(
        "Render ayah-range video",
        type="primary",
        disabled=not ready,
        key="render_button_ayah",
    ):
        if not ready:
            return
        # Build AyahRange instances; surface validation errors inline.
        try:
            from yt_audio_filter.ayah_repeater import AyahRange

            ranges = [
                AyahRange(
                    surah=r["surah"],
                    start=r["start"],
                    end=r["end"],
                    repeats=r["repeats"],
                    gap_seconds=r["gap"],
                )
                for r in rows
            ]
        except (ValueError, OverlayError) as exc:
            st.error(f"Range validation failed: {exc}")
            return

        # Pick the EveryAyah short slug for the chosen reciter (the
        # backend accepts both the short slug and the folder path).
        short_slug = reciter.slug  # type: ignore[union-attr]
        # Sanity check — the picker has already filtered, but be loud.
        valid_qa_slugs = {
            entry["quranicaudio_slug"]
            for entry in ayah_data.EVERYAYAH_RECITERS.values()
        }
        if short_slug not in valid_qa_slugs:
            st.error(
                f"Reciter {short_slug!r} has no EveryAyah folder; "
                "pick a different one."
            )
            return

        assert metadata is not None and visual is not None
        _render_ayah_ranges_and_display(
            ranges=ranges,
            reciter_slug=short_slug,
            visual=visual,
            metadata=metadata,
            upscale=upscale,
            preset_slug=preset_slug,
            burn_subtitles=burn_subtitles,
            playlist_id=playlist_id,
        )

    if (
        metadata is not None
        and st.session_state.get("rendered_kind") == "ayah"
    ):
        _preview_and_download(metadata, playlist_id=playlist_id)


# ---------------------------------------------------------------------------
# Tab: Weekly lesson plan
# ---------------------------------------------------------------------------


def _render_tab_lesson() -> None:
    """Run a weekly lesson plan (wishlist C1).

    Synchronous on purpose: ``lesson_planner.render_plan`` does N back-to-
    back FFmpeg renders and the Streamlit thread is the simplest place
    to drive it. We surface that with a clear warning before kick-off so
    the teacher knows the page will sit unresponsive until done. A
    threaded variant with a ``queue.Queue`` polled via ``st.empty`` is
    a Phase 3 follow-up.
    """
    assert st is not None
    st.markdown(
        "Render every lesson in a JSON plan back-to-back. Useful for the "
        "Saturday-evening 'prep next week's videos' workflow."
    )

    plan_path = st.text_input(
        "Lesson plan JSON path",
        value=st.session_state.get("lesson_plan_path", DEFAULT_LESSON_PLAN_PATH),
        key="lesson_plan_path_input",
        help="Path to a weekly-plan JSON. See examples/lesson-plan-week.json.",
    )
    st.session_state["lesson_plan_path"] = plan_path

    cols = st.columns([1, 1, 4])
    with cols[0]:
        if st.button("Validate plan", key="lesson_validate"):
            try:
                from yt_audio_filter.lesson_planner import load_plan

                plan = load_plan(Path(plan_path))
                st.session_state["lesson_plan_validated"] = True
                st.session_state["lesson_plan_error"] = ""
                st.session_state["lesson_plan_summary"] = (
                    f"{len(plan.lessons)} lesson(s); week of {plan.week_of}; "
                    f"reciter={plan.default_reciter}"
                )
            except OverlayError as exc:
                st.session_state["lesson_plan_validated"] = False
                msg = exc.message
                if exc.details:
                    msg = f"{msg}\n\n{exc.details}"
                st.session_state["lesson_plan_error"] = msg
            except Exception as exc:  # noqa: BLE001
                st.session_state["lesson_plan_validated"] = False
                st.session_state["lesson_plan_error"] = repr(exc)

    if st.session_state.get("lesson_plan_validated"):
        st.success(
            f"✅ Plan OK: {st.session_state.get('lesson_plan_summary', '')}"
        )
    elif st.session_state.get("lesson_plan_error"):
        st.error(f"❌ {st.session_state['lesson_plan_error']}")

    st.warning(
        "Running the plan is **synchronous** — the page will be "
        "unresponsive for the duration of every render. A 5-day plan can "
        "take 30+ minutes depending on FFmpeg and upscale settings. "
        "Don't close the tab; the renders write to disk as they "
        "complete."
    )

    with cols[1]:
        run_clicked = st.button(
            "Run plan",
            type="primary",
            disabled=not st.session_state.get("lesson_plan_validated"),
            key="lesson_run",
        )

    # Placeholders for streaming progress.
    status_placeholder = st.empty()
    results_placeholder = st.empty()
    errors_placeholder = st.empty()

    if run_clicked and st.session_state.get("lesson_plan_validated"):
        try:
            from yt_audio_filter.lesson_planner import load_plan, render_plan

            plan = load_plan(Path(plan_path))
        except OverlayError as exc:
            st.error(f"Plan re-load failed: {exc.message}")
            return

        # Reset the previous run's tables.
        st.session_state["lesson_results"] = []
        st.session_state["lesson_errors"] = []

        def on_lesson_start(lesson, idx: int, total: int) -> None:
            status_placeholder.info(
                f"Lesson {idx + 1}/{total}: {lesson.day} — surahs "
                f"{lesson.surah_numbers}"
            )

        def on_lesson_done(lesson, output_path: Path) -> None:
            st.session_state["lesson_results"].append(
                (lesson.day, str(output_path))
            )

        def on_lesson_error(lesson, exc: Exception) -> None:
            st.session_state["lesson_errors"].append((lesson.day, str(exc)))

        try:
            render_plan(
                plan,
                output_dir=DEFAULT_OUTPUT_DIR,
                cache_dir=DEFAULT_CACHE_DIR,
                on_lesson_start=on_lesson_start,
                on_lesson_done=on_lesson_done,
                on_lesson_error=on_lesson_error,
            )
            status_placeholder.success(
                f"Plan finished: "
                f"{len(st.session_state['lesson_results'])} OK, "
                f"{len(st.session_state['lesson_errors'])} failed"
            )
        except Exception as exc:  # noqa: BLE001
            status_placeholder.error(f"Plan run aborted: {exc}")

    # Render the results / errors tables on every rerun so they survive
    # navigation across tabs.
    results = st.session_state.get("lesson_results") or []
    errors = st.session_state.get("lesson_errors") or []

    if results:
        with results_placeholder.container():
            st.subheader("Results")
            st.table(
                {
                    "Day": [r[0] for r in results],
                    "Output": [r[1] for r in results],
                }
            )
            for day, path_str in results:
                p = Path(path_str)
                if p.exists():
                    try:
                        st.download_button(
                            label=f"Download {p.name}",
                            data=p.read_bytes(),
                            file_name=p.name,
                            mime="video/mp4",
                            key=f"lesson_dl_{day}_{p.name}",
                        )
                    except OSError as exc:
                        st.warning(f"Could not read {p}: {exc}")

    if errors:
        with errors_placeholder.container():
            st.subheader("Errors")
            st.table(
                {
                    "Day": [e[0] for e in errors],
                    "Error": [e[1] for e in errors],
                }
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Streamlit ``main()``. Safe to import; renders when Streamlit is live."""
    if st is None:
        raise RuntimeError(
            "streamlit is not installed. Install with: pip install -e '.[app]'"
        )

    # set_page_config must be the FIRST Streamlit call on every rerun.
    st.set_page_config(page_title="yt-quran-overlay", layout="wide")
    _init_session_state()

    surahs = _surah_entries()
    (
        metadata,
        _metadata_path,
        upscale,
        preset_slug,
        burn_subtitles,
        playlist_id,
    ) = _sidebar(surahs)

    try:
        channels = _load_channels_cached(str(DEFAULT_CHANNELS_PATH))
    except Exception as e:
        st.error(f"Cannot load channels: {e}")
        channels = []

    # Review fix: prune ``ch_<slug>`` keys for slugs that no longer exist.
    _prune_stale_channel_filters(channels)

    tab_simple, tab_ayah, tab_lesson, tab_music = st.tabs(
        [
            "Surah render",
            "Ayah range (memorization)",
            "Weekly lesson plan",
            "Music removal",
        ]
    )

    with tab_simple:
        _render_tab_simple(
            surahs=surahs,
            channels=channels,
            metadata=metadata,
            upscale=upscale,
            playlist_id=playlist_id,
        )

    with tab_ayah:
        _render_tab_ayah(
            channels=channels,
            metadata=metadata,
            upscale=upscale,
            preset_slug=preset_slug,
            burn_subtitles=burn_subtitles,
            playlist_id=playlist_id,
        )

    with tab_lesson:
        _render_tab_lesson()

    with tab_music:
        _render_tab_music_removal(playlist_id=playlist_id)


if __name__ == "__main__":  # pragma: no cover
    main()
