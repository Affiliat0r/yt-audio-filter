"""Job handlers — the bridge from a Studio job to the existing pipeline.

Every handler is a *consumer* of the public ``yt_audio_filter`` API; nothing
under ``src/`` is touched. Renders always run with ``upload=False``: the
Studio publishes only when the user explicitly presses "Upload to YouTube",
which comes back as a second job with ``uploadRequested`` set.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from yt_audio_filter import (
    cartoon_search,
    overlay_pipeline,
    source_probe,
    uploader,
    youtube,
)
from yt_audio_filter.cartoon_catalog import CatalogVideo
from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.logger import get_logger
from yt_audio_filter.metadata import OverlayMetadata, load_metadata

from . import blob
from .catalog_sync import cache_state_for, scan_cache_states
from .client import StudioClient
from .config import WorkerConfig
from .contract import (
    DEFAULT_METADATA_PATH,
    SEARCH_CHANNEL_SLUG,
    AyahJobInput,
    AyahRangeSpec,
    Job,
    JobResult,
    MusicRemovalJobInput,
    ProbeJobInput,
    RenderSettings,
    SearchJobInput,
    SourceQuality,
    SurahJobInput,
    catalog_video_to_json,
)
from .state import RenderedJobStore

logger = get_logger()

#: How often the render heartbeat re-posts the current stage. Long renders
#: are otherwise silent for tens of minutes, and the UI needs to know the
#: worker is alive — and the worker needs to learn about cancellations.
HEARTBEAT_SECONDS = 15.0


class JobCancelled(Exception):
    """The user cancelled the job; unwind and stop working on it."""


# ---------------------------------------------------------------------------
# Progress plumbing
# ---------------------------------------------------------------------------


class JobContext:
    """Progress reporting + cancellation for one job.

    ``report`` is fail-soft on transport errors — a dropped progress ping must
    never abort a 40-minute render — but a ``{"cancelled": true}`` response is
    always honoured by raising :class:`JobCancelled`.
    """

    def __init__(
        self,
        client: StudioClient,
        job_id: str,
        *,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        self.client = client
        self.job_id = job_id
        self.heartbeat_seconds = heartbeat_seconds
        self.cancelled = False
        self.stage_name = "Starting"
        self.percent: Optional[int] = None

    def report(
        self,
        stage: Optional[str] = None,
        percent: Optional[int] = None,
        log_lines: Optional[List[str]] = None,
    ) -> None:
        """Push a progress update, raising :class:`JobCancelled` if cancelled.

        The percentage is clamped to 0-100 *and* to never decrease: a
        music-removal job's blended bar already reaches 100 before the blob
        delivery stage reports, and a bar that walks backwards reads as a bug.
        """
        if stage is not None:
            self.stage_name = stage
        if percent is not None:
            bounded = max(0, min(100, int(percent)))
            self.percent = max(self.percent or 0, bounded)
        for line in log_lines or []:
            logger.info("[%s] %s", self.job_id, line)
        try:
            cancelled = self.client.progress(
                self.job_id, self.stage_name, self.percent, log_lines
            )
        except Exception as exc:  # noqa: BLE001 - progress is best-effort
            logger.warning("Progress update failed for %s: %s", self.job_id, exc)
            cancelled = False
        if cancelled:
            self.cancelled = True
            raise JobCancelled(f"Job {self.job_id} was cancelled")

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelled(f"Job {self.job_id} was cancelled")

    @contextmanager
    def stage(self, name: str, percent: Optional[int] = None) -> Iterator[None]:
        """Enter a long stage, pulsing a heartbeat until the block exits.

        Cancellation detected by the heartbeat thread is raised on exit — the
        FFmpeg/Demucs subprocess underneath cannot be interrupted mid-flight,
        so "promptly" means "at the next stage boundary".
        """
        self.report(name, percent)
        stop = threading.Event()
        thread: Optional[threading.Thread] = None
        if self.heartbeat_seconds > 0:
            thread = threading.Thread(
                target=self._pulse, args=(stop,), name=f"hb-{self.job_id}", daemon=True
            )
            thread.start()
        try:
            yield
        finally:
            stop.set()
            if thread is not None:
                thread.join(timeout=2.0)
        self.raise_if_cancelled()

    def _pulse(self, stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_seconds):
            try:
                if self.client.progress(self.job_id, self.stage_name, self.percent):
                    self.cancelled = True
                    return
            except Exception as exc:  # noqa: BLE001 - heartbeat is best-effort
                logger.debug("Heartbeat failed for %s: %s", self.job_id, exc)


def classify_music_removal_stage(stage: str) -> str:
    """Bucket a ``pipeline.process_video`` stage name.

    Same routing the Streamlit app used, so the two UIs agree on what counts
    as which phase: chunked-mode stages share buckets with their non-chunked
    equivalents (split == extract, process-chunks == Demucs, concatenate ==
    remux).
    """
    text = (stage or "").strip().lower()
    if not text:
        return "other"
    if "extract" in text or "split" in text:
        return "extract"
    if (
        "isolat" in text
        or "demucs" in text
        or "vocal" in text
        or "process chunks" in text
    ):
        return "demucs"
    if "remux" in text or "concatenate" in text:
        return "remux"
    return "other"


#: Per-phase share of the overall bar: extract 0-10, demucs 10-90, remux 90-100.
_PHASE_WINDOWS: Dict[str, Tuple[int, int]] = {
    "extract": (0, 10),
    "demucs": (10, 90),
    "remux": (90, 100),
}

_PHASE_LABELS: Dict[str, str] = {
    "extract": "Extracting audio",
    "demucs": "Isolating vocals (Demucs)",
    "remux": "Remuxing video",
}


class MusicRemovalProgress:
    """Blend three pipeline phases into one monotonic 0-100 percentage."""

    def __init__(self) -> None:
        self.percent = 0
        self.bucket: Optional[str] = None

    def update(self, stage: str, percent: object) -> Optional[Tuple[str, int]]:
        """Return ``(label, overall_percent)`` when the bar should move.

        ``None`` means "nothing worth reporting" — an unclassifiable stage, a
        non-numeric percent, or no forward movement since the last update.
        That throttling keeps a 100-step Demucs run to ~100 HTTP calls.
        """
        bucket = classify_music_removal_stage(stage)
        if bucket == "other":
            return None
        try:
            local = max(0, min(100, int(percent)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

        start, end = _PHASE_WINDOWS[bucket]
        overall = start + round(local * (end - start) / 100)
        overall = max(self.percent, min(100, overall))
        if overall == self.percent and bucket == self.bucket:
            return None
        self.percent = overall
        self.bucket = bucket
        return _PHASE_LABELS[bucket], overall


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def resolve_metadata(settings: RenderSettings) -> OverlayMetadata:
    """Load the metadata template referenced by a job's settings."""
    path = Path(settings.metadata_path or DEFAULT_METADATA_PATH)
    if not path.exists():
        raise OverlayError(
            f"Metadata file not found: {path}",
            "The Studio's metadataPath is resolved relative to the worker's "
            "working directory. Run the worker from the repository root, or "
            f"point settings.metadataPath at an existing file (default: "
            f"{DEFAULT_METADATA_PATH}).",
        )
    return load_metadata(path)


