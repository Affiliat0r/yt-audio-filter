"""Unit tests for the weekly-lesson-plan exporter (wishlist item C1).

Mirrors the mock patterns from ``test_overlay_pipeline_numbers.py``:
``cartoon_catalog.list_videos`` and ``quran_audio_source.list_reciters``
are stubbed so validation is offline; the pipeline call is patched at
its lazy-import site so the batch runner never actually downloads or
renders.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from yt_audio_filter.cartoon_catalog import CatalogVideo
from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.lesson_planner import (
    Lesson,
    WeeklyPlan,
    load_plan,
    render_plan,
)
from yt_audio_filter.overlay_pipeline import OverlayResult
from yt_audio_filter.quran_audio_source import Reciter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reciter(slug: str = "alafasy", display: str = "Mishary Rashid Alafasy") -> Reciter:
    return Reciter(
        slug=slug,
        display_name=display,
        sample_url="https://example.com/sample.mp3",
        url_pattern=f"https://example.com/{slug}/{{num:03d}}.mp3",
    )


def _catalog_video(video_id: str, title: str = "Cartoon") -> CatalogVideo:
    return CatalogVideo(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=title,
        duration=900,
        view_count=1000,
        upload_date="20260101",
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        channel_slug="toys",
    )


_DEFAULT_RECITERS = (
    _reciter("alafasy", "Mishary Rashid Alafasy"),
    _reciter("sudais", "Abdur-Rahman As-Sudais"),
)
_DEFAULT_VIDEOS = (
    _catalog_video("vid-default", "Default Visual"),
    _catalog_video("vid-saturday", "Saturday Visual"),
)


def _write_metadata(tmp_path: Path) -> Path:
    """Write a minimal-valid OverlayMetadata JSON next to the plan."""
    meta = tmp_path / "meta.json"
    meta.write_text(
        json.dumps(
            {
                "title": "$detected_surah - $reciter",
                "description_template": "Surahs: $detected_surah",
                "tags": ["quran"],
            }
        ),
        encoding="utf-8",
    )
    return meta


def _plan_dict(metadata_template: Path, lessons: List[dict]) -> dict:
    return {
        "week_of": "2026-05-04",
        "channel_metadata_template": str(metadata_template),
        "default_reciter": "alafasy",
        "default_visual_video_id": "vid-default",
        "default_upscale": False,
        "lessons": lessons,
    }


def _write_plan(tmp_path: Path, lessons: List[dict] | None = None) -> Path:
    if lessons is None:
        lessons = [
            {"day": "Monday", "surah_numbers": [1, 112], "repeats": [3, 2]},
            {"day": "Tuesday", "surah_numbers": [95], "repeats": [10]},
        ]
    metadata = _write_metadata(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_plan_dict(metadata, lessons)), encoding="utf-8"
    )
    return plan_path


def _patch_catalog():
    """Returns a context-manager that patches both the reciter manifest
    and the cartoon catalog so loader validation runs offline."""
    return patch.multiple(
        "yt_audio_filter.lesson_planner",
        _known_reciter_slugs=lambda: {r.slug for r in _DEFAULT_RECITERS},
        _known_visual_video_ids=lambda *args, **kwargs: {
            v.video_id for v in _DEFAULT_VIDEOS
        },
    )


# ---------------------------------------------------------------------------
# load_plan
# ---------------------------------------------------------------------------


def test_load_plan_happy_path(tmp_path: Path) -> None:
    plan_path = _write_plan(tmp_path)
    with _patch_catalog():
        plan = load_plan(plan_path)

    assert isinstance(plan, WeeklyPlan)
    assert plan.week_of == "2026-05-04"
    assert plan.default_reciter == "alafasy"
    assert plan.default_visual_video_id == "vid-default"
    assert plan.default_upscale is False
    assert len(plan.lessons) == 2

    monday = plan.lessons[0]
    assert isinstance(monday, Lesson)
    assert monday.day == "Monday"
    assert monday.surah_numbers == [1, 112]
    assert monday.repeats == [3, 2]
    assert monday.reciter_override is None
    assert monday.visual_video_id_override is None
    assert monday.upscale_override is None

    tuesday = plan.lessons[1]
    assert tuesday.surah_numbers == [95]
    assert tuesday.repeats == [10]


def test_load_plan_validates_surah_numbers_repeats_length(tmp_path: Path) -> None:
    plan_path = _write_plan(
        tmp_path,
        [{"day": "Monday", "surah_numbers": [1, 2, 3], "repeats": [1, 1]}],
    )
    with _patch_catalog():
        with pytest.raises(OverlayError, match="same length"):
            load_plan(plan_path)


def test_load_plan_unknown_reciter_raises(tmp_path: Path) -> None:
    metadata = _write_metadata(tmp_path)
    plan_path = tmp_path / "plan.json"
    payload = _plan_dict(
        metadata,
        [{"day": "Monday", "surah_numbers": [1], "repeats": [1]}],
    )
    payload["default_reciter"] = "not-a-reciter"
    plan_path.write_text(json.dumps(payload), encoding="utf-8")

    with _patch_catalog():
        with pytest.raises(OverlayError, match="Unknown default_reciter"):
            load_plan(plan_path)


def test_load_plan_unknown_video_id_raises(tmp_path: Path) -> None:
    metadata = _write_metadata(tmp_path)
    plan_path = tmp_path / "plan.json"
    payload = _plan_dict(
        metadata,
        [{"day": "Monday", "surah_numbers": [1], "repeats": [1]}],
    )
    payload["default_visual_video_id"] = "not-in-catalog"
    plan_path.write_text(json.dumps(payload), encoding="utf-8")

    with _patch_catalog():
        with pytest.raises(OverlayError, match="default_visual_video_id"):
            load_plan(plan_path)


def test_load_plan_missing_metadata_template_raises(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    payload = _plan_dict(
        Path("does-not-exist.json"),
        [{"day": "Monday", "surah_numbers": [1], "repeats": [1]}],
    )
    plan_path.write_text(json.dumps(payload), encoding="utf-8")

    with _patch_catalog():
        with pytest.raises(OverlayError, match="channel_metadata_template not found"):
            load_plan(plan_path)


def test_load_plan_invalid_surah_number_raises(tmp_path: Path) -> None:
    plan_path = _write_plan(
        tmp_path,
        [{"day": "Monday", "surah_numbers": [200], "repeats": [1]}],
    )
    with _patch_catalog():
        with pytest.raises(OverlayError, match="1..114"):
            load_plan(plan_path)


def test_load_plan_invalid_repeat_raises(tmp_path: Path) -> None:
    plan_path = _write_plan(
        tmp_path,
        [{"day": "Monday", "surah_numbers": [1], "repeats": [0]}],
    )
    with _patch_catalog():
        with pytest.raises(OverlayError, match="1..99"):
            load_plan(plan_path)


def test_load_plan_unknown_lesson_override_raises(tmp_path: Path) -> None:
    plan_path = _write_plan(
        tmp_path,
        [
            {
                "day": "Monday",
                "surah_numbers": [1],
                "repeats": [1],
                "reciter_override": "ghost-reciter",
            }
        ],
    )
    with _patch_catalog():
        with pytest.raises(OverlayError, match="reciter_override"):
            load_plan(plan_path)


def test_load_plan_invalid_week_of_raises(tmp_path: Path) -> None:
    metadata = _write_metadata(tmp_path)
    plan_path = tmp_path / "plan.json"
    payload = _plan_dict(
        metadata,
        [{"day": "Monday", "surah_numbers": [1], "repeats": [1]}],
    )
    payload["week_of"] = "next-monday"
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    with _patch_catalog():
        with pytest.raises(OverlayError, match="week_of"):
            load_plan(plan_path)


# ---------------------------------------------------------------------------
# render_plan
# ---------------------------------------------------------------------------


def _build_plan(lessons: List[Lesson], metadata_template: Path) -> WeeklyPlan:
    return WeeklyPlan(
        week_of="2026-05-04",
        channel_metadata_template=metadata_template,
        default_reciter="alafasy",
        default_visual_video_id="vid-default",
        default_upscale=False,
        lessons=lessons,
    )


def _fake_pipeline(output_path: Path, **kwargs) -> OverlayResult:
    """Default pipeline-mock side effect: writes a marker MP4 + returns result."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"\x00" * 16)
    return OverlayResult(
        output_path=output_path,
        uploaded_video_id=None,
        audio_url="",
        video_url="https://www.youtube.com/watch?v=vid-default",
    )


