# Streamlit App Review — 2026-04-25

## Summary

The Streamlit UI is solidly architected — clean module split, good use of
`st.cache_data`, idempotent module-level code, and a sensible smoke-test
boundary. Most rerun-cost concerns are already handled. The serious issues
are concentrated in the `_cartoon_gallery` selection model (loses the user's
choice on pagination, allows multi-select against design intent) and a few
caching bugs that produce stale UI state (metadata badge, `selected_visual_id`,
gallery page index across filter changes). Nothing here is a hard blocker, but
the gallery selection bug will be confusing to real users.

## High-impact (fix before merge)

- **Selection wiped when paginating the gallery** — `streamlit_app.py:509-510`.
  At the end of `_cartoon_gallery` the code does
  `if selected_video is None: st.session_state["selected_visual_id"] = None`.
  But `selected_video` is computed only from videos rendered on the *current
  page*. A user who selects a tile on page 1 then clicks "Next ▶" lands on
  page 2 with no tile selected → the gallery clears the persisted id and the
  Render button goes back to disabled. Track selection independently of which
  page is currently rendering: only clear `selected_visual_id` when the user
  actively unticks the box for `sel_<that_id>`, not when it isn't on screen.

- **Multi-select against the "exactly one cartoon" contract** —
  `streamlit_app.py:484-493`. Checkboxes don't enforce mutual exclusion. If
  the user ticks two tiles, the loop silently picks the first iterated one as
  `selected_video` and leaves the other tile's checkbox visibly checked.
  Replace per-tile `st.checkbox` with a single `st.radio` over the page (or
  use a "Select" button per tile that calls a callback to set
  `selected_visual_id` and unset others). The design doc explicitly calls out
  "exactly 1 thumbnail" (`docs/plans/2026-04-19-streamlit-app-design.md:98`).

- **Refresh-catalog toggle keeps wiping the cache on every rerun** —
  `streamlit_app.py:362-366, 372-382`. `st.toggle` is sticky: once the user
  flips it ON it stays ON across every subsequent interaction, so each rerun
  re-enters the `if refresh:` branch, deletes `cartoon_catalog.json` again,
  and bumps `catalog_cache_bust`. The catalog gets re-scraped not once but on
  every interaction until the user manually flips the toggle off. Use
  `st.button("Refresh catalog")` (one-shot) instead, or capture the previous
  toggle state in session-state and only act on the OFF→ON transition.

- **Metadata badge never updates after the JSON file is edited** —
  `streamlit_app.py:197-204, 250-254`. `_load_metadata_cached` is keyed on the
  path string with no `ttl` and no mtime in the key. A user who fixes a
  validation error in `examples/metadata-surah-arrahman.json` will keep
  seeing the red badge until the Streamlit process restarts. Either add
  `ttl=10` to the decorator or include `Path(path_str).stat().st_mtime` in
  the cache key. Same applies to the surah / reciter / channels JSONs (less
  likely to be hand-edited mid-session, but the principle is the same).

- **`_visual_download_state` runs N×5 stat calls per rerun, inside a hot
  loop** — `streamlit_app.py:338-345, 422`. For a 250-video catalog that's
  1250 `Path.exists()` calls on every keystroke in the search box, every
  page change, every checkbox toggle. Cache it: scan `cache/` once with
  `os.scandir`, build a `{video_id: state}` dict, wrap in `@_cache_data`
  keyed on the catalog cache-bust counter (so it invalidates on refresh).

- **Render handler doesn't deduplicate stacked log handlers across crashes**
  — `streamlit_app.py:536-577`. The `try/finally` removes the two handlers
  on the happy and exception paths, but if the user clicks Render, navigates
  away mid-render (Streamlit kills the script context), or the process
  recovers from a `KeyboardInterrupt`, both `_StreamlitLogBuffer` and
  `_FlushingHandler` linger on the project logger across reruns. Each
  subsequent Render click adds two more. Defensively scrub any pre-existing
  handlers of those types from `project_logger.handlers` before adding new
  ones, or attach a single sentinel handler keyed on `id(...)` you can
  detect.