def ensure_visual_resolvable(visual: CatalogVideo, cache_dir: Path) -> None:
    """Make a keyword-search pick visible to ``_resolve_visual_video``.

    Search results are not backed by a configured channel, so the pipeline's
    catalog lookup would not find them. Persisting the pick under the
    ``__search__`` slug is exactly what the Streamlit gallery did.
    """
    if visual.channel_slug == SEARCH_CHANNEL_SLUG:
        cartoon_search.add_pick_to_catalog(visual, cache_dir=Path(cache_dir))


def preset_resolution(preset_slug: str) -> Optional[Tuple[int, int]]:
    """Resolution for a render preset slug; ``None`` to keep pipeline defaults."""
    if not preset_slug:
        return None
    from yt_audio_filter.render_presets import get_preset

    return get_preset(preset_slug).resolution


def surahs_from_ranges(ranges: List[AyahRangeSpec]) -> List[int]:
    """Surah numbers behind a set of ayah ranges, consecutive dupes collapsed.

    Two ranges in the same surah would otherwise title the upload
    "Al-Fatiha + Al-Fatiha".
    """
    numbers: List[int] = []
    for spec in ranges:
        if not numbers or numbers[-1] != spec.surah:
            numbers.append(spec.surah)
    return numbers


def deliver_render(
    job: Job,
    ctx: JobContext,
    cfg: WorkerConfig,
    store: RenderedJobStore,
    output_path: Path,
    *,
    video_metadata: Optional[youtube.VideoMetadata] = None,
) -> JobResult:
    """Record the local file, then best-effort publish it to Vercel Blob.

    A missing token or a failed upload is **not** a job failure: the render
    is on disk and the log line says where. Only the browser preview is lost.
    """
    output_path = Path(output_path)
    store.record(job.id, output_path, job.kind, video_metadata)

    size = output_path.stat().st_size if output_path.exists() else 0
    result = JobResult(file_name=output_path.name, size_bytes=size)

    if not cfg.blob_enabled:
        ctx.report(
            "Rendered (preview unavailable)",
            98,
            [
                "BLOB_READ_WRITE_TOKEN is not set, so no browser preview was "
                f"uploaded. The rendered file is on the worker at {output_path}",
            ],
        )
        return result

    ctx.report("Uploading preview to Vercel Blob", 92)
    try:
        upload = blob.upload_file(output_path, cfg.blob_token or "")
    except Exception as exc:  # noqa: BLE001 - preview is optional
        logger.warning("Blob upload failed for job %s: %s", job.id, exc)
        ctx.report(
            "Rendered (preview upload failed)",
            98,
            [
                f"Vercel Blob upload failed: {exc}",
                f"The rendered file is on the worker at {output_path}",
            ],
        )
        return result

    ctx.report("Preview ready", 99, [f"Preview uploaded: {upload.url}"])
    return dataclasses.replace(
        result, blob_url=upload.url, blob_pathname=upload.pathname
    )