def test_render_plan_calls_pipeline_once_per_lesson(tmp_path: Path) -> None:
    metadata = _write_metadata(tmp_path)
    lessons = [
        Lesson(day="Monday", surah_numbers=[1, 112], repeats=[3, 2]),
        Lesson(day="Tuesday", surah_numbers=[95], repeats=[10], reciter_override="sudais"),
        Lesson(
            day="Saturday",
            surah_numbers=[36],
            repeats=[1],
            visual_video_id_override="vid-saturday",
            upscale_override=True,
        ),
    ]
    plan = _build_plan(lessons, metadata)

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers"
    ) as mock_pipeline:
        mock_pipeline.side_effect = lambda **kw: _fake_pipeline(**kw)

        outputs = render_plan(
            plan,
            output_dir=tmp_path / "out",
            cache_dir=tmp_path / "cache",
        )

    assert mock_pipeline.call_count == 3
    assert len(outputs) == 3

    calls = mock_pipeline.call_args_list

    # Lesson 1: defaults applied (reciter, visual, upscale).
    kw0 = calls[0].kwargs
    assert kw0["surah_numbers"] == [1, 1, 1, 112, 112]
    assert kw0["reciter_slug"] == "alafasy"
    assert kw0["visual_video_id"] == "vid-default"
    assert kw0["upscale"] is False
    assert kw0["upload"] is False

    # Lesson 2: reciter override, defaults for visual + upscale.
    kw1 = calls[1].kwargs
    assert kw1["surah_numbers"] == [95] * 10
    assert kw1["reciter_slug"] == "sudais"
    assert kw1["visual_video_id"] == "vid-default"
    assert kw1["upscale"] is False

    # Lesson 3: visual + upscale override, default reciter.
    kw2 = calls[2].kwargs
    assert kw2["surah_numbers"] == [36]
    assert kw2["reciter_slug"] == "alafasy"
    assert kw2["visual_video_id"] == "vid-saturday"
    assert kw2["upscale"] is True


