"""Unit tests for worker job dispatch.

Each job kind must reach the right pipeline function with the right keyword
arguments — above all ``upload=False``, because publishing to YouTube is only
ever a separate, explicitly user-requested job.

Everything below is mocked: no ``requests``, no FFmpeg, no Demucs, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple
from unittest.mock import patch

import pytest

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
from yt_audio_filter.source_probe import SourceQualityInfo
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
# probe dispatch
# ---------------------------------------------------------------------------


def test_probe_job_returns_the_measured_quality_of_the_cached_visual(cfg, store):
    client = FakeClient()
    job = _job("probe", {"kind": "probe", "visual": _visual()})

    with patch(
        "yt_audio_filter.source_probe.probe_source_quality",
        return_value=SourceQualityInfo(
            kind="measured", width=640, height=360, fps=25.0, codec="h264"
        ),
    ) as mock_probe:
        result = run_job(job, _ctx(client), cfg, store)

    kwargs = mock_probe.call_args.kwargs
    assert kwargs["video_id"] == "abc123"
    assert kwargs["url"] == "https://www.youtube.com/watch?v=abc123"
    assert kwargs["cache_dir"] == cfg.cache_dir

    assert result.source_quality is not None
    payload = result.to_json()["sourceQuality"]
    assert payload["kind"] == "measured"
    assert payload["height"] == 360
    assert payload["codec"] == "h264"
    assert payload["probedAt"] > 0


def test_probe_job_reports_an_undeterminable_quality_instead_of_failing(cfg, store):
    """The card degrades to "unknown"; a quality hint must never fail a job."""
    client = FakeClient()
    job = _job("probe", {"kind": "probe", "visual": _visual()})

    with patch(
        "yt_audio_filter.source_probe.probe_source_quality",
        return_value=SourceQualityInfo(kind="listed"),
    ):
        result = run_job(job, _ctx(client), cfg, store)

    assert result.source_quality is not None
    assert result.source_quality.height is None
    assert result.to_json()["sourceQuality"]["height"] is None


def test_probe_job_produces_nothing_to_upload(cfg, store, rendered):
    """A probe never renders a file, so an upload request against one has to
    fail loudly rather than publish some other job's leftover render."""
    client = FakeClient()
    store.record("job-1", rendered, "probe")
    job = _job("probe", {"kind": "probe", "visual": _visual()}, upload_requested=True)

    with pytest.raises(OverlayError, match="nothing to upload"):
        run_job(job, _ctx(client), cfg, store)


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
        result={
            "localPath": str(rendered),
            "fileName": "rendered.mp4",
            "sizeBytes": 2048,
        },
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

    # The render's own fields must survive alongside the new YouTube id, so the
    # finished card can still say where the file is.
    assert result.youtube_video_id == "yt-42"
    assert result.local_path == str(rendered)
    assert result.file_name == "rendered.mp4"


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
    job = _music_job(upload_requested=True, result={"localPath": str(rendered)})

    with patch("yt_audio_filter.uploader.upload_to_youtube", return_value="yt-99") as mock:
        result = run_job(job, _ctx(client), cfg, store)

    kwargs = mock.call_args.kwargs
    assert kwargs["video_path"] == rendered
    assert kwargs["privacy"] == "private"
    assert kwargs["playlist_id"] == "PLmusic"
    assert kwargs["original_metadata"].title == "Original Title"
    assert result.youtube_video_id == "yt-99"
    assert result.local_path == str(rendered)


def test_upload_without_a_recorded_render_fails_with_a_clear_error(
    cfg, store, metadata_file
):
    client = FakeClient()
    job = _surah_job(metadata_file, upload_requested=True)

    with pytest.raises(OverlayError, match="No local rendered file"):
        run_job(job, _ctx(client), cfg, store)


# ---------------------------------------------------------------------------
# Render delivery — the file never leaves this machine
# ---------------------------------------------------------------------------


def test_render_result_carries_the_local_path_name_and_size(
    cfg, store, metadata_file, rendered
):
    """``localPath`` is the whole delivery story now: it tells the user which
    file to look for, and it is what the Studio gates the Upload button on."""
    client = FakeClient()

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ):
        execute_job(_surah_job(metadata_file), cfg, client, store)

    assert len(client.complete_calls) == 1
    call = client.complete_calls[0]
    assert call["ok"] is True
    assert call["result"]["localPath"] == str(rendered.resolve())
    assert call["result"]["fileName"] == "rendered.mp4"
    assert call["result"]["sizeBytes"] == 2048

    # The path is in the log too, so it survives dismissing the job card.
    logged = " ".join(line for _, _, lines in client.progress_calls for line in lines)
    assert str(rendered.resolve()) in logged


def test_render_records_the_path_in_the_sidecar_store_for_a_later_upload(
    cfg, store, metadata_file, rendered
):
    """The upload handler reads the sidecar, not the job result — that is what
    lets a render survive a worker restart before it is published."""
    client = FakeClient()

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
        return_value=OverlayResult(output_path=rendered),
    ):
        execute_job(_surah_job(metadata_file), cfg, client, store)

    record = store.lookup("job-1")
    assert record is not None
    assert Path(record.path) == rendered.resolve()


