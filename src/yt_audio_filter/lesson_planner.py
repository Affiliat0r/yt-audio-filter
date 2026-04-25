"""Weekly lesson-plan exporter (wishlist item C1).

A teacher fills a JSON describing a Mon-Fri (or any-length) week of
lessons. Each lesson maps to one render against
``run_overlay_from_surah_numbers``. This module:

  1. Parses + validates the JSON into a frozen ``WeeklyPlan`` of
     ``Lesson`` rows. Validation happens up front so the teacher sees
     every schema problem before the first network call.
  2. Runs each lesson sequentially through the existing overlay
     pipeline, calling user-supplied callbacks so a UI (Streamlit) can
     render a progress list. A single lesson failure is reported via
     ``on_lesson_error`` and the batch keeps going.

Render-only by design: uploading is a separate teacher decision and
mirrors the existing render-first / upload-later flow already in
:mod:`overlay_pipeline`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from .exceptions import OverlayError
from .logger import get_logger
from .metadata import load_metadata
from .surah_detector import get_surah_info

logger = get_logger()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Lesson:
    """One row of the weekly plan.

    ``surah_numbers`` and ``repeats`` must have the same length. The
    pipeline expands each ``(number, repeat)`` pair into ``repeat`` copies
    of ``number`` before calling ``run_overlay_from_surah_numbers`` (so a
    Lesson with ``surah_numbers=[1]`` and ``repeats=[10]`` renders the
    "10x Al-Fatiha" pattern).

    Override fields are optional; when omitted the WeeklyPlan-level
    defaults are used. Override fields validate (if present) at
    ``load_plan`` time so the teacher gets all errors up front.
    """

    day: str
    surah_numbers: List[int]
    repeats: List[int]
    title_override: Optional[str] = None
    reciter_override: Optional[str] = None
    visual_video_id_override: Optional[str] = None
    upscale_override: Optional[bool] = None


@dataclass(frozen=True)
class WeeklyPlan:
    """Validated weekly plan parsed from JSON."""

    week_of: str
    channel_metadata_template: Path
    default_reciter: str
    default_visual_video_id: str
    default_upscale: bool
    lessons: List[Lesson] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loader / validator
# ---------------------------------------------------------------------------


_WEEK_OF_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9_]+")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise OverlayError(message)


def _validate_surah_number(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OverlayError(f"{where} must be an integer surah number in 1..114")
    if value < 1 or value > 114:
        raise OverlayError(f"{where} = {value} is out of range; must be 1..114")
    return value


def _validate_repeat(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OverlayError(f"{where} must be an integer repeat count in 1..99")
    if value < 1 or value > 99:
        raise OverlayError(f"{where} = {value} is out of range; must be 1..99")
    return value


def _known_reciter_slugs() -> set[str]:
    """Snapshot of valid reciter slugs at load time."""
    from .quran_audio_source import list_reciters

    return {r.slug.lower() for r in list_reciters()}


def _known_visual_video_ids(cache_dir: Optional[Path] = None) -> set[str]:
    """Snapshot of valid cartoon-catalog video_ids at load time."""
    from .cartoon_catalog import DEFAULT_CACHE_DIR, list_videos

    videos = list_videos(cache_dir=cache_dir or DEFAULT_CACHE_DIR)
    return {v.video_id for v in videos}


def _parse_lesson(
    raw: dict,
    index: int,
    *,
    valid_reciters: set[str],
    valid_video_ids: set[str],
) -> Lesson:
    if not isinstance(raw, dict):
        raise OverlayError(f"Lesson {index + 1}: must be a JSON object")

    day = raw.get("day")
    _require(
        isinstance(day, str) and bool(day.strip()),
        f"Lesson {index + 1}: 'day' must be a non-empty string",
    )

    surah_numbers_raw = raw.get("surah_numbers")
    _require(
        isinstance(surah_numbers_raw, list) and len(surah_numbers_raw) > 0,
        f"Lesson {index + 1}: 'surah_numbers' must be a non-empty list",
    )
    repeats_raw = raw.get("repeats")
    _require(
        isinstance(repeats_raw, list) and len(repeats_raw) > 0,
        f"Lesson {index + 1}: 'repeats' must be a non-empty list",
    )
    if len(surah_numbers_raw) != len(repeats_raw):
        raise OverlayError(
            f"Lesson {index + 1}: surah_numbers and repeats must have the same length "
            f"(got {len(surah_numbers_raw)} vs {len(repeats_raw)})"
        )

    surah_numbers = [
        _validate_surah_number(n, f"Lesson {index + 1}: surah_numbers[{i}]")
        for i, n in enumerate(surah_numbers_raw)
    ]
    repeats = [
        _validate_repeat(r, f"Lesson {index + 1}: repeats[{i}]")
        for i, r in enumerate(repeats_raw)
    ]

    title_override = raw.get("title_override")
    if title_override is not None and not isinstance(title_override, str):
        raise OverlayError(
            f"Lesson {index + 1}: 'title_override' must be a string when set"
        )

    reciter_override = raw.get("reciter_override")
    if reciter_override is not None:
        if not isinstance(reciter_override, str) or not reciter_override.strip():
            raise OverlayError(
                f"Lesson {index + 1}: 'reciter_override' must be a non-empty string"
            )
        if reciter_override.lower() not in valid_reciters:
            raise OverlayError(
                f"Lesson {index + 1}: unknown reciter_override {reciter_override!r}",
                f"Valid slugs: {sorted(valid_reciters)}",
            )

    visual_video_id_override = raw.get("visual_video_id_override")
    if visual_video_id_override is not None:
        if (
            not isinstance(visual_video_id_override, str)
            or not visual_video_id_override.strip()
        ):
            raise OverlayError(
                f"Lesson {index + 1}: 'visual_video_id_override' must be a non-empty string"
            )
        if visual_video_id_override not in valid_video_ids:
            sample = ", ".join(sorted(valid_video_ids)[:10]) or "(catalog empty)"
            raise OverlayError(
                f"Lesson {index + 1}: unknown visual_video_id_override "
                f"{visual_video_id_override!r}",
                f"First 10 catalog ids: {sample}",
            )

    upscale_override = raw.get("upscale_override")
    if upscale_override is not None and not isinstance(upscale_override, bool):
        raise OverlayError(
            f"Lesson {index + 1}: 'upscale_override' must be a boolean when set"
        )

    return Lesson(
        day=day,
        surah_numbers=surah_numbers,
        repeats=repeats,
        title_override=title_override,
        reciter_override=reciter_override,
        visual_video_id_override=visual_video_id_override,
        upscale_override=upscale_override,
    )


def load_plan(path: Path) -> WeeklyPlan:
    """Parse + validate a weekly-plan JSON file.

    Validation order (fail-fast wins to keep the teacher iterating fast):

    1. File exists / parses as a JSON object.
    2. Top-level shape: ``week_of``, ``channel_metadata_template``,
       ``default_reciter``, ``default_visual_video_id``,
       ``default_upscale``, ``lessons`` (non-empty list).
    3. ``channel_metadata_template`` exists on disk and parses via
       :func:`metadata.load_metadata`.
    4. ``default_reciter`` resolves against
       :func:`quran_audio_source.list_reciters` (one catalog hit).
    5. ``default_visual_video_id`` exists in
       :func:`cartoon_catalog.list_videos` (one catalog hit, may
       trigger a scrape on first call).
    6. Per-lesson validation: shape, surah_numbers/repeats, override
       fields. Reciter/video-id overrides reuse the same snapshot
       sets from steps 4+5 — no extra catalog work per lesson.

    Raises:
        OverlayError: with a field-level message on any failure.
    """
    path = Path(path)
    if not path.exists():
        raise OverlayError(f"Lesson plan file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OverlayError(f"Invalid JSON in lesson plan: {path}", str(exc)) from exc

    if not isinstance(raw, dict):
        raise OverlayError(f"Lesson plan root must be a JSON object: {path}")

    week_of = raw.get("week_of")
    _require(
        isinstance(week_of, str) and bool(_WEEK_OF_PATTERN.match(week_of or "")),
        "'week_of' must be a YYYY-MM-DD date string",
    )
    assert isinstance(week_of, str)  # for type-checkers

    template_raw = raw.get("channel_metadata_template")
    _require(
        isinstance(template_raw, str) and bool(template_raw.strip()),
        "'channel_metadata_template' must be a non-empty string path",
    )
    assert isinstance(template_raw, str)
    template_path = Path(template_raw)
    if not template_path.is_absolute():
        template_path = (path.parent / template_path).resolve()
    if not template_path.exists():
        raise OverlayError(
            f"channel_metadata_template not found: {template_path}",
            f"Resolved from {template_raw!r} relative to {path.parent}.",
        )
    # Will raise OverlayError with a useful message on schema problems.
    load_metadata(template_path)

    default_reciter = raw.get("default_reciter")
    _require(
        isinstance(default_reciter, str) and bool(default_reciter.strip()),
        "'default_reciter' must be a non-empty string slug",
    )
    assert isinstance(default_reciter, str)
    valid_reciters = _known_reciter_slugs()
    if default_reciter.lower() not in valid_reciters:
        raise OverlayError(
            f"Unknown default_reciter slug: {default_reciter!r}",
            f"Valid slugs: {sorted(valid_reciters)}",
        )

    default_visual = raw.get("default_visual_video_id")
    _require(
        isinstance(default_visual, str) and bool(default_visual.strip()),
        "'default_visual_video_id' must be a non-empty string",
    )
    assert isinstance(default_visual, str)
    valid_video_ids = _known_visual_video_ids()
    if default_visual not in valid_video_ids:
        sample = ", ".join(sorted(valid_video_ids)[:10]) or "(catalog empty)"
        raise OverlayError(
            f"Unknown default_visual_video_id: {default_visual!r}",
            f"Not found in cartoon catalog. First 10 ids: {sample}",
        )

    default_upscale = raw.get("default_upscale", False)
    if not isinstance(default_upscale, bool):
        raise OverlayError("'default_upscale' must be a boolean")

    lessons_raw = raw.get("lessons")
    _require(
        isinstance(lessons_raw, list) and len(lessons_raw) > 0,
        "'lessons' must be a non-empty list",
    )
    assert isinstance(lessons_raw, list)
    lessons = [
        _parse_lesson(
            entry,
            i,
            valid_reciters=valid_reciters,
            valid_video_ids=valid_video_ids,
        )
        for i, entry in enumerate(lessons_raw)
    ]

    return WeeklyPlan(
        week_of=week_of,
        channel_metadata_template=template_path,
        default_reciter=default_reciter,
        default_visual_video_id=default_visual,
        default_upscale=default_upscale,
        lessons=lessons,
    )


# ---------------------------------------------------------------------------
# Output naming
# ---------------------------------------------------------------------------


def _surah_tag_for_lesson(lesson: Lesson) -> str:
    """Filename tag for a lesson: leading surah's PascalCase tag, plus
    ``_+Nmore`` when the lesson has more than one surah_number entry.

    Mirrors the convention used by ``_surah_numbers_output_filename`` in
    :mod:`overlay_pipeline` so a teacher reading both filenames sees the
    same shape.
    """
    first = get_surah_info(lesson.surah_numbers[0])
    head_repeat = lesson.repeats[0]
    head = f"{first.tag}-x{head_repeat}" if head_repeat > 1 else first.tag
    if len(lesson.surah_numbers) == 1:
        return head
    extras = len(lesson.surah_numbers) - 1
    return f"{head}_+{extras}more"


def _output_filename(week_of: str, lesson: Lesson) -> str:
    """``<week_of>_<day>_<surah_tag>.mp4`` with non-alphanumeric stripped
    from ``day`` so a free-form value like "Mon (revision)" doesn't break
    on Windows path rules."""
    day_safe = _FILENAME_SAFE.sub("", lesson.day) or "Day"
    return f"{week_of}_{day_safe}_{_surah_tag_for_lesson(lesson)}.mp4"


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------


def _expand_surahs(lesson: Lesson) -> List[int]:
    """``[1, 95]`` + ``[3, 1]`` -> ``[1, 1, 1, 95]``."""
    expanded: List[int] = []
    for n, r in zip(lesson.surah_numbers, lesson.repeats):
        expanded.extend([n] * r)
    return expanded


def render_plan(
    plan: WeeklyPlan,
    output_dir: Path,
    cache_dir: Path,
    *,
    on_lesson_start: Optional[Callable[[Lesson, int, int], None]] = None,
    on_lesson_done: Optional[Callable[[Lesson, Path], None]] = None,
    on_lesson_error: Optional[Callable[[Lesson, Exception], None]] = None,
) -> List[Path]:
    """Run each lesson sequentially through ``run_overlay_from_surah_numbers``.

    The pipeline call is *lazy-imported* inside the function body to
    avoid a circular import — :mod:`overlay_pipeline` already imports
    a sibling module hierarchy at module scope.

    Callbacks (all optional, all synchronous):

    * ``on_lesson_start(lesson, index_zero_based, total)`` — fires
      before each lesson's render.
    * ``on_lesson_done(lesson, output_path)`` — fires after a
      successful render.
    * ``on_lesson_error(lesson, exception)`` — fires on a per-lesson
      failure; the batch then continues with the next lesson rather
      than aborting (matches ``run_overlay_batch`` semantics).

    Returns:
        Paths of MP4s that rendered successfully, in lesson order.
        Failed lessons are absent from this list and reported via
        ``on_lesson_error``.
    """
    from .overlay_pipeline import run_overlay_from_surah_numbers

    output_dir = Path(output_dir)
    cache_dir = Path(cache_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    metadata = load_metadata(plan.channel_metadata_template)

    succeeded: List[Path] = []
    total = len(plan.lessons)
    for i, lesson in enumerate(plan.lessons):
        out_path = output_dir / _output_filename(plan.week_of, lesson)
        reciter_slug = lesson.reciter_override or plan.default_reciter
        visual_video_id = (
            lesson.visual_video_id_override or plan.default_visual_video_id
        )
        upscale = (
            lesson.upscale_override
            if lesson.upscale_override is not None
            else plan.default_upscale
        )
        expanded = _expand_surahs(lesson)

        logger.info(
            "Lesson %d/%d (%s): %d surah(s) -> %s",
            i + 1,
            total,
            lesson.day,
            len(expanded),
            out_path.name,
        )

        if on_lesson_start is not None:
            try:
                on_lesson_start(lesson, i, total)
            except Exception:
                logger.exception("on_lesson_start callback raised; ignoring")

        try:
            result = run_overlay_from_surah_numbers(
                surah_numbers=expanded,
                reciter_slug=reciter_slug,
                visual_video_id=visual_video_id,
                metadata=metadata,
                output_path=out_path,
                cache_dir=cache_dir,
                upscale=upscale,
                upload=False,
            )
        except Exception as exc:  # noqa: BLE001 — report and continue
            logger.error("Lesson %d (%s) failed: %s", i + 1, lesson.day, exc)
            if on_lesson_error is not None:
                try:
                    on_lesson_error(lesson, exc)
                except Exception:
                    logger.exception("on_lesson_error callback raised; ignoring")
            continue

        succeeded.append(result.output_path)
        if on_lesson_done is not None:
            try:
                on_lesson_done(lesson, result.output_path)
            except Exception:
                logger.exception("on_lesson_done callback raised; ignoring")

    logger.info("Lesson plan complete: %d/%d succeeded", len(succeeded), total)
    return succeeded