def test_render_plan_callbacks_fire(tmp_path: Path) -> None:
    metadata = _write_metadata(tmp_path)
    lessons = [
        Lesson(day="Monday", surah_numbers=[1], repeats=[1]),
        Lesson(day="Tuesday", surah_numbers=[112], repeats=[1]),
    ]
    plan = _build_plan(lessons, metadata)

    started: List[tuple[str, int, int]] = []
    finished: List[tuple[str, str]] = []
    errors: List[tuple[str, str]] = []

    def on_start(lesson: Lesson, idx: int, total: int) -> None:
        started.append((lesson.day, idx, total))

    def on_done(lesson: Lesson, path: Path) -> None:
        finished.append((lesson.day, path.name))

    def on_error(lesson: Lesson, exc: Exception) -> None:
        errors.append((lesson.day, str(exc)))

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers"
    ) as mock_pipeline:
        mock_pipeline.side_effect = lambda **kw: _fake_pipeline(**kw)

        render_plan(
            plan,
            output_dir=tmp_path / "out",
            cache_dir=tmp_path / "cache",
            on_lesson_start=on_start,
            on_lesson_done=on_done,
            on_lesson_error=on_error,
        )

    assert started == [("Monday", 0, 2), ("Tuesday", 1, 2)]
    assert [day for day, _ in finished] == ["Monday", "Tuesday"]
    assert errors == []