def test_search_result_has_no_local_path_so_it_can_never_be_uploaded(cfg, store):
    """A search job finishes ``done`` with nothing on disk. Its result must not
    carry ``localPath``, because that field is the Studio's upload gate."""
    client = FakeClient()

    with patch("yt_audio_filter.cartoon_search.search_videos", return_value=[]):
        execute_job(
            _job("search", {"kind": "search", "query": "cartoon"}), cfg, client, store
        )

    assert client.complete_calls[0]["ok"] is True
    assert "localPath" not in client.complete_calls[0]["result"]


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


# ------------------------------------------- unknown job kinds must not stall


def test_unparseable_job_fails_that_job_not_the_poll_loop() -> None:
    """The Studio deploys on push while the worker is updated by hand, so it
    WILL be handed job kinds it does not know.

    That must fail the job, not the claim call. If the parse error escapes
    ``claim()``, ``run_forever`` reads it as a transport failure, backs off to
    a minute, and the job — already marked ``claimed`` server-side — is
    stranded forever with real work queued behind it.
    """
    from unittest.mock import MagicMock

    from worker.client import StudioClient

    client = StudioClient("https://studio.example", "tok")
    posted: list = []

    def fake_post(path, payload):
        posted.append((path, payload))
        if path == "/api/worker/claim":
            return {"job": {"id": "job-1", "kind": "teleport", "input": {"kind": "teleport"}}}
        return {}

    client._post = fake_post  # type: ignore[method-assign]

    job = client.claim(MagicMock(to_json=lambda: {}))

    # Idle, not an exception — the loop keeps its normal cadence.
    assert job is None
    # And the job was reported as failed rather than left hanging.
    completes = [p for p in posted if p[0] == "/api/worker/complete"]
    assert len(completes) == 1
    body = completes[0][1]
    assert body["jobId"] == "job-1"
    assert body["ok"] is False
    assert "teleport" in body["error"]


def test_claim_survives_being_unable_to_report_the_bad_job() -> None:
    """Reporting is best effort — if that POST also fails, the loop still must
    not take the exception."""
    from unittest.mock import MagicMock

    from worker.client import StudioClient

    client = StudioClient("https://studio.example", "tok")

    def fake_post(path, payload):
        if path == "/api/worker/claim":
            return {"job": {"id": "job-2", "kind": "teleport"}}
        raise RuntimeError("studio unreachable")

    client._post = fake_post  # type: ignore[method-assign]

    assert client.claim(MagicMock(to_json=lambda: {})) is None


# ------------------------------------------- never prompt on an unattended box


def test_worker_forbids_interactive_youtube_auth() -> None:
    """`run_local_server` opens a browser and blocks forever waiting for a
    redirect. On a worker nobody is sitting at, that freezes the single-job
    loop behind a prompt that can never be answered."""
    import os

    from worker.worker import _forbid_interactive_auth
    from yt_audio_filter.uploader import NONINTERACTIVE_ENV, _noninteractive

    original = os.environ.pop(NONINTERACTIVE_ENV, None)
    try:
        assert _noninteractive() is False
        _forbid_interactive_auth()
        assert _noninteractive() is True
    finally:
        os.environ.pop(NONINTERACTIVE_ENV, None)
        if original is not None:
            os.environ[NONINTERACTIVE_ENV] = original


def test_noninteractive_auth_fails_with_instructions_instead_of_hanging() -> None:
    """The failure must name the machine and say exactly what to run on it."""
    import os
    from unittest.mock import patch

    from yt_audio_filter.uploader import (
        NONINTERACTIVE_ENV,
        YouTubeUploadError,
        authenticate_youtube,
    )

    original = os.environ.get(NONINTERACTIVE_ENV)
    os.environ[NONINTERACTIVE_ENV] = "1"
    try:
        with patch(
            "yt_audio_filter.uploader.check_upload_dependencies", return_value=True
        ), patch(
            "yt_audio_filter.uploader.check_credentials_configured", return_value=True
        ), patch(
            "yt_audio_filter.uploader.OAUTH_TOKEN_FILE"
        ) as token_file, patch(
            "google_auth_oauthlib.flow.InstalledAppFlow.run_local_server"
        ) as server:
            token_file.exists.return_value = False
            with pytest.raises(YouTubeUploadError) as excinfo:
                authenticate_youtube()

        # The browser flow must never have been reached.
        server.assert_not_called()
        message = f"{excinfo.value.message} {excinfo.value.details}"
        assert "re-authorised" in message
        assert "authenticate_youtube" in message
        assert "Nothing was uploaded" in message
    finally:
        os.environ.pop(NONINTERACTIVE_ENV, None)
        if original is not None:
            os.environ[NONINTERACTIVE_ENV] = original
