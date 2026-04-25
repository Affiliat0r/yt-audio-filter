"""Streamlit UI for yt-quran-overlay.

Single-page local app that wires together:

* ``quran_audio_source`` for the reciter list + sample previews
* ``cartoon_catalog`` for the cartoon thumbnail gallery
* ``overlay_pipeline.run_overlay_from_surah_numbers`` for rendering
  (Agent C; imported lazily at render time so the app still boots if the
  contract function hasn't landed yet)
* ``metadata.load_metadata`` for the publish metadata template
* ``surah_detector._SURAHS`` as the canonical source for the 114 surah
  numbers + canonical English names

The module is designed to be executed via ``streamlit run``, but
``main()`` is importable so a smoke test can verify the module loads.
Top-level code runs on every Streamlit rerun; keep it cheap and
idempotent.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections import defaultdict
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
from yt_audio_filter.metadata import OverlayMetadata, load_metadata
from yt_audio_filter.quran_audio_source import Reciter, is_surah_cached, list_reciters
from yt_audio_filter.surah_detector import _SURAHS


# ---------------------------------------------------------------------------
# Constants & small data holders
# ---------------------------------------------------------------------------

DEFAULT_METADATA_PATH = "examples/metadata-surah-arrahman.json"
LOG_BUFFER_MAX_LINES = 40
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


@_cache_data
def _ensure_thumbnail_cached(
    video_id: str, thumbnail_url: str, cache_dir_str: str
) -> Optional[str]:
    """Return the local thumbnail path as a string, or ``None`` on failure.

    Takes ``thumbnail_url`` explicitly so we don't have to round-trip
    through ``list_videos`` (which would tie the cache key to the
    catalog cache-bust counter). A ``CatalogVideo`` is reconstructed
    with only the fields ``ensure_thumbnail`` reads.
    """
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
    """Return ``[is_cached_for_surah_1, ..., is_cached_for_surah_114]``.

    Keyed on ``(reciter_slug, cache_dir, cache_bust)`` so the panel
    doesn't re-stat 114 files on every Streamlit rerun. Cleared by
    bumping ``st.session_state["audio_cache_bust"]``.
    """
    cache_dir = Path(cache_dir_str)
    return [is_surah_cached(n, reciter_slug, cache_dir) for n in range(1, 115)]


@_cache_data
def _load_metadata_cached(
    path_str: str, mtime_ns: int
) -> Tuple[bool, str, Optional[OverlayMetadata]]:
    """Return ``(ok, message, metadata_or_none)`` for the sidebar badge.

    ``mtime_ns`` is part of the cache key so edits to the JSON file
    are picked up on the next rerun without a process restart.
    """
    del mtime_ns  # only used for cache invalidation
    try:
        meta = load_metadata(Path(path_str))
    except Exception as exc:  # noqa: BLE001 - surface any validation error
        return False, str(exc), None
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
    ss.setdefault("selected_visual_id", None)
    ss.setdefault("catalog_cache_bust", 0)
    ss.setdefault("audio_cache_bust", 0)


def _sidebar(surahs: List[SurahEntry]) -> Tuple[Optional[OverlayMetadata], str, bool]:
    """Render the sidebar. Returns (metadata_or_none, metadata_path, upscale)."""
    assert st is not None
    st.sidebar.title("yt-quran-overlay")

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

    return meta, metadata_path, upscale


def _surah_picker(surahs: List[SurahEntry]) -> List[int]:
    """Return the selected surah numbers expanded by per-surah repeats.

    Order is preserved from the multiselect. Each row gets a number_input
    (1..99); an entry with repeat=N expands to N consecutive copies of
    that surah number, so the downstream pipeline concatenates the same
    audio file N times.
    """
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

    # Per-surah repeat widget. Defaults to 1 so the existing single-pick
    # flow is unchanged. Cap at 99; that's already a 50+ minute video for
    # most surahs.
    st.caption("Audio plays in this order; the visual loops underneath.")
    expanded: List[int] = []
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
        expanded.extend([number] * int(repeats))
    return expanded


def _reciter_picker() -> Optional[Reciter]:
    """Reciter selectbox + sample-audio preview."""
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

    names = [r.display_name for r in reciters]
    chosen_name = st.selectbox("Reciter", options=names, index=0, key="reciter_select")
    chosen = next((r for r in reciters if r.display_name == chosen_name), None)
    if chosen is None:
        return None

    # Sample preview. We hand the remote URL directly to st.audio; it does
    # its own HTTP range requests, so we don't burn bandwidth pre-fetching.
    st.audio(chosen.sample_url)
    st.caption(f"Slug: `{chosen.slug}`  —  sample: Al-Fatiha")

    _cached_audio_panel(chosen)
    return chosen


def _cached_audio_panel(reciter: Reciter) -> None:
    """Collapsible panel showing which surahs are already cached on disk.

    Mirrors the gallery's per-visual badge pattern but for the 114 surahs
    of the currently-selected reciter. Informational only — selection
    happens upstairs in the multiselect.
    """
    assert st is not None
    cache_bust = st.session_state.get("audio_cache_bust", 0)
    statuses = _cached_surah_status(reciter.slug, str(DEFAULT_CACHE_DIR), cache_bust)
    cached_count = sum(1 for s in statuses if s)

    expander_label = (
        f"Cached audio for this reciter ({cached_count} / 114)"
    )
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

        # Compact 4-column grid of "✅ 001. Al-Fatiha" / "·  002. Al-Baqarah".
        # Avoids 114 separate widgets — just markdown lines.
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
    """Scan ``cache_dir`` once and return ``{video_id: state}``.

    Caches one ``os.scandir`` pass instead of N×5 ``Path.exists`` calls
    inside the gallery loop. Invalidated by bumping
    ``st.session_state['visual_cache_bust']``.
    """
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
    """Return ``'upscaled' | 'downloaded' | 'new'`` for ``video_id``.

    Pass ``index`` from ``_visual_state_index`` to avoid re-stating; falls
    back to the slow per-video path if no index is given (kept for tests).
    """
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
    """Click handler for the per-tile Select button. Mutual-exclusion.

    Used as ``on_click`` so the click is processed BEFORE the next
    rerun renders, ensuring exactly one ``selected_visual_id`` survives
    even if the click happens to be on a tile not currently visible.
    """
    if st is None:  # pragma: no cover
        return
    if st.session_state.get("selected_visual_id") == video_id:
        st.session_state["selected_visual_id"] = None
    else:
        st.session_state["selected_visual_id"] = video_id


def _cartoon_gallery(channels: List[CartoonChannel]) -> Optional[CatalogVideo]:
    """Thumbnail grid with filters, badges, and cached-first ordering.

    Selection is tracked solely via ``st.session_state['selected_visual_id']``
    and a click-callback per tile; the gallery does NOT clear the id when
    the selected tile is offscreen, so paginating or filtering preserves
    the user's choice.
    """
    assert st is not None
    st.subheader("Cartoon video")

    col_refresh, col_search = st.columns([1, 3])
    with col_refresh:
        # One-shot button; the previous st.toggle was sticky and re-deleted
        # the catalog cache on every rerun for as long as it was ON.
        if st.button("Refresh catalog", help="Rescrape channels, then relist."):
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
            # Also nuke the on-disk visual-state index since cached files
            # may have changed alongside the catalog.
            st.session_state["visual_cache_bust"] = (
                st.session_state.get("visual_cache_bust", 0) + 1
            )
    with col_search:
        search = st.text_input(
            "Filter by title", value="", placeholder="e.g. train, bus, dinosaur"
        ).strip().lower()

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

    # Channel filter (defaults: all checked)
    all_slugs = [c.slug for c in channels]
    for v in videos:
        if v.channel_slug not in all_slugs:
            all_slugs.append(v.channel_slug)
    with st.expander(f"Filter by channel ({len(all_slugs)} total)", expanded=False):
        active_slugs = set()
        cols = st.columns(min(len(all_slugs), 5))
        for i, slug in enumerate(all_slugs):
            with cols[i % len(cols)]:
                if st.checkbox(_channel_display_name(channels, slug),
                               value=True, key=f"ch_{slug}"):
                    active_slugs.add(slug)

    sort_mode = st.selectbox(
        "Sort",
        options=("Downloaded first", "Longest first", "Shortest first",
                 "Most viewed", "Newest", "Title A-Z"),
        index=0,
    )

    # Reset pagination whenever the filter / sort / search changes so a
    # user on page 5 doesn't end up looking at unrelated tiles after
    # narrowing the result set.
    filter_signature = (search, sort_mode, frozenset(active_slugs))
    last_signature = st.session_state.get("gallery_filter_signature")
    if last_signature is not None and last_signature != filter_signature:
        st.session_state["gallery_page"] = 0
    st.session_state["gallery_filter_signature"] = filter_signature

    # One stat-pass over the cache dir, memoized by st.cache_data — the
    # previous per-video Path.exists ran ~5N stat calls per rerun.
    state_index = _visual_state_index(
        str(DEFAULT_CACHE_DIR),
        st.session_state.get("visual_cache_bust", 0),
    )

    filtered = [
        v for v in videos
        if v.channel_slug in active_slugs
        and (not search or search in v.title.lower())
    ]

    rank_state = {"upscaled": 0, "downloaded": 1, "new": 2}
    if sort_mode == "Downloaded first":
        filtered.sort(key=lambda v: (rank_state[state_index.get(v.video_id, "new")], -v.view_count))
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

    n_total = len(videos)
    n_cached = sum(
        1 for v in videos if state_index.get(v.video_id, "new") != "new"
    )
    st.caption(
        f"Showing **{len(filtered)}** of **{n_total}** videos · "
        f"{n_cached} already cached (🟢/🔵) · "
        f"{n_total - n_cached} would download on render."
    )

    if not filtered:
        st.warning("No videos match the current filter/search.")
        return None

    selected_id = st.session_state.get("selected_visual_id")
    # Resolve the persisted selection against the FULL catalog, not just
    # the current page — so flipping pages or filters preserves it.
    selected_video: Optional[CatalogVideo] = next(
        (v for v in videos if v.video_id == selected_id), None
    )

    # Pagination so 247 items don't all render at once.
    PAGE_SIZE = 24
    page = st.session_state.get("gallery_page", 0)
    total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages - 1)
    start = page * PAGE_SIZE
    page_videos = filtered[start:start + PAGE_SIZE]

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
            is_active = (selected_id == v.video_id)
            st.button(
                "✓ Selected (click to deselect)" if is_active else "Select",
                type=("primary" if is_active else "secondary"),
                key=f"sel_btn_{v.video_id}",
                on_click=_select_visual_callback,
                args=(v.video_id,),
                use_container_width=True,
            )

    # Pager
    if total_pages > 1:
        pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
        with pcol1:
            if st.button("◀ Previous", disabled=page == 0, key="gallery_prev"):
                st.session_state["gallery_page"] = max(0, page - 1)
                st.rerun()
        with pcol2:
            st.caption(f"Page {page + 1} / {total_pages}")
        with pcol3:
            if st.button("Next ▶", disabled=page >= total_pages - 1, key="gallery_next"):
                st.session_state["gallery_page"] = min(total_pages - 1, page + 1)
                st.rerun()

    return selected_video


def _render_and_display(
    surah_numbers: List[int],
    reciter: Reciter,
    visual: CatalogVideo,
    metadata: OverlayMetadata,
    upscale: bool,
) -> None:
    """Invoke Agent C's backend, stream logs, store the result in session."""
    assert st is not None
    # Import here so that a missing contract function only breaks at render
    # time, not at module import (lets the smoke test pass regardless).
    try:
        from yt_audio_filter.overlay_pipeline import run_overlay_from_surah_numbers
    except ImportError as exc:
        st.error(
            "Backend function `run_overlay_from_surah_numbers` is not "
            "available yet. Agent C's pipeline extension hasn't landed.\n\n"
            f"ImportError: {exc}"
        )
        return

    project_logger = logging.getLogger("yt_audio_filter")
    buf = _StreamlitLogBuffer()
    previous_level = project_logger.level
    project_logger.setLevel(logging.INFO)
    project_logger.addHandler(buf)

    status = st.status("Rendering...", expanded=True)
    log_placeholder = status.empty()

    # A class so we can periodically flush the buffer to the UI from the
    # logging handler. We override emit() to update Streamlit too.
    class _FlushingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_placeholder.code(buf.snapshot() or "(waiting for logs)")

    flusher = _FlushingHandler()
    project_logger.addHandler(flusher)

    result = None
    try:
        # Agent C's signature takes an optional output_path; we let them
        # default to a NamedTemporaryFile internally per the design doc.
        # We still pass ``output_path=None`` explicitly so the call is
        # robust against a missing default.
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

    st.session_state["rendered_path"] = output_path
    # Stash identifying info so the later Upload button can call
    # upload_rendered() without the user reselecting.
    st.session_state["rendered_title_vars"] = {
        "surah_numbers": list(surah_numbers),
        "reciter_slug": reciter.slug,
        "visual_video_id": visual.video_id,
        "visual_title": visual.title,
    }


