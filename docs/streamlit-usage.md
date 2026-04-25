# Streamlit UI — Setup & Usage

The Streamlit app is a local, single-page UI around the same overlay
pipeline the CLI uses. It lets you pick surahs, pick a reciter (with an
inline audio sample), pick a cartoon visual by clicking its thumbnail,
render, preview, and optionally upload — without touching the shell.

## Install

The app is gated behind the `[app]` extra so non-UI installs stay lean:

```bash
pip install -e ".[app]"
```

This pulls in `streamlit>=1.30` and `pillow>=10.0`. Everything else
(FFmpeg, Demucs, yt-dlp, pytubefix, upscale, upload dependencies) is
handled by the base install and the `[upload]` extra.

## Run

From the repo root:

```bash
streamlit run src/yt_audio_filter/streamlit_app.py
```

Streamlit will open `http://localhost:8501` in your default browser.
The process is single-user / single-session; there's no auth — the app
assumes it's behind your OS.

## Walkthrough

![screenshot](screenshots/streamlit.png)

1. **Sidebar — metadata JSON.** Defaults to
   `examples/metadata-surah-arrahman.json`. The app parses it on every
   rerun and shows a green/red badge so you know the template is valid
   before you render.
2. **Sidebar — upscale toggle.** Off by default. First run downloads the
   Real-ESRGAN weights (~65 MB) and is slow.
3. **Surahs.** A multiselect over all 114 surahs. Type to filter, use
   arrow keys to navigate, click to add. Selection order is preserved —
   the backend concatenates audios in the order you pick.
4. **Reciter.** Selectbox over the ~20 verified reciters in
   `src/yt_audio_filter/data/reciters.json`. The small audio widget
   plays the Al-Fatiha sample for the chosen reciter so you can hear
   them before committing.
5. **Cartoon video.** A thumbnail gallery grouped by channel. Exactly
   one tile should have its "Select" checkbox ticked. Hit the
   *Refresh catalog (rescrape channels)* toggle to drop
   `cache/cartoon_catalog.json` and rebuild — useful when a channel
   uploads new content.
6. **Render.** Runs the three-stage pipeline inside an `st.status`
   block; the log panel streams the live output (last 40 lines).
   When done, the rendered MP4 is previewed inline via `st.video` and
   a **Download MP4** button serves the bytes.
7. **Upload to YouTube.** Appears only after a successful render. The
   existing `--upload` behavior from the CLI (OAuth via
   `[upload]` extra, explicit title/description from the metadata
   template) is invoked, and the app shows a clickable link to the
   resulting video.

## Upscale & upload — how the UI differs from the CLI

The UI defers to the backend for both:

* `--upscale` on the CLI corresponds to the sidebar toggle; same
  Real-ESRGAN path, same cache in `cache/upscaled/`.
* The CLI's `--upload` flag is always driven by a separate button in
  the UI. The app renders first and lets you preview the MP4 *before*
  you publish.

## Known caveats

* **Salim Bahanan.** `data/reciters.json` does not carry Salim Bahanan
  directly — quranicaudio.com doesn't mirror him. We substituted
  *Abdullah Awad al-Juhani* in the same slot. If you specifically need
  Salim Bahanan for a render, use the CLI's surah-name mode with direct
  YouTube URLs (see the main CLAUDE.md / README for the
  `yt-quran-overlay --surah ...` invocation).
* **Output lifetime.** Renders go to a `tempfile.NamedTemporaryFile` —
  the file persists until the Streamlit process exits, which is long
  enough for the inline preview and the download button. Use the
  **Download MP4** button to save a copy you want to keep.
* **First render is slow.** The cartoon catalog is scraped on first
  use; the app's `st.cache_data` keeps it warm for subsequent reruns.
  The "Refresh catalog" toggle invalidates both the Streamlit cache and
  the on-disk JSON.