# ---------------------------------------------------------------------------
# Render handlers
# ---------------------------------------------------------------------------


def handle_surah(
    job: Job, ctx: JobContext, cfg: WorkerConfig, store: RenderedJobStore
) -> JobResult:
    """``kind: "surah"`` — full-surah overlay via the surah-numbers backend."""
    job_input = job.input
    assert isinstance(job_input, SurahJobInput)
    settings = job_input.settings

    metadata = resolve_metadata(settings)
    ensure_visual_resolvable(job_input.visual, cfg.cache_dir)

    ctx.report(
        "Preparing render",
        2,
        [
            f"{len(job_input.surah_numbers)} surah segment(s), reciter "
            f"{job_input.reciter_slug}, visual {job_input.visual.video_id}"
        ],
    )

    with ctx.stage("Rendering overlay", 10):
        overlay_result = overlay_pipeline.run_overlay_from_surah_numbers(
            surah_numbers=list(job_input.surah_numbers),
            reciter_slug=job_input.reciter_slug,
            visual_video_id=job_input.visual.video_id,
            metadata=metadata,
            output_path=None,
            cache_dir=Path(cfg.cache_dir),
            resolution=preset_resolution(settings.preset_slug),
            upscale=settings.upscale,
            upload=False,
        )

    return deliver_render(job, ctx, cfg, store, overlay_result.output_path)


def handle_ayah(
    job: Job, ctx: JobContext, cfg: WorkerConfig, store: RenderedJobStore
) -> JobResult:
    """``kind: "ayah"`` — ayah-range repetition render for memorisation."""
    job_input = job.input
    assert isinstance(job_input, AyahJobInput)
    settings = job_input.settings

    metadata = resolve_metadata(settings)
    ensure_visual_resolvable(job_input.visual, cfg.cache_dir)
    ranges = [spec.to_ayah_range() for spec in job_input.ranges]

    ctx.report(
        "Preparing render",
        2,
        [
            f"{len(ranges)} ayah range(s), reciter {job_input.reciter_slug}, "
            f"visual {job_input.visual.video_id}"
        ],
    )

    with ctx.stage("Rendering ayah drill", 10):
        overlay_result = overlay_pipeline.run_overlay_from_ayah_ranges(
            ranges=ranges,
            reciter_slug=job_input.reciter_slug,
            visual_video_id=job_input.visual.video_id,
            metadata=metadata,
            output_path=None,
            cache_dir=Path(cfg.cache_dir),
            upscale=settings.upscale,
            upload=False,
            playlist_id=settings.playlist_id,
            preset_slug=settings.preset_slug or None,
            burn_subtitles=settings.burn_subtitles,
        )

    return deliver_render(job, ctx, cfg, store, overlay_result.output_path)


