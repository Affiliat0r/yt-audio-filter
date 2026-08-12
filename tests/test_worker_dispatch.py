"""Unit tests for worker job dispatch.

Each job kind must reach the right pipeline function with the right keyword
arguments — above all ``upload=False``, because publishing to YouTube is only
ever a separate, explicitly user-requested job.

Everything below is mocked: no ``requests``, no FFmpeg, no Demucs, no network.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple
from unittest.mock import patch

import pytest

from worker.blob import BlobUpload, BlobUploadError
from worker.config import WorkerConfig
from worker.contract import Job, JobResult
from worker.handlers import (
    JobCancelled,
    JobContext,
    MusicRemovalProgress,
    classify_music_removal_stage,
    run_job,
    surahs_from_ranges,
)
from worker.state import RenderedJobStore
from worker.worker import execute_job
from yt_audio_filter.cartoon_catalog import CatalogVideo
from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.overlay_pipeline import OverlayResult
from yt_audio_filter.youtube import VideoMetadata


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeClient:
    """Records worker->Studio calls; can pretend the user hit Cancel."""

    def __init__(self, cancel_on_call: Optional[int] = None) -> None:
        self.cancel_on_call = cancel_on_call
        self.progress_calls: List[Tuple[str, Optional[int], List[str]]] = []
        self.complete_calls: List[dict] = []

    def progress(
        self,
        job_id: str,
        stage: str,
        percent: Optional[int] = None,
        log_lines: Optional[List[str]] = None,
    ) -> bool:
        self.progress_calls.append((stage, percent, list(log_lines or [])))
        if self.cancel_on_call is None:
            return False
        return len(self.progress_calls) >= self.cancel_on_call

    def complete(
        self,
        job_id: str,
        ok: bool,
        result: Optional[JobResult] = None,
        error: Optional[str] = None,
        error_details: Optional[str] = None,
    ) -> None:
        self.complete_calls.append(
            {
                "jobId": job_id,
                "ok": ok,
                "result": result.to_json() if result is not None else None,
                "error": error,
                "errorDetails": error_details,
            }
        )

    @property
    def percents(self) -> List[Optional[int]]:
        return [percent for _, percent, _ in self.progress_calls]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def metadata_file(tmp_path: Path) -> Path:
    path = tmp_path / "metadata.json"
    path.write_text(
        json.dumps(
            {
                "title": "$detected_surah - $reciter",
                "description_template": "Surahs: $detected_surah",
                "tags": ["quran"],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def cfg(tmp_path: Path) -> WorkerConfig:
    return WorkerConfig(
        base_url="https://studio.test",
        token="tok",
        blob_token=None,
        poll_seconds=0.01,
        cache_dir=tmp_path / "cache",
    )


@pytest.fixture()
def store(tmp_path: Path) -> RenderedJobStore:
    return RenderedJobStore(tmp_path / "state" / "rendered_jobs.json")


@pytest.fixture()
def rendered(tmp_path: Path) -> Path:
    path = tmp_path / "rendered.mp4"
    path.write_bytes(b"0" * 2048)
    return path


def _ctx(client: FakeClient, job_id: str = "job-1") -> JobContext:
    # heartbeat_seconds=0 keeps the tests single-threaded and deterministic.
    return JobContext(client, job_id, heartbeat_seconds=0)


def _visual(channel_slug: str = "toyfactory") -> dict:
    return {
        "videoId": "abc123",
        "url": "https://www.youtube.com/watch?v=abc123",
        "title": "Fun Cartoon",
        "duration": 900,
        "viewCount": 10,
        "uploadDate": "20260101",
        "thumbnailUrl": "https://i.ytimg.com/vi/abc123/hqdefault.jpg",
        "channelSlug": channel_slug,
    }


def _job(
    kind: str,
    job_input: dict,
    *,
    job_id: str = "job-1",
    upload_requested: bool = False,
    result: Optional[dict] = None,
) -> Job:
    return Job.from_json(
        {
            "id": job_id,
            "kind": kind,
            "status": "claimed",
            "input": job_input,
            "result": result,
            "uploadRequested": upload_requested,
        }
    )


def _surah_job(metadata_file: Path, *, channel_slug: str = "toyfactory", **overrides) -> Job:
    job_input = {
        "kind": "surah",
        "surahNumbers": [1, 112],
        "reciterSlug": "alafasy",
        "visual": _visual(channel_slug),
        "settings": {
            "presetSlug": "",
            "burnSubtitles": False,
            "upscale": True,
            "metadataPath": str(metadata_file),
            "playlistId": "PL1",
        },
    }
    return _job("surah", job_input, **overrides)


def _ayah_job(metadata_file: Path, **overrides) -> Job:
    job_input = {
        "kind": "ayah",
        "ranges": [{"surah": 1, "start": 1, "end": 7, "repeats": 3, "gapSeconds": 2.0}],
        "reciterSlug": "husary",
        "visual": _visual(),
        "settings": {
            "presetSlug": "youtube_landscape",
            "burnSubtitles": True,
            "upscale": False,
            "metadataPath": str(metadata_file),
            "playlistId": "PL9",
        },
    }
    return _job("ayah", job_input, **overrides)


def _music_job(**overrides) -> Job:
    job_input = {
        "kind": "music_removal",
        "visual": _visual(),
        "privacy": "private",
        "playlistId": "PLmusic",
    }
    return _job("music_removal", job_input, **overrides)


# ---------------------------------------------------------------------------
# surah dispatch
# ---------------------------------------------------------------------------


def test_surah_job_calls_the_surah_numbers_backend_with_upload_false(
    cfg, store, metadata_file, rendered
):
    client = FakeClient()
    job = _surah_job(metadata_file)

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ) as mock_render:
        result = run_job(job, _ctx(client), cfg, store)

    kwargs = mock_render.call_args.kwargs
    assert kwargs["surah_numbers"] == [1, 112]
    assert kwargs["reciter_slug"] == "alafasy"
    assert kwargs["visual_video_id"] == "abc123"
    assert kwargs["output_path"] is None
    assert kwargs["cache_dir"] == cfg.cache_dir
    assert kwargs["upscale"] is True
    assert kwargs["upload"] is False
    assert result.file_name == "rendered.mp4"
    assert result.size_bytes == 2048


def test_surah_job_translates_the_preset_slug_into_a_resolution(
    cfg, store, metadata_file, rendered
):
    client = FakeClient()
    base = _surah_job(metadata_file)
    job = Job.from_json(
        {
            **base.to_json(),
            "input": {
                **base.input.to_json(),
                "settings": {
                    **base.input.settings.to_json(),
                    "presetSlug": "instagram_square",
                },
            },
        }
    )

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ) as mock_render:
        run_job(job, _ctx(client), cfg, store)

    assert mock_render.call_args.kwargs["resolution"] == (1080, 1080)


def test_surah_job_without_a_preset_leaves_the_resolution_to_the_pipeline(
    cfg, store, metadata_file, rendered
):
    client = FakeClient()

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ) as mock_render:
        run_job(_surah_job(metadata_file), _ctx(client), cfg, store)

    assert mock_render.call_args.kwargs["resolution"] is None


def test_search_pick_is_persisted_into_the_catalog_before_rendering(
    cfg, store, metadata_file, rendered
):
    client = FakeClient()
    job = _surah_job(metadata_file, channel_slug="__search__")

    with patch("yt_audio_filter.cartoon_search.add_pick_to_catalog") as mock_add, patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ):
        run_job(job, _ctx(client), cfg, store)

    video = mock_add.call_args.args[0]
    assert isinstance(video, CatalogVideo)
    assert video.video_id == "abc123"
    assert mock_add.call_args.kwargs["cache_dir"] == cfg.cache_dir


def test_catalog_pick_is_not_re_added(cfg, store, metadata_file, rendered):
    client = FakeClient()

    with patch("yt_audio_filter.cartoon_search.add_pick_to_catalog") as mock_add, patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ):
        run_job(_surah_job(metadata_file), _ctx(client), cfg, store)

    mock_add.assert_not_called()


# ---------------------------------------------------------------------------
# ayah dispatch
# ---------------------------------------------------------------------------


def test_ayah_job_builds_ayah_ranges_and_forwards_render_settings(
    cfg, store, metadata_file, rendered
):
    client = FakeClient()

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_ayah_ranges",
        return_value=OverlayResult(output_path=rendered),
    ) as mock_render:
        run_job(_ayah_job(metadata_file), _ctx(client), cfg, store)

    kwargs = mock_render.call_args.kwargs
    ranges = kwargs["ranges"]
    assert [(r.surah, r.start, r.end, r.repeats, r.gap_seconds) for r in ranges] == [
        (1, 1, 7, 3, 2.0)
    ]
    assert kwargs["reciter_slug"] == "husary"
    assert kwargs["visual_video_id"] == "abc123"
    assert kwargs["preset_slug"] == "youtube_landscape"
    assert kwargs["burn_subtitles"] is True
    assert kwargs["playlist_id"] == "PL9"
    assert kwargs["upscale"] is False
    assert kwargs["upload"] is False


# ---------------------------------------------------------------------------
# music removal dispatch
# ---------------------------------------------------------------------------


def _video_metadata(path: Path, video_id: str = "abc123") -> VideoMetadata:
    return VideoMetadata(
        video_id=video_id,
        title="Original Title",
        description="desc",
        channel="Some Channel",
        tags=["a"],
        duration=120,
        view_count=5,
        file_path=path,
    )


def test_music_removal_downloads_then_processes_to_the_cache_path(cfg, store, tmp_path):
    client = FakeClient()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x")

    with patch(
        "yt_audio_filter.youtube.download_video_with_metadata",
        return_value=_video_metadata(source),
    ) as mock_download, patch("yt_audio_filter.pipeline.process_video") as mock_process:
        # process_video writes its output; emulate that so size_bytes is real.
        def _write_output(**kwargs):
            Path(kwargs["output_path"]).write_bytes(b"y" * 10)
            return kwargs["output_path"]

        mock_process.side_effect = _write_output
        result = run_job(_music_job(), _ctx(client), cfg, store)

    dl_kwargs = mock_download.call_args.kwargs
    assert dl_kwargs["url"] == "https://www.youtube.com/watch?v=abc123"
    assert dl_kwargs["output_dir"] == cfg.cache_dir
    assert dl_kwargs["use_cache"] is True

    proc_kwargs = mock_process.call_args.kwargs
    assert proc_kwargs["input_path"] == source
    assert proc_kwargs["output_path"] == (
        cfg.cache_dir / "music_removed" / "music_removed_abc123.mp4"
    )
    assert callable(proc_kwargs["progress_callback"])
    assert result.file_name == "music_removed_abc123.mp4"
    assert result.size_bytes == 10


def test_music_removal_records_source_metadata_for_the_later_upload(cfg, store, tmp_path):
    client = FakeClient()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x")

    with patch(
        "yt_audio_filter.youtube.download_video_with_metadata",
        return_value=_video_metadata(source),
    ), patch("yt_audio_filter.pipeline.process_video"):
        run_job(_music_job(), _ctx(client), cfg, store)

    record = store.lookup("job-1")
    assert record is not None
    assert record.video_metadata is not None
    assert record.video_metadata.title == "Original Title"
    assert record.video_metadata.tags == ["a"]


# ---------------------------------------------------------------------------
# music removal progress blending
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stage,bucket",
    [
        ("Extract Audio", "extract"),
        ("Split video into chunks", "extract"),
        ("Isolate Vocals", "demucs"),
        ("Running Demucs", "demucs"),
        ("Process chunks", "demucs"),
        ("Remux Video", "remux"),
        ("Concatenate chunks", "remux"),
        ("Something else", "other"),
        ("", "other"),
    ],
)
def test_stage_classification_matches_the_streamlit_buckets(stage, bucket):
    assert classify_music_removal_stage(stage) == bucket


def test_progress_blending_is_monotonic_and_spans_zero_to_one_hundred():
    tracker = MusicRemovalProgress()
    updates = [
        ("Extract Audio", 0),
        ("Extract Audio", 50),
        ("Extract Audio", 100),
        ("Isolate Vocals", 0),
        ("Isolate Vocals", 25),
        ("Isolate Vocals", 50),
        ("Isolate Vocals", 100),
        ("Remux Video", 0),
        ("Remux Video", 50),
        ("Remux Video", 100),
    ]

    percents = [moved[1] for moved in (tracker.update(s, p) for s, p in updates) if moved]

    assert percents == sorted(percents)
    assert percents[0] == 0
    assert percents[-1] == 100
    assert all(0 <= p <= 100 for p in percents)


def test_progress_blending_keeps_each_phase_in_its_window():
    tracker = MusicRemovalProgress()

    assert tracker.update("Extract Audio", 100) == ("Extracting audio", 10)
    assert tracker.update("Isolate Vocals", 50) == ("Isolating vocals (Demucs)", 50)
    assert tracker.update("Isolate Vocals", 100) == ("Isolating vocals (Demucs)", 90)
    assert tracker.update("Remux Video", 100) == ("Remuxing video", 100)


def test_progress_blending_never_moves_backwards_on_a_phase_restart():
    tracker = MusicRemovalProgress()
    tracker.update("Isolate Vocals", 80)

    # A chunked run restarts the Demucs percentage for every chunk.
    moved = tracker.update("Isolate Vocals", 5)
    assert moved is None
    assert tracker.percent == 74


def test_progress_blending_ignores_unclassifiable_and_non_numeric_updates():
    tracker = MusicRemovalProgress()

    assert tracker.update("Loading model", 50) is None
    assert tracker.update("Extract Audio", "half") is None


def test_music_removal_progress_reaches_the_studio_monotonically(cfg, store, tmp_path):
    client = FakeClient()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x")

    def fake_process(**kwargs):
        callback = kwargs["progress_callback"]
        for stage, percent in [
            ("Extract Audio", 0),
            ("Extract Audio", 100),
            ("Isolate Vocals", 50),
            ("Isolate Vocals", 100),
            ("Remux Video", 100),
        ]:
            callback(stage, percent)
        return kwargs["output_path"]

    with patch(
        "yt_audio_filter.youtube.download_video_with_metadata",
        return_value=_video_metadata(source),
    ), patch("yt_audio_filter.pipeline.process_video", side_effect=fake_process):
        run_job(_music_job(), _ctx(client), cfg, store)

    blended = [p for stage, p, _ in client.progress_calls if p is not None]
    assert blended == sorted(blended)
    assert 0 in blended and 10 in blended and 50 in blended and 90 in blended


# ---------------------------------------------------------------------------
# search dispatch
# ---------------------------------------------------------------------------


def test_search_job_returns_camel_case_catalog_videos(cfg, store):
    client = FakeClient()
    job = _job("search", {"kind": "search", "query": "toy factory", "maxResults": 5})
    hit = CatalogVideo(
        video_id="vid1",
        url="https://www.youtube.com/watch?v=vid1",
        title="A Cartoon",
        duration=610,
        view_count=99,
        upload_date="20260201",
        thumbnail_url="https://i.ytimg.com/vi/vid1/hqdefault.jpg",
        channel_slug="__search__",
    )

    with patch(
        "yt_audio_filter.cartoon_search.search_videos", return_value=[hit]
    ) as mock_search:
        result = run_job(job, _ctx(client), cfg, store)

    kwargs = mock_search.call_args.kwargs
    assert kwargs["query"] == "toy factory"
    assert kwargs["max_results"] == 5
    assert kwargs["cache_dir"] == cfg.cache_dir

    assert result.search_results is not None
    entry = result.search_results[0]
    assert entry["videoId"] == "vid1"
    assert entry["viewCount"] == 99
    assert entry["channelSlug"] == "__search__"
    assert entry["cacheState"] == "new"


def test_search_job_reports_the_cache_state_of_known_visuals(cfg, store):
    client = FakeClient()
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    (cfg.cache_dir / "upscaled_vid1.mp4").write_bytes(b"x")
    (cfg.cache_dir / "video_vid2.mp4").write_bytes(b"x")

    def _video(video_id: str) -> CatalogVideo:
        return CatalogVideo(
            video_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            title=video_id,
            duration=10,
            view_count=0,
            upload_date="",
            thumbnail_url="",
            channel_slug="__search__",
        )

    job = _job("search", {"kind": "search", "query": "q"})
    with patch(
        "yt_audio_filter.cartoon_search.search_videos",
        return_value=[_video("vid1"), _video("vid2"), _video("vid3")],
    ):
        result = run_job(job, _ctx(client), cfg, store)

    states = {v["videoId"]: v["cacheState"] for v in result.search_results or []}
    assert states == {"vid1": "upscaled", "vid2": "downloaded", "vid3": "new"}


# ---------------------------------------------------------------------------
# upload dispatch (explicit, second pass over an already-rendered job)
# ---------------------------------------------------------------------------


def test_upload_request_uploads_the_existing_file_without_re_rendering(
    cfg, store, metadata_file, rendered
):
    client = FakeClient()
    store.record("job-1", rendered, "surah")
    job = _surah_job(
        metadata_file,
        upload_requested=True,
        result={"blobUrl": "https://blob/x.mp4", "fileName": "x.mp4", "sizeBytes": 5},
    )

    with patch(
        "yt_audio_filter.overlay_pipeline.upload_rendered", return_value="yt-42"
    ) as mock_upload, patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers"
    ) as mock_render:
        result = run_job(job, _ctx(client), cfg, store)

    mock_render.assert_not_called()
    kwargs = mock_upload.call_args.kwargs
    assert mock_upload.call_args.args[0] == rendered
    assert kwargs["surah_numbers"] == [1, 112]
    assert kwargs["reciter_slug"] == "alafasy"
    assert kwargs["visual_title"] == "Fun Cartoon"
    assert kwargs["playlist_id"] == "PL1"

    # The preview URL must survive alongside the new YouTube id.
    assert result.youtube_video_id == "yt-42"
    assert result.blob_url == "https://blob/x.mp4"


def test_ayah_upload_collapses_ranges_into_surah_numbers(
    cfg, store, metadata_file, rendered
):
    client = FakeClient()
    store.record("job-1", rendered, "ayah")
    job = _ayah_job(metadata_file, upload_requested=True)

    with patch(
        "yt_audio_filter.overlay_pipeline.upload_rendered", return_value="yt-7"
    ) as mock_upload:
        run_job(job, _ctx(client), cfg, store)

    assert mock_upload.call_args.kwargs["surah_numbers"] == [1]


def test_surahs_from_ranges_collapses_consecutive_duplicates():
    from worker.contract import AyahRangeSpec

    ranges = [
        AyahRangeSpec(surah=1, start=1, end=3),
        AyahRangeSpec(surah=1, start=4, end=7),
        AyahRangeSpec(surah=112, start=1, end=4),
    ]
    assert surahs_from_ranges(ranges) == [1, 112]


def test_music_removal_upload_uses_the_seo_uploader(cfg, store, rendered, tmp_path):
    client = FakeClient()
    store.record("job-1", rendered, "music_removal", _video_metadata(tmp_path / "src.mp4"))
    job = _music_job(upload_requested=True, result={"blobUrl": "https://blob/m.mp4"})

    with patch("yt_audio_filter.uploader.upload_to_youtube", return_value="yt-99") as mock:
        result = run_job(job, _ctx(client), cfg, store)

    kwargs = mock.call_args.kwargs
    assert kwargs["video_path"] == rendered
    assert kwargs["privacy"] == "private"
    assert kwargs["playlist_id"] == "PLmusic"
    assert kwargs["original_metadata"].title == "Original Title"
    assert result.youtube_video_id == "yt-99"
    assert result.blob_url == "https://blob/m.mp4"


def test_upload_without_a_recorded_render_fails_with_a_clear_error(
    cfg, store, metadata_file
):
    client = FakeClient()
    job = _surah_job(metadata_file, upload_requested=True)

    with pytest.raises(OverlayError, match="No local rendered file"):
        run_job(job, _ctx(client), cfg, store)


# ---------------------------------------------------------------------------
# Blob delivery
# ---------------------------------------------------------------------------


def test_successful_blob_upload_populates_blob_url(cfg, store, metadata_file, rendered):
    client = FakeClient()
    blob_cfg = WorkerConfig(
        base_url=cfg.base_url,
        token=cfg.token,
        blob_token="blob-tok",
        poll_seconds=cfg.poll_seconds,
        cache_dir=cfg.cache_dir,
    )

    upload = BlobUpload(
        url="https://blob.example/rendered-xyz.mp4",
        download_url="https://blob.example/rendered-xyz.mp4?download=1",
        pathname="rendered.mp4",
        size_bytes=2048,
    )
    with patch("worker.blob.upload_file", return_value=upload) as mock_upload, patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ):
        result = run_job(_surah_job(metadata_file), _ctx(client), blob_cfg, store)

    assert mock_upload.call_args.args == (rendered, "blob-tok")
    assert result.blob_url == "https://blob.example/rendered-xyz.mp4"


def test_blob_upload_failure_still_completes_the_job_successfully(
    cfg, store, metadata_file, rendered
):
    client = FakeClient()
    blob_cfg = WorkerConfig(
        base_url=cfg.base_url,
        token=cfg.token,
        blob_token="blob-tok",
        poll_seconds=cfg.poll_seconds,
        cache_dir=cfg.cache_dir,
    )
    job = _surah_job(metadata_file)

    with patch(
        "worker.blob.upload_file", side_effect=BlobUploadError("HTTP 403", 403, "denied")
    ), patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ):
        execute_job(job, blob_cfg, client, store)

    assert len(client.complete_calls) == 1
    call = client.complete_calls[0]
    assert call["ok"] is True
    assert "blobUrl" not in call["result"]
    assert call["result"]["fileName"] == "rendered.mp4"

    logged = " ".join(line for _, _, lines in client.progress_calls for line in lines)
    assert "HTTP 403" in logged
    assert str(rendered) in logged  # the local path must be recoverable


def test_missing_blob_token_completes_the_job_and_logs_the_local_path(
    cfg, store, metadata_file, rendered
):
    client = FakeClient()

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ), patch("worker.blob.upload_file") as mock_upload:
        execute_job(_surah_job(metadata_file), cfg, client, store)

    mock_upload.assert_not_called()
    assert client.complete_calls[0]["ok"] is True
    assert "blobUrl" not in client.complete_calls[0]["result"]

    logged = " ".join(line for _, _, lines in client.progress_calls for line in lines)
    assert "BLOB_READ_WRITE_TOKEN" in logged
    assert str(rendered) in logged


# ---------------------------------------------------------------------------
# Failure + cancellation
# ---------------------------------------------------------------------------


def test_overlay_error_is_reported_with_error_and_error_details(
    cfg, store, metadata_file
):
    client = FakeClient()

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        side_effect=OverlayError("Unknown visual video_id: 'abc123'", "Catalog is empty"),
    ):
        execute_job(_surah_job(metadata_file), cfg, client, store)

    assert len(client.complete_calls) == 1
    call = client.complete_calls[0]
    assert call["ok"] is False
    assert call["error"] == "Unknown visual video_id: 'abc123'"
    assert call["errorDetails"] == "Catalog is empty"
    assert call["result"] is None


def test_a_missing_metadata_file_fails_the_job_with_a_clear_error(cfg, store, tmp_path):
    client = FakeClient()
    job = _surah_job(tmp_path / "does-not-exist.json")

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers"
    ) as mock_render:
        execute_job(job, cfg, client, store)

    mock_render.assert_not_called()
    call = client.complete_calls[0]
    assert call["ok"] is False
    assert "Metadata file not found" in call["error"]


def test_an_unexpected_exception_still_produces_error_details(cfg, store, metadata_file):
    client = FakeClient()

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        side_effect=RuntimeError("ffmpeg vanished"),
    ):
        execute_job(_surah_job(metadata_file), cfg, client, store)

    call = client.complete_calls[0]
    assert call["ok"] is False
    assert call["error"] == "ffmpeg vanished"
    assert "RuntimeError" in call["errorDetails"]


def test_cancellation_before_the_render_aborts_without_calling_the_pipeline(
    cfg, store, metadata_file
):
    # The first progress post comes back cancelled.
    client = FakeClient(cancel_on_call=1)

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers"
    ) as mock_render:
        with pytest.raises(JobCancelled):
            run_job(_surah_job(metadata_file), _ctx(client), cfg, store)

    mock_render.assert_not_called()


def test_cancellation_mid_render_stops_a_music_removal_job(cfg, store, tmp_path):
    client = FakeClient(cancel_on_call=3)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x")
    seen: List[int] = []

    def fake_process(**kwargs):
        callback = kwargs["progress_callback"]
        for percent in range(0, 101, 10):
            callback("Isolate Vocals", percent)
            seen.append(percent)
        return kwargs["output_path"]

    with patch(
        "yt_audio_filter.youtube.download_video_with_metadata",
        return_value=_video_metadata(source),
    ), patch("yt_audio_filter.pipeline.process_video", side_effect=fake_process):
        execute_job(_music_job(), cfg, client, store)

    # The callback raised out of process_video, so the loop stopped early...
    assert len(seen) < 11
    # ...and a cancelled job is never reported as done or failed.
    assert client.complete_calls == []


def test_cancellation_detected_by_the_heartbeat_raises_at_the_stage_boundary(
    cfg, store, metadata_file, rendered
):
    client = FakeClient()
    ctx = JobContext(client, "job-1", heartbeat_seconds=0)

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ):
        # Simulate the background heartbeat having observed a cancellation
        # while FFmpeg was mid-render.
        def _cancel_during_render(**kwargs):
            ctx.cancelled = True
            return OverlayResult(output_path=rendered)

        with patch(
            "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
            side_effect=_cancel_during_render,
        ):
            with pytest.raises(JobCancelled):
                run_job(_surah_job(metadata_file), ctx, cfg, store)


# ---------------------------------------------------------------------------
# Worker targeting (several machines, one queue)
# ---------------------------------------------------------------------------


def _targeted_cfg(cfg: WorkerConfig, worker_id: str) -> WorkerConfig:
    return WorkerConfig(
        base_url=cfg.base_url,
        token=cfg.token,
        blob_token=cfg.blob_token,
        poll_seconds=cfg.poll_seconds,
        cache_dir=cfg.cache_dir,
        worker_id=worker_id,
    )


def _with_target(job: Job, target: Optional[str]) -> Job:
    return Job.from_json({**job.to_json(), "targetWorkerId": target})


def test_a_job_targeted_at_another_worker_fails_instead_of_running(
    cfg, store, metadata_file
):
    """Server-side routing does the filtering; this backstop makes a routing bug
    a visible failure rather than a render on the wrong (possibly GPU-less)
    machine."""
    client = FakeClient()
    job = _with_target(_surah_job(metadata_file), "desktop-b0b0b0b0")

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers"
    ) as mock_render:
        execute_job(job, _targeted_cfg(cfg, "laptop-a1a1a1a1"), client, store)

    mock_render.assert_not_called()
    assert len(client.complete_calls) == 1
    call = client.complete_calls[0]
    assert call["ok"] is False
    assert "desktop-b0b0b0b0" in call["error"]
    assert "laptop-a1a1a1a1" in call["error"]
    assert call["result"] is None


def test_a_job_targeted_at_this_worker_runs(cfg, store, metadata_file, rendered):
    client = FakeClient()
    job = _with_target(_surah_job(metadata_file), "laptop-a1a1a1a1")

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ) as mock_render:
        execute_job(job, _targeted_cfg(cfg, "laptop-a1a1a1a1"), client, store)

    mock_render.assert_called_once()
    assert client.complete_calls[0]["ok"] is True


def test_an_untargeted_job_runs_on_whichever_worker_claimed_it(
    cfg, store, metadata_file, rendered
):
    client = FakeClient()
    job = _with_target(_surah_job(metadata_file), None)

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ) as mock_render:
        execute_job(job, _targeted_cfg(cfg, "laptop-a1a1a1a1"), client, store)

    mock_render.assert_called_once()
    assert client.complete_calls[0]["ok"] is True


def test_unknown_job_kind_is_rejected(cfg, store):
    client = FakeClient()
    job = _job("search", {"kind": "search", "query": "x"})
    object.__setattr__(job, "kind", "transcode")

    with pytest.raises(OverlayError, match="Unknown job kind"):
        run_job(job, _ctx(client), cfg, store)


# --------------------------------------------------------------- blob pathname


def test_blob_pathname_comes_from_url_not_echoed_field() -> None:
    """Regression: with ``x-add-random-suffix: 1`` the Blob API echoes back the
    *requested* pathname while storing the object under a suffixed name.

    Trusting the echoed field made the Studio presign a path that does not
    exist, so every render preview 404'd even though the upload succeeded.
    """
    from unittest.mock import MagicMock

    from worker import blob as blob_mod

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        # What the API really returns: suffixed url, unsuffixed pathname.
        "url": "https://s.private.blob.vercel-storage.com/AnNas_LI0SR-Xy7Qz9.mp4",
        "downloadUrl": "https://s.private.blob.vercel-storage.com/AnNas_LI0SR-Xy7Qz9.mp4?download=1",
        "pathname": "AnNas_LI0SR.mp4",
    }
    session = MagicMock()
    session.put.return_value = response

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "AnNas_LI0SR.mp4"
        src.write_bytes(b"\x00" * 32)
        upload = blob_mod.upload_file(src, "tok", session=session)

    assert upload.pathname == "AnNas_LI0SR-Xy7Qz9.mp4"
    assert upload.pathname != response.json.return_value["pathname"]

    # And the private-store access header must be present.
    sent_headers = session.put.call_args.kwargs["headers"]
    assert sent_headers["x-vercel-blob-access"] == "private"