def test_render_plan_continues_on_lesson_error(tmp_path: Path) -> None:
    metadata = _write_metadata(tmp_path)
    lessons = [
        Lesson(day="Monday", surah_numbers=[1], repeats=[1]),
        Lesson(day="Tuesday", surah_numbers=[112], repeats=[1]),
        Lesson(day="Wednesday", surah_numbers=[113], repeats=[1]),
    ]
    plan = _build_plan(lessons, metadata)

    boom = RuntimeError("synthetic render failure")

    def _side_effect(**kw) -> OverlayResult:
        if kw["surah_numbers"] == [112]:
            raise boom
        return _fake_pipeline(**kw)

    error_log: List[tuple[str, Exception]] = []
    done_log: List[str] = []

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers"
    ) as mock_pipeline:
        mock_pipeline.side_effect = _side_effect

        outputs = render_plan(
            plan,
            output_dir=tmp_path / "out",
            cache_dir=tmp_path / "cache",
            on_lesson_done=lambda lsn, p: done_log.append(lsn.day),
            on_lesson_error=lambda lsn, exc: error_log.append((lsn.day, exc)),
        )

    # Pipeline was called for all 3 lessons.
    assert mock_pipeline.call_count == 3
    # Lessons 1 + 3 succeeded; Tuesday absent from outputs.
    assert [p.name for p in outputs] == [
        "2026-05-04_Monday_AlFatiha.mp4",
        "2026-05-04_Wednesday_AlFalaq.mp4",
    ]
    assert done_log == ["Monday", "Wednesday"]
    # Failed lesson reported via on_error.
    assert len(error_log) == 1
    assert error_log[0][0] == "Tuesday"
    assert error_log[0][1] is boom


def test_render_plan_output_paths_named_correctly(tmp_path: Path) -> None:
    metadata = _write_metadata(tmp_path)
    lessons = [
        # Single surah, no repeat -> "AlFatiha".
        Lesson(day="Monday", surah_numbers=[1], repeats=[1]),
        # Single surah, repeat=10 -> "AlFatiha-x10".
        Lesson(day="Tuesday", surah_numbers=[1], repeats=[10]),
        # Multi-surah -> "AlFatiha_+1more" (head + count of remaining entries).
        Lesson(day="Wednesday", surah_numbers=[1, 112], repeats=[1, 1]),
        # Multi-surah with head repeat=3 -> "AlFatiha-x3_+2more".
        Lesson(
            day="Thursday",
            surah_numbers=[1, 112, 113],
            repeats=[3, 2, 2],
        ),
        # Free-form day name with non-alphanumerics gets sanitized.
        Lesson(day="Sat (revision)", surah_numbers=[36], repeats=[1]),
    ]
    plan = _build_plan(lessons, metadata)

    captured: List[Path] = []

    with patch(
        "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers"
    ) as mock_pipeline:
        def _capture(**kw):
            captured.append(kw["output_path"])
            return _fake_pipeline(**kw)

        mock_pipeline.side_effect = _capture
        render_plan(
            plan,
            output_dir=tmp_path / "out",
            cache_dir=tmp_path / "cache",
        )

    names = [p.name for p in captured]
    assert names == [
        "2026-05-04_Monday_AlFatiha.mp4",
        "2026-05-04_Tuesday_AlFatiha-x10.mp4",
        "2026-05-04_Wednesday_AlFatiha_+1more.mp4",
        "2026-05-04_Thursday_AlFatiha-x3_+2more.mp4",
        "2026-05-04_Satrevision_YaSin.mp4",
    ]