def _preview_and_download(metadata: OverlayMetadata) -> None:
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
        _upload_and_show(metadata)


def _upload_and_show(metadata: OverlayMetadata) -> None:
    """Call Agent C's upload_rendered and display the resulting URL."""
    assert st is not None
    try:
        from yt_audio_filter.overlay_pipeline import upload_rendered
    except ImportError as exc:
        st.error(
            "Backend function `upload_rendered` is not available yet.\n\n"
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
            )
        except Exception as exc:  # noqa: BLE001
            st.exception(exc)
            return

    st.success("Uploaded.")
    st.markdown(f"[Open on YouTube]({url})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Streamlit ``main()``. Safe to import; renders when Streamlit is live."""
    if st is None:
        raise RuntimeError(
            "streamlit is not installed. Install with: pip install -e '.[app]'"
        )

    st.set_page_config(page_title="yt-quran-overlay", layout="wide")
    _init_session_state()

    surahs = _surah_entries()
    metadata, _metadata_path, upscale = _sidebar(surahs)

    try:
        channels = _load_channels_cached(str(DEFAULT_CHANNELS_PATH))
    except Exception as e:
        st.error(f"Cannot load channels: {e}")
        channels = []

    surah_numbers = _surah_picker(surahs)
    reciter = _reciter_picker()
    visual = _cartoon_gallery(channels)

    ready = (
        metadata is not None
        and bool(surah_numbers)
        and reciter is not None
        and visual is not None
    )

    # Validation feedback for the Render button.
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

    # Keep the preview + upload section below so it stays visible after
    # the render even though the button click triggered a rerun.
    if metadata is not None:
        _preview_and_download(metadata)


if __name__ == "__main__":  # pragma: no cover
    main()