- **Closure over `log_placeholder` outlives the render** —
  `streamlit_app.py:543-552`. `_FlushingHandler.emit` captures
  `log_placeholder` (an `st.empty()` from this rerun's status block) in its
  closure. If the handler isn't removed (see prior bullet), a *future*
  rerun's logging call would write to a placeholder bound to a long-dead
  `st.status` context — Streamlit usually no-ops this but the handler holds
  a hard reference to the DeltaGenerator. Combine with the
  scrub-before-attach fix above.

- **Pagination doesn't reset when the filter or sort changes** —
  `streamlit_app.py:459-465`. `gallery_page` only gets clamped via
  `min(page, total_pages - 1)`, so a user on page 5/11 who narrows the
  search drops to page 0 silently — fine — but a user on page 5/11 who
  *flips* sort from "Newest" to "Title A-Z" stays on page 5, looking at
  totally different videos than before they sorted. Reset `gallery_page` to
  0 whenever `(search, sort_mode, frozenset(active_slugs))` differs from the
  last-seen tuple stored in session_state.

- **`upload_with_explicit_metadata` runs synchronously on the main
  thread** — `overlay_pipeline.py:736, streamlit_app.py:644-652`. A 5-minute
  upload over a flaky connection blocks every other widget in the session.
  At minimum, surface a clearer "do not close this tab" warning above the
  spinner; longer-term, the only real fix is a background thread + polling
  via `st.rerun()`. Worth flagging because the upload, unlike the render,
  pushes data to a third party and a half-completed cancel is genuinely
  destructive.

## Medium

- **Stale per-tile checkbox state survives catalog refresh** —
  `streamlit_app.py:484-493`. Each tile's checkbox is keyed `sel_<video_id>`.
  After "Refresh catalog" drops a video, its `sel_<old_id>` entry stays in
  `st.session_state` forever. Not user-visible, but unbounded growth across
  hours of use. After a refresh, prune `session_state` keys starting with
  `sel_` whose video_id isn't in the new `videos` list.

- **`download_button` re-reads the entire MP4 from disk on every rerun** —
  `streamlit_app.py:610-621`. `path.read_bytes()` runs unconditionally
  while the preview is on screen — every time the user types in the surah
  search, the radio for reciter changes, etc. For a 50-MB output file
  that's 50 MB of disk I/O per interaction. Wrap the read in
  `@st.cache_data` keyed on `(path, path.stat().st_mtime)`.

- **`channels.json` parse error in sidebar swallows the message but
  catalog load shows it** — `streamlit_app.py:266-275, 384-392`. The
  sidebar's About expander shows `st.warning(f"channels.json error: {e}")`
  *only when expanded*, so a user who never opens it sees an empty gallery
  and "Catalog load failed: …" on the main panel — confusing because the
  root cause is on a different surface. Promote the channels.json warning
  out of the expander or, conversely, suppress the main-panel error when
  channels.json is the cause.

- **`run_overlay_from_surah_numbers` is not given an `output_path`, so
  every render lands in `tempfile.gettempdir()` with a deterministic
  name** — `overlay_pipeline.py:693-695`, called from
  `streamlit_app.py:560-569`. Two simultaneous renders for the same
  `(surah_numbers, visual_video_id)` write to the same path. The CLI sets
  `force=True` so render-overlap clobbers; the UI flow likely wants the
  same render to be cache-hit safe. Either include `reciter_slug` in
  `_surah_numbers_output_filename` or pass an explicit `output_path` from
  the UI.

- **`st.audio(chosen.sample_url)` re-fires on every rerun** —
  `streamlit_app.py:333. Streamlit handles HTTP range requests, but
  switching reciters causes a fresh `st.audio` element with a new URL,
  and changing any unrelated widget causes the audio element to re-render
  with the same URL — playback restarts. Acceptable, but worth noting if
  users complain. If it bites, gate behind an explicit "Preview" button.

- **`tempfile` and `defaultdict` and `json` are imported but unused** —
  `streamlit_app.py:22, 24, 25`. Dead imports.

- **`_StreamlitLogBuffer` formatter is set in `__init__` but the project
  logger may already have a handler-level formatter** — `streamlit_app.py:111`.
  Buffer-only formatting is fine, but the `_FlushingHandler` doesn't set its
  own formatter, so its default `format(record)` produces a different shape
  than the buffer's. Since `_FlushingHandler` only calls `buf.snapshot()`
  (not `record.getMessage()`), this is moot — but it leaves the impression
  that the flusher is doing the formatting. Drop the `setFormatter` from
  `_FlushingHandler` (it sets none today, but a future maintainer might add
  one). Or merge the two handlers into one that both buffers and flushes.

- **`_resolve_visual_video` does a fresh `list_videos` call per render** —
  `overlay_pipeline.py:560-569`. The Streamlit cache holds the catalog,
  but `run_overlay_from_surah_numbers` re-does the scrape (or at least the
  cache read) inside the pipeline. Pass the resolved `CatalogVideo`
  directly from the UI instead of re-resolving by id.

- **`get_reciter` raises `OverlayError` on missing slug — fine — but the
  error renders via `st.exception` showing a Python traceback** —
  `streamlit_app.py:653`. End-users on a local UI shouldn't see
  tracebacks. Use `st.error(str(exc))` for `OverlayError` specifically and
  `st.exception` only for unexpected exception types.

- **`st.toggle` for "Refresh catalog" sits *above* the search box but
  visually reads as a global control**. With the button-fix above, also
  consider moving it next to the page total ("Showing 247 videos · 🔄").

- **No way to clear a render** — `streamlit_app.py:587-595`. Once
  `rendered_path` is set in session_state, the preview + download +
  upload section sticks around forever. Add a "Clear" button next to
  "Download MP4" that clears `rendered_path` / `rendered_title_vars`.

- **`upload_rendered` requires the file to still exist on disk** —
  `overlay_pipeline.py:787-792`. The render goes to
  `tempfile.gettempdir()`. On Windows that's
  `%LOCALAPPDATA%\Temp` which the OS clears on reboot. A user who
  renders, sleeps overnight, comes back and clicks Upload may get a
  cryptic "file not found" when the temp file was reaped. Either copy to
  `output/` on success or warn explicitly.

## Low / nits

- **`assert st is not None` in every UI helper** — `streamlit_app.py:232,
  242, 294, 314, 357, 523, 600, 628`. The module-level fallback is the
  only place where `st is None` is possible; once `main()` has guarded it
  the inner functions don't need 8 repeated asserts. Extract a single
  guard in `main()` and let the rest assume `st`.

- **`_ensure_thumbnail_cached` returns `Optional[str]` but `st.image`
  takes a path** — `streamlit_app.py:470-476`. Works, but the type
  contract on `st.image` is `str | bytes | PIL.Image | …`; passing a
  `Path` would also work. Document the choice.

- **`thumb_path` truthy check assumes empty-string is falsy** —
  `streamlit_app.py:473`. `_ensure_thumbnail_cached` returns either a
  non-empty path string or `None`. Tighten to `if thumb_path is not None:`.

- **`_format_duration` uses `int(seconds or 0)` then re-tests `seconds <= 0`** —
  `streamlit_app.py:212-220`. `int(None or 0)` is `0`, which triggers
  the `<= 0` branch and returns `"?"`. But a 0-second video also returns
  `"?"` — mildly misleading. Differentiate or rename.

- **`channel_slug` lookup is O(N×M) inside the loop** —
  `streamlit_app.py:223-227, 481`. Build a `{slug: display_name}` dict
  once at the top of `_cartoon_gallery`.

- **`PAGE_SIZE = 24` is a magic constant inside the function** —
  `streamlit_app.py:459`. Promote to module-level next to
  `LOG_BUFFER_MAX_LINES`.

- **`sort_mode` strings are duplicated for matching** —
  `streamlit_app.py:414-441`. Replace with an enum + dict-of-callables,
  or at least a single `OPTIONS = (...)` tuple referenced by both
  `selectbox` and the `if/elif` ladder.

- **Channel-filter checkboxes default to all checked, but the `expander`
  is collapsed by default** — `streamlit_app.py:405`. A user who never
  opens it can't tell which channels are filtering the view. Show a
  caption like "Channels: 5/5 active" outside the expander.

- **`active_slugs = set()` inside the expander is rebuilt on every
  rerun** — `streamlit_app.py:406-412`. Trivial cost, but the pattern is
  unusual; the natural Streamlit way is `st.multiselect` for channel
  picking.

- **`st.session_state["catalog_cache_bust"]` is bumped but never read
  back to verify the bump actually invalidated `_list_videos_cached`** —
  `streamlit_app.py:380-389`. Works (Streamlit hashes positional args),
  but a comment near the bump explaining the contract would help.

- **`label_visibility="collapsed"` on the surah multiselect** —
  `streamlit_app.py:302`. The `subheader("Surahs")` *is* the label, so
  this is fine. But the duplicate label (`"Surahs"` in `multiselect()`
  and again in the heading) is redundant; pick one.

- **`Refresh catalog` toggle help text contradicts behaviour** —
  `streamlit_app.py:362-366`. "Rescrape channels, then relist" suggests a
  one-shot, but the toggle stays ON. Fix when the toggle becomes a
  button.

- **`OverlayError` with a `details` arg gets formatted as plain `str(e)`** —
  `streamlit_app.py:391`. Users miss the actionable `details=` content
  defined in `quran_audio_source.py:101-104`. Show both
  message and details.

- **`_StreamlitLogBuffer.emit` slices `self.lines[:] = self.lines[-N:]` on
  every overflow** — `streamlit_app.py:118-121`. Fine. A `collections.deque(maxlen=N)`
  would be cleaner and avoid the slice.

- **`metadata.render_title` is called inside a try/except that catches
  `Exception`** — `streamlit_app.py:571-573`. `OverlayError` is the
  expected type; explicit `except OverlayError as exc: st.error(...)`
  followed by a fall-through `except Exception` is friendlier.

- **`st.set_page_config` must be the first Streamlit call** —
  `streamlit_app.py:673`. It is, but `_init_session_state()` is called
  immediately after, before any sidebar — leaving room for someone to
  later move `set_page_config` further down. Add a comment marking it
  load-bearing.

- **`_load_channels_cached` is called twice per rerun** —
  `streamlit_app.py:267, 680`. Once inside the sidebar, once at the top
  of `main`. Cache hit is cheap, but redundant.

- **`Path("output")` in `DEFAULT_OUTPUT_DIR` is unused** —
  `streamlit_app.py:64`. Dead constant.

## What's good (don't change)

- **Cache boundaries are right.** `_load_channels_cached`,
  `_list_videos_cached`, `_load_reciters_cached`, and
  `_ensure_thumbnail_cached` all sit on `@st.cache_data` with explicit
  cache-bust counters where invalidation is needed. This is the
  textbook Streamlit pattern and the team got it right.

- **Module-level imports tolerate a missing Streamlit install.** The
  `try/except ImportError` block at `streamlit_app.py:33-36` plus the
  `_cache_data` shim at `132-140` lets the smoke test (and `pytest`)
  import the module without `[app]` extras. Smart.

- **Lazy import of `overlay_pipeline.run_overlay_from_surah_numbers`
  inside the render handler** — `streamlit_app.py:526-534`. Lets the
  module import cleanly even if Agent C's contract isn't merged yet.
  Same trick for `upload_rendered`.

- **Validation feedback ("Select: at least one surah, …") is
  user-friendly** — `streamlit_app.py:697-707`. Better than disabling
  the button silently.

- **Session-state keys are initialized in one place** — `_init_session_state`
  at `230-237`. Easy to audit.

- **Atomic write pattern in `cartoon_catalog._write_cache`** —
  `cartoon_catalog.py:138-145`. Tempfile + `.replace()` keeps the on-disk
  cache consistent across crashes.

- **`download_surah` streams in 64 KB chunks with atomic rename** —
  `quran_audio_source.py:170-214`. Half-written files never satisfy the
  cache check. Same story as the catalog write.

- **`_resolve_visual_video` error message lists the first 10 available
  ids** — `overlay_pipeline.py:569-573`. Concrete and actionable when
  someone passes a stale id.

- **`OverlayMetadata` validation surfaces in the sidebar with a clear
  red badge** — `streamlit_app.py:250-254`. Catches template typos before
  the user spends 5 minutes rendering.

- **The 40-line ring buffer + `st.code` log panel is the right tool for
  the job.** Not perfect, but vastly better than no progress feedback at
  all during a multi-minute render.
