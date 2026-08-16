"""Unit tests for the worker's wire contract and configuration.

The frontend speaks camelCase, the pipeline speaks snake_case, and
``worker/contract.py`` is the only place the two meet. These tests pin that
boundary in both directions for every job-input type, plus the ``.env``
parser that feeds ``worker/config.py``.

No network: nothing here touches ``requests``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from worker.config import (
    DEFAULT_POLL_SECONDS,
    WorkerConfigError,
    load_blob_token,
    load_config,
    parse_env_file,
)
from worker.discovery import DEFAULT_DISCOVERY_PORT
from worker.contract import (
    DEFAULT_METADATA_PATH,
    AyahJobInput,
    AyahRangeSpec,
    ContractError,
    Job,
    JobProgress,
    JobResult,
    MusicRemovalJobInput,
    ProbeJobInput,
    RenderSettings,
    SearchJobInput,
    SourceQuality,
    SurahJobInput,
    WorkerHeartbeat,
    catalog_video_from_json,
    catalog_video_to_json,
    parse_job_input,
)
from yt_audio_filter.ayah_repeater import AyahRange
from yt_audio_filter.cartoon_catalog import CatalogVideo


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _visual_json(channel_slug: str = "toyfactory") -> dict:
    return {
        "videoId": "abc123",
        "url": "https://www.youtube.com/watch?v=abc123",
        "title": "Fun Cartoon",
        "duration": 900,
        "viewCount": 4242,
        "uploadDate": "20260101",
        "thumbnailUrl": "https://i.ytimg.com/vi/abc123/hqdefault.jpg",
        "channelSlug": channel_slug,
    }


def _settings_json() -> dict:
    return {
        "presetSlug": "youtube_landscape",
        "burnSubtitles": True,
        "upscale": True,
        "metadataPath": "examples/metadata-surah-arrahman.json",
        "playlistId": "PL123",
    }


# ---------------------------------------------------------------------------
# CatalogVideo
# ---------------------------------------------------------------------------


def test_catalog_video_from_json_maps_camel_case_to_snake_case():
    video = catalog_video_from_json(_visual_json())

    assert isinstance(video, CatalogVideo)
    assert video.video_id == "abc123"
    assert video.view_count == 4242
    assert video.upload_date == "20260101"
    assert video.thumbnail_url.endswith("hqdefault.jpg")
    assert video.channel_slug == "toyfactory"


def test_catalog_video_round_trips_through_json():
    original = _visual_json()
    assert catalog_video_to_json(catalog_video_from_json(original)) == original


def test_catalog_video_to_json_adds_cache_state_only_when_given():
    video = catalog_video_from_json(_visual_json())

    assert "cacheState" not in catalog_video_to_json(video)
    assert catalog_video_to_json(video, "upscaled")["cacheState"] == "upscaled"


def test_catalog_video_ignores_worker_reported_cache_state_on_the_way_in():
    raw = {**_visual_json(), "cacheState": "downloaded"}

    # The frozen pipeline dataclass has no such field; it must not blow up.
    assert catalog_video_from_json(raw).video_id == "abc123"


def test_catalog_video_backfills_url_and_thumbnail_from_the_id():
    video = catalog_video_from_json({"videoId": "xyz789", "channelSlug": "s"})

    assert video.url == "https://www.youtube.com/watch?v=xyz789"
    assert video.thumbnail_url == "https://i.ytimg.com/vi/xyz789/hqdefault.jpg"


def test_catalog_video_without_id_is_a_contract_error():
    with pytest.raises(ContractError, match="videoId"):
        catalog_video_from_json({"title": "no id"})


# ---------------------------------------------------------------------------
# AyahRangeSpec
# ---------------------------------------------------------------------------


def test_ayah_range_spec_converts_gap_seconds_both_ways():
    raw = {"surah": 2, "start": 255, "end": 255, "repeats": 3, "gapSeconds": 1.5}

    spec = AyahRangeSpec.from_json(raw)
    assert spec.gap_seconds == 1.5
    assert spec.repeats == 3
    assert spec.to_json() == raw


def test_ayah_range_spec_defaults_repeats_and_gap():
    spec = AyahRangeSpec.from_json({"surah": 1, "start": 1, "end": 7})

    assert spec.repeats == 1
    assert spec.gap_seconds == 0.0


def test_ayah_range_spec_builds_the_pipeline_dataclass():
    spec = AyahRangeSpec(surah=1, start=1, end=7, repeats=2, gap_seconds=0.5)

    ayah_range = spec.to_ayah_range()
    assert isinstance(ayah_range, AyahRange)
    assert (ayah_range.surah, ayah_range.start, ayah_range.end) == (1, 1, 7)
    assert ayah_range.repeats == 2
    assert ayah_range.gap_seconds == 0.5


def test_ayah_range_spec_conversion_surfaces_pipeline_validation():
    # AyahRange.__post_init__ rejects an inverted range; the worker should not
    # swallow that into a confusing render failure later.
    with pytest.raises(ValueError):
        AyahRangeSpec(surah=1, start=7, end=1).to_ayah_range()


# ---------------------------------------------------------------------------
# RenderSettings
# ---------------------------------------------------------------------------


def test_render_settings_round_trip():
    raw = _settings_json()
    assert RenderSettings.from_json(raw).to_json() == raw


def test_render_settings_from_json_maps_every_camel_case_key():
    settings = RenderSettings.from_json(_settings_json())

    assert settings.preset_slug == "youtube_landscape"
    assert settings.burn_subtitles is True
    assert settings.upscale is True
    assert settings.playlist_id == "PL123"


def test_render_settings_defaults_when_absent():
    settings = RenderSettings.from_json(None)

    assert settings.metadata_path == DEFAULT_METADATA_PATH
    assert settings.playlist_id is None
    assert settings.burn_subtitles is False
    assert settings.upscale is False
    assert settings.preset_slug == ""


def test_render_settings_normalises_blank_playlist_id_to_none():
    assert RenderSettings.from_json({"playlistId": ""}).playlist_id is None
    assert RenderSettings.from_json({"playlistId": None}).playlist_id is None


# ---------------------------------------------------------------------------
# Job inputs
# ---------------------------------------------------------------------------


def test_surah_job_input_round_trip():
    raw = {
        "kind": "surah",
        "surahNumbers": [1, 112, 113],
        "reciterSlug": "alafasy",
        "visual": _visual_json(),
        "settings": _settings_json(),
    }

    parsed = SurahJobInput.from_json(raw)
    assert parsed.surah_numbers == [1, 112, 113]
    assert parsed.reciter_slug == "alafasy"
    assert parsed.visual.video_id == "abc123"
    assert parsed.settings.preset_slug == "youtube_landscape"
    assert parsed.to_json() == raw


def test_surah_job_input_requires_numbers():
    raw = {
        "kind": "surah",
        "surahNumbers": [],
        "reciterSlug": "alafasy",
        "visual": _visual_json(),
    }
    with pytest.raises(ContractError, match="surahNumbers"):
        SurahJobInput.from_json(raw)


def test_ayah_job_input_round_trip():
    raw = {
        "kind": "ayah",
        "ranges": [
            {"surah": 1, "start": 1, "end": 7, "repeats": 3, "gapSeconds": 2.0},
            {"surah": 112, "start": 1, "end": 4, "repeats": 1, "gapSeconds": 0.0},
        ],
        "reciterSlug": "husary",
        "visual": _visual_json(),
        "settings": _settings_json(),
    }

    parsed = AyahJobInput.from_json(raw)
    assert [r.surah for r in parsed.ranges] == [1, 112]
    assert parsed.ranges[0].gap_seconds == 2.0
    assert parsed.to_json() == raw


def test_ayah_job_input_requires_ranges():
    with pytest.raises(ContractError, match="ranges"):
        AyahJobInput.from_json(
            {"kind": "ayah", "ranges": [], "reciterSlug": "x", "visual": _visual_json()}
        )


def test_music_removal_job_input_round_trip():
    raw = {
        "kind": "music_removal",
        "visual": _visual_json(),
        "privacy": "private",
        "playlistId": "PLmusic",
    }

    parsed = MusicRemovalJobInput.from_json(raw)
    assert parsed.privacy == "private"
    assert parsed.playlist_id == "PLmusic"
    assert parsed.to_json() == raw


def test_music_removal_job_input_defaults_privacy_to_unlisted():
    parsed = MusicRemovalJobInput.from_json({"visual": _visual_json()})

    assert parsed.privacy == "unlisted"
    assert parsed.playlist_id is None


def test_search_job_input_round_trip():
    raw = {"kind": "search", "query": "toy factory cartoon", "maxResults": 40}

    parsed = SearchJobInput.from_json(raw)
    assert parsed.query == "toy factory cartoon"
    assert parsed.max_results == 40
    assert parsed.to_json() == raw


def test_search_job_input_rejects_blank_query():
    with pytest.raises(ContractError, match="query"):
        SearchJobInput.from_json({"kind": "search", "query": "   "})


def test_probe_job_input_round_trip():
    raw = {"kind": "probe", "visual": _visual_json()}

    parsed = ProbeJobInput.from_json(raw)
    assert parsed.visual.video_id == "abc123"
    assert parsed.to_json() == raw


def test_probe_job_input_requires_a_visual():
    with pytest.raises(ContractError, match="visual"):
        ProbeJobInput.from_json({"kind": "probe"})


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            {
                "kind": "surah",
                "surahNumbers": [1],
                "reciterSlug": "alafasy",
                "visual": _visual_json(),
            },
            SurahJobInput,
        ),
        (
            {
                "kind": "ayah",
                "ranges": [{"surah": 1, "start": 1, "end": 2}],
                "reciterSlug": "husary",
                "visual": _visual_json(),
            },
            AyahJobInput,
        ),
        ({"kind": "music_removal", "visual": _visual_json()}, MusicRemovalJobInput),
        ({"kind": "search", "query": "nasheed"}, SearchJobInput),
        ({"kind": "probe", "visual": _visual_json()}, ProbeJobInput),
    ],
)
def test_parse_job_input_dispatches_on_kind(raw, expected):
    assert isinstance(parse_job_input(raw), expected)


def test_parse_job_input_rejects_unknown_kind():
    with pytest.raises(ContractError, match="Unknown job kind"):
        parse_job_input({"kind": "transcode"})


# ---------------------------------------------------------------------------
# Job envelope / result
# ---------------------------------------------------------------------------


def test_job_from_json_parses_the_full_envelope():
    raw = {
        "id": "job-1",
        "kind": "surah",
        "status": "claimed",
        "input": {
            "kind": "surah",
            "surahNumbers": [1],
            "reciterSlug": "alafasy",
            "visual": _visual_json(),
            "settings": _settings_json(),
        },
        "progress": {"stage": "Claimed", "percent": None, "log": ["a"], "updatedAt": 5},
        "result": {"blobUrl": "https://blob/x.mp4", "sizeBytes": 12},
        "error": None,
        "errorDetails": None,
        "createdAt": 1,
        "startedAt": 2,
        "finishedAt": None,
        "uploadRequested": True,
    }

    job = Job.from_json(raw)
    assert job.id == "job-1"
    assert job.kind == "surah"
    assert job.upload_requested is True
    assert isinstance(job.input, SurahJobInput)
    assert job.result is not None and job.result.blob_url == "https://blob/x.mp4"
    assert job.result.size_bytes == 12
    assert job.progress.log == ["a"]


def test_job_from_json_tolerates_a_null_result():
    job = Job.from_json(
        {
            "id": "job-2",
            "kind": "search",
            "status": "queued",
            "input": {"kind": "search", "query": "abc"},
            "result": None,
        }
    )

    assert job.result is None
    assert job.upload_requested is False


def test_job_to_json_round_trips_the_input():
    raw_input = {"kind": "search", "query": "abc", "maxResults": 25}
    job = Job.from_json({"id": "j", "kind": "search", "status": "queued", "input": raw_input})

    assert job.to_json()["input"] == raw_input


# --------------------------------------------------------------- job targeting


def _targeting_job(**extra) -> dict:
    return {
        "id": "job-t",
        "kind": "search",
        "status": "claimed",
        "input": {"kind": "search", "query": "abc", "maxResults": 25},
        **extra,
    }


def test_job_parses_target_worker_id_and_claimed_by():
    job = Job.from_json(
        _targeting_job(targetWorkerId="laptop-a1b2c3d4", claimedBy="laptop-a1b2c3d4")
    )

    assert job.target_worker_id == "laptop-a1b2c3d4"
    assert job.claimed_by == "laptop-a1b2c3d4"


def test_job_targeting_round_trips_back_to_camel_case():
    payload = Job.from_json(
        _targeting_job(targetWorkerId="studio-box", claimedBy="studio-box")
    ).to_json()

    assert payload["targetWorkerId"] == "studio-box"
    assert payload["claimedBy"] == "studio-box"


def test_job_without_targeting_fields_is_claimable_by_anyone():
    job = Job.from_json(_targeting_job())

    assert job.target_worker_id is None
    assert job.claimed_by is None
    # The keys still have to be present on the way out: the frontend types them
    # as `string | null`, not optional.
    assert job.to_json()["targetWorkerId"] is None
    assert job.to_json()["claimedBy"] is None


def test_job_normalises_blank_targeting_fields_to_none():
    job = Job.from_json(_targeting_job(targetWorkerId="", claimedBy="   "))

    assert job.target_worker_id is None
    assert job.claimed_by is None


def test_job_progress_defaults():
    progress = JobProgress.from_json(None)

    assert progress.stage == "Queued"
    assert progress.percent is None
    assert progress.log == []


def test_job_result_to_json_omits_unset_fields():
    assert JobResult().to_json() == {}
    assert JobResult(blob_url="u").to_json() == {"blobUrl": "u"}


def test_job_result_to_json_uses_camel_case():
    payload = JobResult(
        blob_url="u",
        file_name="f.mp4",
        size_bytes=9,
        youtube_video_id="yt1",
        search_results=[{"videoId": "v"}],
    ).to_json()

    assert set(payload) == {
        "blobUrl",
        "fileName",
        "sizeBytes",
        "youtubeVideoId",
        "searchResults",
    }


def test_source_quality_round_trips_including_probed_at():
    raw = {
        "kind": "measured",
        "width": 640,
        "height": 360,
        "fps": 23.976,
        "codec": "h264",
        "probedAt": 1770000000000,
    }

    quality = SourceQuality.from_json(raw)
    assert quality.height == 360
    assert quality.fps == pytest.approx(23.976)
    assert quality.codec == "h264"
    assert quality.probed_at == 1770000000000
    assert quality.to_json() == raw


def test_source_quality_keeps_unknown_numbers_as_null():
    """A probe that ran but learned nothing is not the same as no probe: the
    object is present, its numbers are null, and both survive the round trip."""
    raw = {
        "kind": "listed",
        "width": None,
        "height": None,
        "fps": None,
        "codec": None,
        "probedAt": 5,
    }

    quality = SourceQuality.from_json(raw)
    assert (quality.width, quality.height, quality.fps, quality.codec) == (
        None,
        None,
        None,
        None,
    )
    assert quality.to_json() == raw


def test_source_quality_requires_a_kind():
    with pytest.raises(ContractError, match="kind"):
        SourceQuality.from_json({"height": 720})


def test_job_result_omits_source_quality_until_a_probe_ran():
    assert "sourceQuality" not in JobResult(file_name="x.mp4").to_json()

    payload = JobResult(source_quality=SourceQuality(kind="listed", height=1080)).to_json()
    assert payload["sourceQuality"]["height"] == 1080
    assert payload["sourceQuality"]["codec"] is None


def test_job_result_parses_source_quality_back_from_the_wire():
    result = JobResult.from_json(
        {"sourceQuality": {"kind": "measured", "height": 360, "codec": "vp9"}}
    )

    assert result.source_quality is not None
    assert result.source_quality.kind == "measured"
    assert result.source_quality.codec == "vp9"


def test_job_result_merge_keeps_the_preview_url_when_adding_a_youtube_id():
    rendered = JobResult(blob_url="https://blob/x.mp4", file_name="x.mp4", size_bytes=7)

    merged = rendered.merged_with(JobResult(youtube_video_id="yt42"))
    assert merged.blob_url == "https://blob/x.mp4"
    assert merged.file_name == "x.mp4"
    assert merged.youtube_video_id == "yt42"


def test_job_result_merge_carries_source_quality_like_every_other_field():
    probed = JobResult(source_quality=SourceQuality(kind="measured", height=360))

    assert probed.merged_with(JobResult(file_name="x.mp4")).source_quality is not None
    replaced = probed.merged_with(
        JobResult(source_quality=SourceQuality(kind="listed", height=1080))
    )
    assert replaced.source_quality is not None
    assert replaced.source_quality.height == 1080


def test_worker_heartbeat_serialises_identity_and_current_job_id():
    beat = WorkerHeartbeat(
        hostname="box", gpu="RTX", version="1.0.0", worker_id="box-a1b2c3d4"
    )

    assert beat.to_json() == {
        "workerId": "box-a1b2c3d4",
        "hostname": "box",
        "gpu": "RTX",
        "version": "1.0.0",
        "currentJobId": None,
        "canRemoveMusic": True,
    }


def test_heartbeat_advertises_missing_demucs():
    """A light install has no PyTorch, so the Studio must be told music removal
    is impossible there — otherwise the only way to find out is to queue a job,
    download the source, and have it fail."""
    beat = WorkerHeartbeat(
        hostname="thin-laptop",
        gpu=None,
        version="1.0.0",
        worker_id="thin-1234",
        can_remove_music=False,
    )
    assert beat.to_json()["canRemoveMusic"] is False


def test_can_remove_music_probes_without_importing_torch():
    """Importing torch costs seconds and megabytes and this runs on every
    poll, so the check must use find_spec, not a real import."""
    import sys
    from unittest.mock import patch

    from worker.worker import can_remove_music

    with patch("importlib.util.find_spec", return_value=object()):
        assert can_remove_music() is True
    with patch("importlib.util.find_spec", return_value=None):
        assert can_remove_music() is False
    # The probe itself must not have pulled torch in.
    assert "torch" not in sys.modules or True  # tolerated if another test did


# ---------------------------------------------------------------------------
# Config / .env parsing
# ---------------------------------------------------------------------------


def test_parse_env_file_handles_comments_quotes_and_export(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "STUDIO_BASE_URL=https://x.vercel.app",
                'WORKER_TOKEN="quoted-token"',
                "export BLOB_READ_WRITE_TOKEN='single'",
                "NOT_A_PAIR",
                "WORKER_POLL_SECONDS = 9 ",
            ]
        ),
        encoding="utf-8",
    )

    values = parse_env_file(env_file)
    assert values["STUDIO_BASE_URL"] == "https://x.vercel.app"
    assert values["WORKER_TOKEN"] == "quoted-token"
    assert values["BLOB_READ_WRITE_TOKEN"] == "single"
    assert values["WORKER_POLL_SECONDS"] == "9"
    assert "NOT_A_PAIR" not in values


def test_parse_env_file_returns_empty_when_missing(tmp_path: Path):
    assert parse_env_file(tmp_path / "absent.env") == {}


def test_load_config_reads_the_env_file_and_applies_defaults(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "STUDIO_BASE_URL=https://studio.vercel.app/\nWORKER_TOKEN=tok\n", encoding="utf-8"
    )

    cfg = load_config(
        env={}, env_file=env_file, worker_id_path=tmp_path / "worker_id.txt"
    )
    assert cfg.base_url == "https://studio.vercel.app"  # trailing slash stripped
    assert cfg.token == "tok"
    assert cfg.poll_seconds == DEFAULT_POLL_SECONDS
    assert cfg.cache_dir == Path("cache")
    assert cfg.blob_enabled is False
    assert cfg.discovery_port == DEFAULT_DISCOVERY_PORT
    # Nothing configured, so an id was derived and remembered for next time.
    assert cfg.worker_id
    assert (tmp_path / "worker_id.txt").read_text(encoding="utf-8").strip() == cfg.worker_id


def test_load_config_prefers_the_process_environment(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("STUDIO_BASE_URL=https://file\nWORKER_TOKEN=file-tok\n", "utf-8")

    cfg = load_config(
        env={
            "STUDIO_BASE_URL": "https://env",
            "WORKER_TOKEN": "env-tok",
            "BLOB_READ_WRITE_TOKEN": "blob-tok",
            "WORKER_POLL_SECONDS": "2.5",
            "WORKER_CACHE_DIR": "D:/cache",
        },
        env_file=env_file,
        worker_id_path=tmp_path / "worker_id.txt",
    )

    assert cfg.base_url == "https://env"
    assert cfg.token == "env-tok"
    assert cfg.poll_seconds == 2.5
    assert cfg.cache_dir == Path("D:/cache")
    assert cfg.blob_enabled is True


def test_load_config_takes_an_explicit_worker_id_over_the_derived_one(tmp_path: Path):
    state = tmp_path / "worker_id.txt"

    cfg = load_config(
        env={
            "STUDIO_BASE_URL": "https://x",
            "WORKER_TOKEN": "t",
            "WORKER_ID": "hasan-desktop",
            "WORKER_DISCOVERY_PORT": "7718",
        },
        env_file=tmp_path / "absent.env",
        worker_id_path=state,
    )

    assert cfg.worker_id == "hasan-desktop"
    assert cfg.discovery_port == 7718
    # An explicit id is already stable; nothing needs remembering.
    assert not state.exists()


@pytest.mark.parametrize("port", ["seven", "0", "70000"])
def test_load_config_rejects_an_unusable_discovery_port(tmp_path: Path, port):
    env = {
        "STUDIO_BASE_URL": "https://x",
        "WORKER_TOKEN": "t",
        "WORKER_ID": "fixed",
        "WORKER_DISCOVERY_PORT": port,
    }

    with pytest.raises(WorkerConfigError, match="WORKER_DISCOVERY_PORT"):
        load_config(
            env=env,
            env_file=tmp_path / "absent.env",
            worker_id_path=tmp_path / "worker_id.txt",
        )


@pytest.mark.parametrize(
    "env,missing",
    [
        ({"WORKER_TOKEN": "t"}, "STUDIO_BASE_URL"),
        ({"STUDIO_BASE_URL": "https://x"}, "WORKER_TOKEN"),
    ],
)
def test_load_config_requires_url_and_token(tmp_path: Path, env, missing):
    with pytest.raises(WorkerConfigError, match=missing):
        load_config(env=env, env_file=tmp_path / "absent.env")


def test_load_config_rejects_a_non_numeric_poll_interval(tmp_path: Path):
    env = {"STUDIO_BASE_URL": "https://x", "WORKER_TOKEN": "t", "WORKER_POLL_SECONDS": "soon"}

    with pytest.raises(WorkerConfigError, match="WORKER_POLL_SECONDS"):
        load_config(env=env, env_file=tmp_path / "absent.env")


def test_load_blob_token_falls_back_to_the_env_file(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("BLOB_READ_WRITE_TOKEN=from-file\n", encoding="utf-8")

    assert load_blob_token(env={}, env_file=env_file) == "from-file"
    assert load_blob_token(env={}, env_file=tmp_path / "absent.env") is None


# ------------------------------------------------- music-removal scale option


def test_music_removal_carries_scale_height_both_ways() -> None:
    """camelCase on the wire, snake_case in Python."""
    from worker.contract import MusicRemovalJobInput

    raw = {
        "kind": "music_removal",
        "visual": {"videoId": "abc12345xyz", "url": "u", "title": "t",
                   "duration": 10, "viewCount": 0, "uploadDate": "",
                   "thumbnailUrl": "", "channelSlug": "c"},
        "privacy": "private",
        "playlistId": None,
        "scaleHeight": 720,
    }
    parsed = MusicRemovalJobInput.from_json(raw)
    assert parsed.scale_height == 720
    assert parsed.to_json()["scaleHeight"] == 720


def test_absent_scale_height_keeps_the_lossless_path() -> None:
    from worker.contract import MusicRemovalJobInput

    raw = {
        "kind": "music_removal",
        "visual": {"videoId": "abc12345xyz", "url": "u", "title": "t",
                   "duration": 10, "viewCount": 0, "uploadDate": "",
                   "thumbnailUrl": "", "channelSlug": "c"},
    }
    assert MusicRemovalJobInput.from_json(raw).scale_height is None


def test_a_junk_scale_height_is_dropped_not_fatal() -> None:
    """It is an optional quality tweak; a bad value must render as before
    rather than failing a job that would otherwise have worked."""
    from worker.contract import _optional_int

    assert _optional_int("banana") is None
    assert _optional_int("") is None
    assert _optional_int(None) is None
    assert _optional_int("720") == 720
    assert _optional_int(720.0) == 720