def handle_music_removal(
    job: Job, ctx: JobContext, cfg: WorkerConfig, store: RenderedJobStore
) -> JobResult:
    """``kind: "music_removal"`` — download, then strip music with Demucs."""
    job_input = job.input
    assert isinstance(job_input, MusicRemovalJobInput)
    visual = job_input.visual
    url = visual.url or f"https://www.youtube.com/watch?v={visual.video_id}"

    # Checked FIRST. This import chain pulls in PyTorch, which a light worker
    # does not have — and discovering that after downloading a 300 MB video
    # wastes the download and the user's time for a failure we could see
    # immediately. See yt_audio_filter/__init__.py for why it is lazy at all.
    try:
        from yt_audio_filter import pipeline
    except ImportError as exc:
        raise OverlayError(
            "This worker cannot remove background music.",
            f"Demucs and PyTorch are not installed on {cfg.worker_id}. "
            f'Install them with: pip install -e ".[music]" (~5 GB), or pick a '
            f"machine that already has them under 'Run the work on'. "
            f"Underlying error: {exc}",
        ) from exc

    # No percentage until the pipeline starts reporting: the blended bar below
    # begins at 0, and a bar that jumps backwards reads as a bug.
    ctx.report("Preparing download", None, [f"Source: {url}"])
    with ctx.stage("Downloading source video"):
        video_metadata = youtube.download_video_with_metadata(
            url=url,
            output_dir=Path(cfg.cache_dir),
            use_cache=True,
        )

    video_id = video_metadata.video_id or visual.video_id
    output_dir = Path(cfg.cache_dir) / "music_removed"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"music_removed_{video_id}.mp4"

    tracker = MusicRemovalProgress()

    def on_progress(stage: str, percent: int) -> None:
        moved = tracker.update(stage, percent)
        if moved is None:
            return
        label, overall = moved
        # ctx.report raises JobCancelled, which unwinds process_video for us.
        ctx.report(label, overall)

    with ctx.stage("Removing background music"):
        pipeline.process_video(
            input_path=video_metadata.file_path,
            output_path=output_path,
            progress_callback=on_progress,
        )

    return deliver_render(
        job, ctx, cfg, store, output_path, video_metadata=video_metadata
    )


def handle_search(
    job: Job, ctx: JobContext, cfg: WorkerConfig, store: RenderedJobStore
) -> JobResult:
    """``kind: "search"`` — live YouTube keyword search for the gallery."""
    job_input = job.input
    assert isinstance(job_input, SearchJobInput)

    ctx.report(f"Searching YouTube for {job_input.query!r}", 20)
    videos = cartoon_search.search_videos(
        query=job_input.query,
        max_results=job_input.max_results,
        cache_dir=Path(cfg.cache_dir),
    )

    states = scan_cache_states(Path(cfg.cache_dir))
    payload = [
        catalog_video_to_json(video, cache_state_for(video.video_id, states))
        for video in videos
    ]
    ctx.report("Search complete", 100, [f"{len(payload)} result(s)"])
    return JobResult(search_results=payload)


def handle_probe(
    job: Job, ctx: JobContext, cfg: WorkerConfig, store: RenderedJobStore
) -> JobResult:
    """``kind: "probe"`` — measure or estimate the source visual's quality.

    Cache state is per machine, so this only answers for the disk it runs on;
    the Studio targets probes at the worker whose cache the user is looking at.
    """
    job_input = job.input
    assert isinstance(job_input, ProbeJobInput)
    visual = job_input.visual
    url = visual.url or f"https://www.youtube.com/watch?v={visual.video_id}"

    ctx.report(f"Probing source quality for {visual.video_id}", 20)
    info = source_probe.probe_source_quality(
        video_id=visual.video_id, url=url, cache_dir=Path(cfg.cache_dir)
    )
    ctx.report("Probe complete", 100, [f"{info.kind}: {info.width}x{info.height}"])
    return JobResult(
        source_quality=SourceQuality(
            kind=info.kind,
            width=info.width,
            height=info.height,
            fps=info.fps,
            codec=info.codec,
            probed_at=int(time.time() * 1000),
        )
    )


# ---------------------------------------------------------------------------
# Upload handler (explicit, user-initiated, never automatic)
# ---------------------------------------------------------------------------


def handle_upload(
    job: Job, ctx: JobContext, cfg: WorkerConfig, store: RenderedJobStore
) -> JobResult:
    """Publish an already-rendered job to YouTube — no re-render.

    The rendered path comes from the sidecar store, so this survives a worker
    restart between the render and the user pressing Upload.
    """
    record = store.lookup(job.id)
    if record is None or not Path(record.path).exists():
        raise OverlayError(
            "No local rendered file found for this job",
            "The worker's sidecar state has no usable path for this job id "
            f"({job.id}). Re-run the render, then upload again.",
        )

    existing = job.result or JobResult()
    rendered_path = Path(record.path)
    job_input = job.input

    ctx.report("Preparing upload", 10, [f"Uploading {rendered_path.name}"])

    if isinstance(job_input, MusicRemovalJobInput):
        with ctx.stage("Uploading to YouTube", 20):
            video_id = uploader.upload_to_youtube(
                video_path=rendered_path,
                original_metadata=record.video_metadata,
                privacy=job_input.privacy,
                playlist_id=job_input.playlist_id,
            )
    elif isinstance(job_input, (SurahJobInput, AyahJobInput)):
        metadata = resolve_metadata(job_input.settings)
        if isinstance(job_input, SurahJobInput):
            surah_numbers = list(job_input.surah_numbers)
        else:
            surah_numbers = surahs_from_ranges(job_input.ranges)
        with ctx.stage("Uploading to YouTube", 20):
            video_id = overlay_pipeline.upload_rendered(
                rendered_path,
                metadata,
                surah_numbers=surah_numbers,
                reciter_slug=job_input.reciter_slug,
                visual_title=job_input.visual.title,
                playlist_id=job_input.settings.playlist_id,
            )
    else:
        raise OverlayError(
            f"Job kind {job.kind!r} has nothing to upload",
            "Only surah, ayah, and music_removal jobs produce a video file.",
        )

    ctx.report(
        "Uploaded",
        99,
        [f"https://www.youtube.com/watch?v={video_id}"],
    )

    # The preview blob existed so the video could be watched before publishing.
    # It is now on YouTube, so the copy is redundant — and Vercel Blob's free
    # tier meters API operations, which every subsequent read of it spends.
    # Best-effort: a failure here must not fail an upload that already worked.
    result = existing.merged_with(JobResult(youtube_video_id=video_id))
    if existing.blob_url and cfg.blob_token:
        if blob.delete_blob(existing.blob_url, cfg.blob_token):
            ctx.report("Uploaded", 100, ["Preview copy removed from Blob storage"])
            result = JobResult(
                youtube_video_id=video_id,
                file_name=existing.file_name,
                size_bytes=existing.size_bytes,
                search_results=existing.search_results,
            )
    return result


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

Handler = Callable[[Job, JobContext, WorkerConfig, RenderedJobStore], JobResult]

HANDLERS: Dict[str, Handler] = {
    "surah": handle_surah,
    "ayah": handle_ayah,
    "music_removal": handle_music_removal,
    "search": handle_search,
    "probe": handle_probe,
}


def run_job(
    job: Job, ctx: JobContext, cfg: WorkerConfig, store: RenderedJobStore
) -> JobResult:
    """Route a claimed job to its handler and return the result payload."""
    if job.upload_requested:
        return handle_upload(job, ctx, cfg, store)
    handler = HANDLERS.get(job.kind)
    if handler is None:
        raise OverlayError(
            f"Unknown job kind: {job.kind!r}",
            f"Supported kinds: {', '.join(sorted(HANDLERS))}",
        )
    return handler(job, ctx, cfg, store)
