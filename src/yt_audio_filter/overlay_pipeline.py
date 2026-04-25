"""Orchestration for the yt-quran-overlay workflow.

Four stages: download video-only, download audio-only, render, optional upload.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .audio_concat import concat_audio
from .channel_discovery import Candidate, fetch_candidates
from .exceptions import OverlayError
from .ffmpeg_overlay import render_overlay
from .logger import get_logger
from .metadata import OverlayMetadata
from .pair_selector import PairChoice, select_pairs
from .pair_state import DEFAULT_STATE_PATH, load_state, save_state
from .surah_detector import ReciterMatch, SurahMatch, detect_reciter, detect_surah, get_surah_info
from .surah_resolver import resolve_surahs
from .upscale import get_or_create_upscaled
from .youtube import download_stream, extract_video_id
from .yt_metadata import YouTubeMetadata, fetch_yt_metadata

logger = get_logger()


@dataclass
class OverlayResult:
    output_path: Path
    uploaded_video_id: Optional[str] = None
    audio_url: Optional[str] = None
    video_url: Optional[str] = None


def _output_filename(audio_url: str, video_url: str) -> str:
    audio_id = extract_video_id(audio_url)
    video_id = extract_video_id(video_url)
    return f"{audio_id}_{video_id}.mp4"


def _build_auto_vars(
    audio_meta: YouTubeMetadata,
    surah: Optional[SurahMatch],
    reciter: Optional[ReciterMatch],
) -> dict:
    """Collect template variables from the audio URL's YouTube metadata.

    Keys:
      audio_title, audio_channel, audio_uploader — raw YT fields.
      detected_surah, surah_tag, surah_number — from the surah detector
        (empty strings when nothing matched, so templates don't blow up).
      reciter, reciter_tag — from the reciter detector when the title names
        a known qari; falls back to the YouTube channel otherwise.
    """
    if reciter is not None:
        reciter_name = reciter.name
        reciter_tag = reciter.tag
    else:
        reciter_name = audio_meta.channel or audio_meta.uploader or ""
        reciter_tag = "".join(p.capitalize() for p in reciter_name.split() if p)
    return {
        "audio_title": audio_meta.title,
        "audio_channel": audio_meta.channel,
        "audio_uploader": audio_meta.uploader,
        "detected_surah": surah.name if surah else "",
        "surah_tag": surah.tag if surah else "",
        "surah_number": str(surah.number) if (surah and surah.number) else "",
        "reciter": reciter_name,
        "reciter_tag": reciter_tag,
    }


def run_overlay(
    video_url: str,
    audio_url: str,
    metadata: OverlayMetadata,
    cache_dir: Path,
    output_dir: Path,
    resolution: Tuple[int, int] = (1920, 1080),
    max_duration: Optional[float] = 7200.0,
    force: bool = False,
    upload: bool = False,
    cookies_from_browser: Optional[str] = None,
    proxy: Optional[str] = None,
    upscale: bool = False,
) -> OverlayResult:
    """Run the 4-stage overlay pipeline."""
    cache_dir = Path(cache_dir)
    output_dir = Path(output_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_name = _output_filename(audio_url, video_url)
    output_path = output_dir / output_name

    if output_path.exists() and not force:
        raise OverlayError(
            f"Output already exists: {output_path}",
            "Pass --force to overwrite.",
        )

    logger.info("[1/4] Downloading visual video (video-only stream)...")
    video_path = download_stream(
        url=video_url,
        output_dir=cache_dir,
        mode="video-only",
        cookies_from_browser=cookies_from_browser,
        proxy=proxy,
    )

    logger.info("[2/4] Downloading Quran audio (audio-only stream)...")
    audio_path = download_stream(
        url=audio_url,
        output_dir=cache_dir,
        mode="audio-only",
        cookies_from_browser=cookies_from_browser,
        proxy=proxy,
    )

    if upscale:
        logger.info("[2.5/4] Upscaling visual via Real-ESRGAN (cached)...")
        visual_video_id = extract_video_id(video_url)
        video_path = get_or_create_upscaled(video_path, visual_video_id, cache_dir)

    logger.info("[3/4] Rendering overlay (two-pass loudnorm + loop + mux)...")
    logo_arg: Optional[Tuple[Path, str]] = None
    if metadata.logo_path is not None:
        if not metadata.logo_path.exists():
            raise OverlayError(f"Logo file not found: {metadata.logo_path}")
        logo_arg = (metadata.logo_path, metadata.logo_position)
    elif upload:
        raise OverlayError(
            "Upload requested but no logo configured",
            "Every uploaded video must carry the channel logo. Set `logo_path` in "
            "the metadata JSON or pass --logo on the CLI. To render without a logo "
            "for testing, drop the --upload flag.",
        )
    else:
        logger.warning("No logo configured; rendering without channel branding")

    render_overlay(
        video_path=video_path,
        audio_path=audio_path,
        output_path=output_path,
        resolution=resolution,
        logo=logo_arg,
        max_duration=max_duration,
        force=force,
    )

    uploaded_id: Optional[str] = None
    if upload:
        logger.info("Fetching audio URL metadata for description rendering...")
        audio_meta = fetch_yt_metadata(audio_url)
        surah = detect_surah(audio_meta.title) or detect_surah(audio_meta.description)
        reciter = detect_reciter(audio_meta.title) or detect_reciter(audio_meta.description)

        # Guard: if the template depends on $detected_surah but we couldn't
        # match one, abort the upload rather than publishing a broken title
        # like " — Reciter | Channel". Caller (batch mode) should catch this
        # and try the next pair.
        template_uses_surah = "$detected_surah" in (metadata.description_template or "") or \
                              "$detected_surah" in (metadata.title or "") or \
                              "$surah_tag" in (metadata.description_template or "") or \
                              "$surah_tag" in (metadata.title or "")
        if surah is None and template_uses_surah:
            raise OverlayError(
                f"Could not detect a surah from the audio URL's metadata",
                f"Audio title: {audio_meta.title!r}. The description/title "
                f"template references $detected_surah / $surah_tag, so uploading "
                f"would produce a broken title. Skipping this pair.",
            )

        if surah:
            logger.info(f"Detected surah: {surah.name} (#{surah.number or '-'})")
        else:
            logger.warning(
                "No surah matched in the audio URL's metadata; description "
                "placeholders for $detected_surah will be empty."
            )
        if reciter:
            logger.info(f"Detected reciter: {reciter.name}")
        else:
            logger.info(
                f"No known reciter matched; falling back to channel name "
                f"({audio_meta.channel!r})"
            )
        auto_vars = _build_auto_vars(audio_meta, surah, reciter)
        title = metadata.render_title(extra_vars=auto_vars)
        description = metadata.render_description(extra_vars=auto_vars)
        logger.info(f"Resolved title: {title}")

        logger.info("[4/4] Uploading to YouTube...")
        from .uploader import upload_with_explicit_metadata

        uploaded_id = upload_with_explicit_metadata(
            video_path=output_path,
            title=title,
            description=description,
            tags=metadata.tags,
            category_id=metadata.category_id,
            privacy=metadata.privacy_status,
        )
    else:
        logger.info("[4/4] Upload skipped (no --upload flag)")

    return OverlayResult(
        output_path=output_path,
        uploaded_video_id=uploaded_id,
        audio_url=audio_url,
        video_url=video_url,
    )


def run_overlay_batch(
    audio_channel: str,
    video_channel: str,
    metadata: OverlayMetadata,
    cache_dir: Path,
    output_dir: Path,
    count: int = 1,
    resolution: Tuple[int, int] = (1920, 1080),
    max_duration: Optional[float] = 7200.0,
    force: bool = False,
    upload: bool = False,
    cookies_from_browser: Optional[str] = None,
    proxy: Optional[str] = None,
    state_path: Path = DEFAULT_STATE_PATH,
    max_candidates_per_channel: int = 200,
    upscale: bool = False,
) -> List[OverlayResult]:
    """Discover candidates, pair by duration, render N videos in sequence.

    After each successful render (and optional upload), the pair is appended to
    the state file so later runs skip it. A render failure is logged and the
    batch continues with the next pair.
    """
    logger.info(f"Starting batch run ({count} video(s))")
    audio_cands = fetch_candidates(audio_channel, max_videos=max_candidates_per_channel)
    visual_cands = fetch_candidates(video_channel, max_videos=max_candidates_per_channel)

    state = load_state(state_path)
    processed = {(p.audio_id, p.video_id) for p in state.pairs}
    logger.info(f"Loaded {len(processed)} previously-processed pair(s) from state")

    picks: List[PairChoice] = select_pairs(
        audio_candidates=audio_cands,
        video_candidates=visual_cands,
        count=count,
        processed_pair_set=processed,
    )

    results: List[OverlayResult] = []
    for i, pick in enumerate(picks, start=1):
        logger.info(
            f"=== Pair {i}/{len(picks)}: audio={pick.audio.video_id} "
            f"visual={pick.visual.video_id} slack={pick.duration_slack:+d}s ==="
        )
        try:
            result = run_overlay(
                video_url=pick.visual.url,
                audio_url=pick.audio.url,
                metadata=metadata,
                cache_dir=cache_dir,
                output_dir=output_dir,
                resolution=resolution,
                max_duration=max_duration,
                force=force,
                upload=upload,
                cookies_from_browser=cookies_from_browser,
                proxy=proxy,
                upscale=upscale,
            )
        except Exception as e:
            logger.error(f"Pair {i} failed: {e}. Continuing with next pair.")
            continue

        state.add(
            audio_id=pick.audio.video_id,
            video_id=pick.visual.video_id,
            uploaded_video_id=result.uploaded_video_id,
            output_path=str(result.output_path),
        )
        save_state(state, state_path)
        results.append(result)

    if not results:
        raise OverlayError("Batch produced no videos; every pair failed or was skipped")
    logger.info(f"Batch complete: {len(results)}/{count} video(s) produced")
    return results


def _slug_tag(name: str) -> str:
    """Mirror surah_detector._slug_tag for joining surah tags in output naming."""
    import re
    parts = re.split(r"[\s\-]+", name.strip())
    return "".join(p[:1].upper() + p[1:].lower() for p in parts if p)


def _surah_output_filename(resolved: List[Candidate], video_id: str) -> str:
    """`AtTin_<vid>.mp4` for one surah; `AtTin_+2more_<vid>.mp4` for several."""
    from .surah_detector import detect_surah

    first_match = detect_surah(resolved[0].title)
    first_tag = first_match.tag if first_match else _slug_tag(resolved[0].title.split()[0])
    if len(resolved) == 1:
        return f"{first_tag}_{video_id}.mp4"
    return f"{first_tag}_+{len(resolved) - 1}more_{video_id}.mp4"


def _build_surah_auto_vars(
    resolved: List[Candidate],
    visual: Candidate,
    audio_meta_first: YouTubeMetadata,
) -> dict:
    """Auto-vars for surah mode: joined names + tags, reciter from first audio."""
    from .surah_detector import detect_reciter, detect_surah

    matches = [detect_surah(c.title) for c in resolved]
    canonical_names = [m.name if m else c.title for m, c in zip(matches, resolved)]
    canonical_tags = [m.tag if m else _slug_tag(c.title.split()[0]) for m, c in zip(matches, resolved)]

    detected_surah = " + ".join(canonical_names)
    surah_tag = "".join(canonical_tags)

    reciter = detect_reciter(audio_meta_first.title) or detect_reciter(audio_meta_first.description)
    if reciter is not None:
        reciter_name = reciter.name
        reciter_tag = reciter.tag
    else:
        reciter_name = audio_meta_first.channel or audio_meta_first.uploader or ""
        reciter_tag = "".join(p.capitalize() for p in reciter_name.split() if p)

    return {
        "audio_title": " + ".join(c.title for c in resolved),
        "audio_channel": audio_meta_first.channel,
        "audio_uploader": audio_meta_first.uploader,
        "detected_surah": detected_surah,
        "surah_tag": surah_tag,
        "surah_number": "",
        "surah_count": str(len(resolved)),
        "reciter": reciter_name,
        "reciter_tag": reciter_tag,
        "visual_title": visual.title,
    }


def run_overlay_surahs(
    surah_names: List[str],
    audio_channel: str,
    video_channel: str,
    metadata: OverlayMetadata,
    cache_dir: Path,
    output_dir: Path,
    resolution: Tuple[int, int] = (1920, 1080),
    max_duration: Optional[float] = 7200.0,
    force: bool = False,
    upload: bool = False,
    cookies_from_browser: Optional[str] = None,
    proxy: Optional[str] = None,
    max_candidates_per_channel: int = 200,
    upscale: bool = False,
) -> OverlayResult:
    """Resolve surah names → audio URLs, concat audios, render against the longest visual.

    Pipeline:
      1. Resolve every requested surah on the audio channel (fail fast on miss).
      2. Download audio-only stream for each resolved candidate.
      3. Concatenate the downloaded audios into one file.
      4. Pick the longest visual candidate from the video channel.
      5. Download the visual (video-only) and render with the loop already
         loudnorming the concatenated audio.
      6. Optional upload, with `$detected_surah` rendered as
         `"Al-Fatiha + At-Tin + Ar-Rahman"` and `$surah_tag` as the
         concatenated PascalCase tags.
    """
    cache_dir = Path(cache_dir)
    output_dir = Path(output_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not surah_names:
        raise OverlayError("No surahs supplied to run_overlay_surahs")

    logger.info(f"[1/5] Resolving {len(surah_names)} surah(s) on audio channel...")
    resolved = resolve_surahs(
        surah_names=surah_names,
        audio_channel_url=audio_channel,
        max_candidates=max_candidates_per_channel,
    )
    for s, c in zip(surah_names, resolved):
        logger.info(f"  {s!r} → {c.title!r} ({c.duration}s)")

    logger.info("[2/5] Downloading audio for each surah...")
    audio_paths: List[Path] = []
    for c in resolved:
        path = download_stream(
            url=c.url,
            output_dir=cache_dir,
            mode="audio-only",
            cookies_from_browser=cookies_from_browser,
            proxy=proxy,
        )
        audio_paths.append(path)

    logger.info(f"[3/5] Concatenating {len(audio_paths)} audio file(s)...")
    if len(audio_paths) == 1:
        concatenated = audio_paths[0]
        logger.info("Single surah; skipping concat.")
    else:
        joined_tag = "_".join(c.video_id for c in resolved)
        concatenated = cache_dir / f"concat_{joined_tag}.m4a"
        if not (concatenated.exists() and concatenated.stat().st_size > 0):
            concat_audio(audio_paths, concatenated)
        else:
            logger.info(f"Using cached concat: {concatenated.name}")

    logger.info("[4/5] Selecting longest visual from video channel...")
    visuals = fetch_candidates(video_channel, max_videos=max_candidates_per_channel)
    visual = max(visuals, key=lambda v: v.duration)
    total_audio_duration = sum(c.duration for c in resolved)
    if visual.duration < total_audio_duration:
        logger.info(
            f"Visual {visual.duration}s < audio {total_audio_duration}s — "
            f"render will loop the visual {total_audio_duration / max(visual.duration, 1):.1f}x"
        )
    visual_path = download_stream(
        url=visual.url,
        output_dir=cache_dir,
        mode="video-only",
        cookies_from_browser=cookies_from_browser,
        proxy=proxy,
    )
    if upscale:
        logger.info("Upscaling visual via Real-ESRGAN (cached)...")
        visual_path = get_or_create_upscaled(visual_path, visual.video_id, cache_dir)

    output_name = _surah_output_filename(resolved, visual.video_id)
    output_path = output_dir / output_name
    if output_path.exists() and not force:
        raise OverlayError(
            f"Output already exists: {output_path}",
            "Pass --force to overwrite.",
        )

    logger.info(f"[5/5] Rendering overlay → {output_name}")
    logo_arg: Optional[Tuple[Path, str]] = None
    if metadata.logo_path is not None:
        if not metadata.logo_path.exists():
            raise OverlayError(f"Logo file not found: {metadata.logo_path}")
        logo_arg = (metadata.logo_path, metadata.logo_position)
    elif upload:
        raise OverlayError(
            "Upload requested but no logo configured",
            "Set logo_path in metadata JSON or pass --logo on the CLI.",
        )

    render_overlay(
        video_path=visual_path,
        audio_path=concatenated,
        output_path=output_path,
        resolution=resolution,
        logo=logo_arg,
        max_duration=max_duration,
        force=force,
    )

    uploaded_id: Optional[str] = None
    if upload:
        logger.info("Fetching audio metadata for description rendering...")
        audio_meta_first = fetch_yt_metadata(resolved[0].url)
        auto_vars = _build_surah_auto_vars(resolved, visual, audio_meta_first)

        if not auto_vars["detected_surah"]:
            raise OverlayError(
                "Could not derive a surah label from the resolved candidates",
                "Title rendering would produce empty placeholders.",
            )

        title = metadata.render_title(extra_vars=auto_vars)
        description = metadata.render_description(extra_vars=auto_vars)
        logger.info(f"Resolved title: {title}")

        from .uploader import upload_with_explicit_metadata

        uploaded_id = upload_with_explicit_metadata(
            video_path=output_path,
            title=title,
            description=description,
            tags=metadata.tags,
            category_id=metadata.category_id,
            privacy=metadata.privacy_status,
        )
    else:
        logger.info("Upload skipped (no --upload flag)")

    return OverlayResult(
        output_path=output_path,
        uploaded_video_id=uploaded_id,
        audio_url=resolved[0].url,
        video_url=visual.url,
    )


# ---------------------------------------------------------------------------
# Surah-number mode (Streamlit UI backend + CLI `--surah-number`)
# ---------------------------------------------------------------------------


def _pascal_case(text: str) -> str:
    """PascalCase a free-form display name for use as an auto-var tag."""
    import re

    parts = re.split(r"[\s\-]+", (text or "").strip())
    return "".join(p[:1].upper() + p[1:].lower() for p in parts if p)


def _compact_consecutive_duplicates(surah_numbers: List[int]) -> List[Tuple[int, int]]:
    """Group consecutive duplicates into ``(number, run_length)`` pairs.

    ``[1, 1, 1, 5]`` -> ``[(1, 3), (5, 1)]``; ``[1, 5, 1]`` -> ``[(1, 1),
    (5, 1), (1, 1)]`` (non-consecutive duplicates stay separate).
    """
    out: List[Tuple[int, int]] = []
    for n in surah_numbers:
        if out and out[-1][0] == n:
            prev_n, prev_count = out[-1]
            out[-1] = (prev_n, prev_count + 1)
        else:
            out.append((n, 1))
    return out


def _surah_numbers_output_filename(surah_numbers: List[int], video_id: str) -> str:
    """`AlFatiha_<vid>.mp4` for one surah; `AlFatiha_+2more_<vid>.mp4` for several.

    Consecutive duplicates compact to ``AlFatiha-x10_<vid>.mp4`` so a
    "10× Al-Fatiha" render produces a sensible filename instead of
    ``AlFatiha_+9more_<vid>.mp4``.
    """
    groups = _compact_consecutive_duplicates(surah_numbers)
    first_n, first_count = groups[0]
    first_info = get_surah_info(first_n)
    first_tag = f"{first_info.tag}-x{first_count}" if first_count > 1 else first_info.tag
    if len(groups) == 1:
        return f"{first_tag}_{video_id}.mp4"
    extras = sum(count for _, count in groups[1:])
    return f"{first_tag}_+{extras}more_{video_id}.mp4"


def _build_surah_numbers_auto_vars(
    surah_numbers: List[int],
    reciter_display_name: str,
    visual_title: str,
) -> dict:
    """Build render auto-vars for the numbers-mode flow.

    Unlike ``_build_surah_auto_vars`` which has to detect surahs/reciter from
    free-form YouTube metadata, here everything is already canonical: numbers
    map straight to ``SurahInfo`` via ``get_surah_info`` and the reciter's
    display name comes directly from the manifest.
    """
    groups = _compact_consecutive_duplicates(surah_numbers)
    name_parts: List[str] = []
    tag_parts: List[str] = []
    for n, count in groups:
        info = get_surah_info(n)
        if count > 1:
            name_parts.append(f"{info.name} (\u00d7{count})")
            tag_parts.append(f"{info.tag}x{count}")
        else:
            name_parts.append(info.name)
            tag_parts.append(info.tag)
    detected_surah = " + ".join(name_parts)
    surah_tag = "".join(tag_parts)
    surah_number_str = (
        str(surah_numbers[0]) if len(surah_numbers) == 1 else ""
    )
    reciter_tag = _pascal_case(reciter_display_name)
    return {
        "audio_title": detected_surah,
        "audio_channel": reciter_display_name,
        "audio_uploader": reciter_display_name,
        "detected_surah": detected_surah,
        "surah_tag": surah_tag,
        "surah_number": surah_number_str,
        "surah_count": str(len(surah_numbers)),
        "reciter": reciter_display_name,
        "reciter_tag": reciter_tag,
        "visual_title": visual_title or "",
    }


def _resolve_visual_video(visual_video_id: str, cache_dir: Path):
    """Look up the given video_id in the cartoon catalog; raise a clear
    OverlayError listing the first-10 available ids if it's missing."""
    from .cartoon_catalog import list_videos

    videos = list_videos(cache_dir=cache_dir)
    for v in videos:
        if v.video_id == visual_video_id:
            return v
    sample = ", ".join(v.video_id for v in videos[:10]) or "(catalog empty)"
    raise OverlayError(
        f"Unknown visual video_id: {visual_video_id!r}",
        f"Not found in the cartoon catalog. First 10 available ids: {sample}",
    )


def run_overlay_from_surah_numbers(
    surah_numbers: List[int],
    reciter_slug: str,
    visual_video_id: str,
    metadata: OverlayMetadata,
    *,
    output_path: Optional[Path] = None,
    cache_dir: Path = Path("cache"),
    resolution: Optional[Tuple[int, int]] = None,
    upscale: bool = False,
    cookies_from_browser: Optional[str] = None,
    proxy: Optional[str] = None,
    upload: bool = False,
) -> OverlayResult:
    """Render a Quran-overlay video from canonical surah numbers + a
    pre-selected visual from the cartoon catalog.

    This is the backend for the Streamlit UI, and also the function behind
    the CLI's ``--surah-number / --reciter / --video-id`` mode. Unlike
    ``run_overlay_surahs`` it does NOT search channels for audio — instead
    it downloads each surah directly from the ``quran_audio_source``
    manifest (stable mirrors) and resolves the visual via the already-
    scraped cartoon catalog.

    Steps:
        1. Download audio for every surah via quran_audio_source.
        2. Concat via audio_concat.concat_audio (cached per joined ids).
        3. Resolve visual_video_id → CatalogVideo → YouTube URL, then
           download video-only via youtube.download_stream.
        4. Optional upscale via upscale.get_or_create_upscaled.
        5. Render via ffmpeg_overlay.render_overlay.
        6. Optional upload via uploader.upload_with_explicit_metadata,
           with auto-vars built from the canonical surah names + reciter.

    Args:
        surah_numbers: Ordered list of surah numbers (1..114). Concatenated
            in the given order.
        reciter_slug: Manifest slug from ``quran_audio_source.list_reciters``.
        visual_video_id: YouTube video id resolved against
            ``cartoon_catalog.list_videos``.
        metadata: OverlayMetadata (title, description template, logo, tags).
        output_path: Destination MP4. When None, a named temp file is used
            (UI flow: preview + optional upload from the same path).
        cache_dir: Cache directory for audio/visual downloads.
        resolution: Render resolution. When None, defaults to (1280, 720)
            under ``upscale=True`` else (1920, 1080).
        upscale: Real-ESRGAN upscale the visual before rendering.
        cookies_from_browser: Passed through to visual download_stream.
        proxy: Passed through to visual download_stream.
        upload: If True AND metadata.logo_path is set, upload after render.

    Returns:
        OverlayResult with output_path, uploaded_video_id (or None),
        audio_url (empty string — the audio isn't a YouTube URL), and
        video_url (the visual's YouTube URL).

    Raises:
        OverlayError: On validation / render / catalog lookup failures.
    """
    from .quran_audio_source import download_surah, get_reciter

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if not surah_numbers:
        raise OverlayError("run_overlay_from_surah_numbers requires at least one surah number")
    for n in surah_numbers:
        # get_surah_info validates range and integer-ness; re-raise as
        # OverlayError so the CLI/UI get a consistent exception type.
        try:
            get_surah_info(n)
        except ValueError as exc:
            raise OverlayError(f"Invalid surah number: {n!r}", str(exc)) from exc

    if resolution is None:
        resolution = (1280, 720) if upscale else (1920, 1080)

    reciter = get_reciter(reciter_slug)

    logger.info(
        "[1/5] Downloading audio for %d surah(s) from %s",
        len(surah_numbers),
        reciter.display_name,
    )
    audio_paths: List[Path] = []
    for n in surah_numbers:
        path = download_surah(n, reciter, cache_dir)
        audio_paths.append(path)

    logger.info(f"[2/5] Concatenating {len(audio_paths)} audio file(s)...")
    if len(audio_paths) == 1:
        concatenated = audio_paths[0]
        logger.info("Single surah; skipping concat.")
    else:
        # Compact consecutive duplicates so 10x Al-Fatiha doesn't expand to
        # ``001_001_..._001`` — produces ``001x10`` instead.
        joined_tag = "_".join(
            f"{n:03d}x{count}" if count > 1 else f"{n:03d}"
            for n, count in _compact_consecutive_duplicates(surah_numbers)
        )
        concatenated = cache_dir / f"concat_{reciter.slug}_{joined_tag}.m4a"
        if not (concatenated.exists() and concatenated.stat().st_size > 0):
            concat_audio(audio_paths, concatenated)
        else:
            logger.info(f"Using cached concat: {concatenated.name}")

    logger.info("[3/5] Resolving visual video_id against cartoon catalog...")
    visual = _resolve_visual_video(visual_video_id, cache_dir)
    visual_path = download_stream(
        url=visual.url,
        output_dir=cache_dir,
        mode="video-only",
        cookies_from_browser=cookies_from_browser,
        proxy=proxy,
    )

    if upscale:
        logger.info("[3.5/5] Upscaling visual via Real-ESRGAN (cached)...")
        visual_path = get_or_create_upscaled(visual_path, visual.video_id, cache_dir)

    # Resolve destination. None → tempfile under gettempdir() so the UI can
    # preview + upload from the same path without owning an output dir.
    if output_path is None:
        output_name = _surah_numbers_output_filename(surah_numbers, visual.video_id)
        output_path = Path(tempfile.gettempdir()) / output_name
    else:
        output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"[4/5] Rendering overlay → {output_path.name}")
    logo_arg: Optional[Tuple[Path, str]] = None
    if metadata.logo_path is not None:
        if not metadata.logo_path.exists():
            raise OverlayError(f"Logo file not found: {metadata.logo_path}")
        logo_arg = (metadata.logo_path, metadata.logo_position)
    elif upload:
        raise OverlayError(
            "Upload requested but no logo configured",
            "Set logo_path in metadata JSON or pass --logo on the CLI.",
        )

    render_overlay(
        video_path=visual_path,
        audio_path=concatenated,
        output_path=output_path,
        resolution=resolution,
        logo=logo_arg,
        max_duration=None,
        force=True,
    )

    uploaded_id: Optional[str] = None
    if upload:
        logger.info("[5/5] Uploading to YouTube...")
        auto_vars = _build_surah_numbers_auto_vars(
            surah_numbers=surah_numbers,
            reciter_display_name=reciter.display_name,
            visual_title=visual.title,
        )
        title = metadata.render_title(extra_vars=auto_vars)
        description = metadata.render_description(extra_vars=auto_vars)
        logger.info(f"Resolved title: {title}")

        from .uploader import upload_with_explicit_metadata

        uploaded_id = upload_with_explicit_metadata(
            video_path=output_path,
            title=title,
            description=description,
            tags=metadata.tags,
            category_id=metadata.category_id,
            privacy=metadata.privacy_status,
        )
    else:
        logger.info("[5/5] Upload skipped (upload=False)")

    return OverlayResult(
        output_path=output_path,
        uploaded_video_id=uploaded_id,
        audio_url="",
        video_url=visual.url,
    )


def upload_rendered(
    rendered_path: Path,
    metadata: OverlayMetadata,
    *,
    surah_numbers: List[int],
    reciter_slug: str,
    visual_title: Optional[str] = None,
    playlist_id: Optional[str] = None,
) -> str:
    """Upload an already-rendered MP4 using the surah-numbers auto-var
    machinery. Used by the Streamlit "Upload" button, which fires after a
    successful render + preview.

    Args:
        rendered_path: The MP4 produced by run_overlay_from_surah_numbers.
        metadata: Same OverlayMetadata used for the render, so title /
            description templates render identically.
        surah_numbers: Surah numbers from the render call (needed to build
            the $detected_surah / $surah_tag vars).
        reciter_slug: Reciter slug from the render call.
        visual_title: Optional; passed through to $visual_title. When None,
            the placeholder is rendered as an empty string.
        playlist_id: Optional YouTube playlist id; when set, the uploaded
            video is appended to that playlist after upload (Phase 1
            ``upload_with_explicit_metadata`` extension).

    Returns:
        The YouTube video id of the uploaded video.

    Raises:
        OverlayError: If the rendered file is missing or the metadata
            template references a placeholder we can't resolve.
    """
    from .quran_audio_source import get_reciter
    from .uploader import upload_with_explicit_metadata

    rendered_path = Path(rendered_path)
    if not rendered_path.exists():
        raise OverlayError(
            f"Rendered file not found: {rendered_path}",
            "upload_rendered requires a previously-produced MP4.",
        )
    if not surah_numbers:
        raise OverlayError("upload_rendered requires at least one surah number")

    reciter = get_reciter(reciter_slug)
    auto_vars = _build_surah_numbers_auto_vars(
        surah_numbers=surah_numbers,
        reciter_display_name=reciter.display_name,
        visual_title=visual_title or "",
    )
    title = metadata.render_title(extra_vars=auto_vars)
    description = metadata.render_description(extra_vars=auto_vars)
    logger.info(f"Uploading previously-rendered file with title: {title}")

    return upload_with_explicit_metadata(
        video_path=rendered_path,
        title=title,
        description=description,
        tags=metadata.tags,
        category_id=metadata.category_id,
        privacy=metadata.privacy_status,
        playlist_id=playlist_id,
    )


# ---------------------------------------------------------------------------
# Ayah-range mode (Streamlit memorisation tab — wishlist M2/M3)
# ---------------------------------------------------------------------------


def _build_ayah_ranges_auto_vars(
    ranges: "List[AyahRange]",
    reciter_display_name: str,
    visual_title: str,
) -> dict:
    """Auto-vars for the ayah-range upload path.

    Title shape mirrors the design-doc example:
    ``"Al-Fatiha (×3) — Ustadh Yusuf | Madrasah"`` for one range
    ``AyahRange(1, 1, 7, 3)``. For multi-range plays we join range
    descriptions with ``" + "``: e.g.
    ``"Al-Fatiha:1-7 (×3) + Al-Baqarah:255 (×5)"``.
    """
    from .ayah_data import ayah_count as _ayah_count

    parts: List[str] = []
    tag_parts: List[str] = []
    for rng in ranges:
        info = get_surah_info(rng.surah)
        max_ayah = _ayah_count(rng.surah)
        if rng.start == 1 and rng.end == max_ayah:
            ayat_label = info.name
        else:
            if rng.start == rng.end:
                ayat_label = f"{info.name}:{rng.start}"
            else:
                ayat_label = f"{info.name}:{rng.start}-{rng.end}"
        if rng.repeats > 1:
            parts.append(f"{ayat_label} (\u00d7{rng.repeats})")
            tag_parts.append(f"{info.tag}{rng.start}_{rng.end}x{rng.repeats}")
        else:
            parts.append(ayat_label)
            tag_parts.append(f"{info.tag}{rng.start}_{rng.end}")
    detected_surah = " + ".join(parts)
    surah_tag = "".join(tag_parts)
    reciter_tag = _pascal_case(reciter_display_name)
    surah_number_str = (
        str(ranges[0].surah) if len(ranges) == 1 else ""
    )
    return {
        "audio_title": detected_surah,
        "audio_channel": reciter_display_name,
        "audio_uploader": reciter_display_name,
        "detected_surah": detected_surah,
        "surah_tag": surah_tag,
        "surah_number": surah_number_str,
        "surah_count": str(len(ranges)),
        "reciter": reciter_display_name,
        "reciter_tag": reciter_tag,
        "visual_title": visual_title or "",
    }


def _ayah_ranges_output_filename(
    ranges: "List[AyahRange]", video_id: str
) -> str:
    """``AlFatiha1_7x3_<vid>.mp4`` for one range; suffix ``_+Nmore`` for
    multi-range plays. Mirrors :func:`_surah_numbers_output_filename` so
    teachers see consistent file names across modes."""
    head_tag = ""
    first = ranges[0]
    info = get_surah_info(first.surah)
    head_tag = f"{info.tag}{first.start}_{first.end}"
    if first.repeats > 1:
        head_tag = f"{head_tag}x{first.repeats}"
    if len(ranges) == 1:
        return f"{head_tag}_{video_id}.mp4"
    extras = len(ranges) - 1
    return f"{head_tag}_+{extras}more_{video_id}.mp4"


def run_overlay_from_ayah_ranges(
    ranges: "List[AyahRange]",
    reciter_slug: str,
    visual_video_id: str,
    metadata: OverlayMetadata,
    *,
    output_path: Optional[Path] = None,
    cache_dir: Path = Path("cache"),
    upscale: bool = False,
    upload: bool = False,
    playlist_id: Optional[str] = None,
    preset_slug: Optional[str] = None,
    burn_subtitles: bool = False,
    extra_translation_id: Optional[int] = None,
    cookies_from_browser: Optional[str] = None,
    proxy: Optional[str] = None,
) -> OverlayResult:
    """Render an ayah-range repetition video for memorisation drills.

    Backs the Streamlit "Ayah range (memorization)" tab. The audio track
    is built from per-ayah EveryAyah MP3s via
    :func:`ayah_repeater.build_ayah_audio` (range repetition + optional
    silent gaps between repeats), then rendered against a cartoon visual
    chosen the same way the surah-numbers flow does it.

    Subtitle generation (``burn_subtitles=True``) is **v1: ayah-level
    only**; word-level karaoke needs per-reciter timing data not yet
    wired in. The .ass file is built from running-duration timestamps
    of the cached per-ayah audio files, so each ayah subtitle event
    runs from the start to the end of that ayah's audio segment.

    Args:
        ranges: One or more :class:`AyahRange` specs in playback order.
        reciter_slug: EveryAyah short slug (see
            :data:`ayah_data.EVERYAYAH_RECITERS`). Used both for audio
            download and for resolving the reciter display name for the
            upload title — when the slug is also a quranicaudio.com slug
            we round-trip through ``quran_audio_source.get_reciter`` to
            pull the display name; otherwise we fall back to the slug.
        visual_video_id: Cartoon-catalog YouTube id (same as the
            surah-numbers flow).
        metadata: OverlayMetadata for the upload title/description
            template + logo.
        output_path: Destination MP4 (defaults to a tempfile).
        cache_dir: Cache directory for ayah MP3s + visual + subtitles.
        upscale: Real-ESRGAN upscale before render.
        upload: After render, push to YouTube via
            :func:`uploader.upload_with_explicit_metadata`.
        playlist_id: Optional YouTube playlist id (forwarded on upload).
        preset_slug: Optional :mod:`render_presets` slug; when set the
            preset's resolution flows into ``render_overlay``. Otherwise
            defaults to (1280, 720) under upscale else (1920, 1080).
        burn_subtitles: Build a trilingual ``.ass`` file via
            :mod:`subtitle_builder` and pass it through to
            ``render_overlay`` for hard-burn into the output MP4. Falls
            back silently if Quran text data is missing.
        extra_translation_id: Optional Quran.com translation resource id
            for the third subtitle line (e.g. 235 for the shipped Dutch
            translation). Ignored when ``burn_subtitles`` is False.
        cookies_from_browser: Forwarded to visual ``download_stream``.
        proxy: Forwarded to visual ``download_stream``.

    Returns:
        OverlayResult with the rendered output_path. ``audio_url`` is
        always the empty string (audio comes from EveryAyah, not YouTube).

    Raises:
        OverlayError: On validation, render, or catalog lookup failures.
    """
    from .ayah_repeater import AyahRange, build_ayah_audio
    from .render_presets import get_preset

    if not ranges:
        raise OverlayError("run_overlay_from_ayah_ranges requires at least one AyahRange")

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Resolve render resolution from preset, upscale fallback, or 1080p default.
    if preset_slug:
        preset = get_preset(preset_slug)
        resolution = preset.resolution
    else:
        resolution = (1280, 720) if upscale else (1920, 1080)

    # Resolve reciter display name. Many EveryAyah short slugs map 1:1 to
    # quranicaudio slugs (alafasy, sudais, ...); when they don't we fall
    # back to the EveryAyah display name in the EVERYAYAH_RECITERS table.
    reciter_display = reciter_slug
    try:
        from .quran_audio_source import get_reciter

        reciter_display = get_reciter(reciter_slug).display_name
    except OverlayError:
        from .ayah_data import EVERYAYAH_RECITERS

        entry = EVERYAYAH_RECITERS.get(reciter_slug.lower())
        if entry is not None:
            reciter_display = entry.get("display_name", reciter_slug)

    # Build the audio track first so we know where to read it from.
    logger.info(
        "[1/5] Building ayah-range audio (%d range(s)) for %s",
        len(ranges),
        reciter_slug,
    )
    # Stable, content-addressable audio filename so two runs with the same
    # spec hit the cache.
    audio_tag_parts: List[str] = []
    for rng in ranges:
        audio_tag_parts.append(
            f"s{rng.surah:03d}_{rng.start:03d}_{rng.end:03d}"
            f"x{rng.repeats}g{int(round(rng.gap_seconds * 1000))}"
        )
    audio_tag = "_".join(audio_tag_parts)
    audio_output = cache_dir / f"ayah_audio_{reciter_slug}_{audio_tag}.m4a"
    if not (audio_output.exists() and audio_output.stat().st_size > 0):
        build_ayah_audio(
            ranges=ranges,
            reciter_slug=reciter_slug,
            cache_dir=cache_dir,
            output=audio_output,
        )
    else:
        logger.info("Using cached ayah-range audio: %s", audio_output.name)

    logger.info("[2/5] Resolving visual video_id against cartoon catalog...")
    visual = _resolve_visual_video(visual_video_id, cache_dir)
    visual_path = download_stream(
        url=visual.url,
        output_dir=cache_dir,
        mode="video-only",
        cookies_from_browser=cookies_from_browser,
        proxy=proxy,
    )

    if upscale:
        logger.info("[2.5/5] Upscaling visual via Real-ESRGAN (cached)...")
        visual_path = get_or_create_upscaled(visual_path, visual.video_id, cache_dir)

    # Optional subtitle .ass file via subtitle_builder.
    subtitles_path: Optional[Path] = None
    if burn_subtitles:
        try:
            from .quran_text import get_ayah_text
            from .subtitle_builder import TimedAyah, build_ass_file

            # Walk a running clock over the ranges; attach the slug to
            # each AyahRange in-flight so _estimate_ayah_timings can find
            # the cache files. We re-implement the clock here (rather
            # than calling _estimate_ayah_timings) so we don't have to
            # mutate the frozen AyahRange dataclass.
            from .ffmpeg_overlay import get_audio_duration

            timed_ayat: List[TimedAyah] = []
            texts = {}
            cursor = 0.0
            for rng in ranges:
                block_durations: List[float] = []
                for ayah in range(rng.start, rng.end + 1):
                    target = (
                        cache_dir
                        / f"audio_ayah_{reciter_slug.lower()}_s{rng.surah:03d}a{ayah:03d}.mp3"
                    )
                    if target.exists() and target.stat().st_size > 0:
                        try:
                            block_durations.append(get_audio_duration(target))
                        except Exception:
                            block_durations.append(2.0)
                    else:
                        block_durations.append(2.0)
                    # Resolve the AyahText once; same key dedupes repeats.
                    key = (rng.surah, ayah)
                    if key not in texts:
                        try:
                            texts[key] = get_ayah_text(
                                rng.surah,
                                ayah,
                                cache_dir=cache_dir,
                                extra_translation_id=extra_translation_id,
                            )
                        except OverlayError as exc:
                            logger.warning(
                                "Skipping subtitle for %s: %s", key, exc
                            )

                for r in range(rng.repeats):
                    if r > 0 and rng.gap_seconds > 0:
                        cursor += rng.gap_seconds
                    for ayah, dur in zip(
                        range(rng.start, rng.end + 1), block_durations
                    ):
                        start = cursor
                        end = cursor + dur
                        timed_ayat.append(
                            TimedAyah(
                                surah=rng.surah,
                                ayah=ayah,
                                start_seconds=start,
                                end_seconds=end,
                            )
                        )
                        cursor = end

            languages = ("ar", "en")
            if extra_translation_id is not None:
                languages = ("ar", "en", "extra")

            subtitles_path = cache_dir / f"subs_{reciter_slug}_{audio_tag}.ass"
            build_ass_file(
                timed_ayat,
                texts,
                subtitles_path,
                languages=languages,
                karaoke=False,  # v1: ayah-level only
                resolution_height=resolution[1],
            )
            logger.info("Built subtitle track: %s", subtitles_path.name)
        except OverlayError as exc:
            logger.warning("Subtitle build failed; rendering without: %s", exc)
            subtitles_path = None

    # Resolve destination.
    if output_path is None:
        output_name = _ayah_ranges_output_filename(ranges, visual.video_id)
        output_path = Path(tempfile.gettempdir()) / output_name
    else:
        output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"[3/5] Rendering overlay -> {output_path.name}")
    logo_arg: Optional[Tuple[Path, str]] = None
    if metadata.logo_path is not None:
        if not metadata.logo_path.exists():
            raise OverlayError(f"Logo file not found: {metadata.logo_path}")
        logo_arg = (metadata.logo_path, metadata.logo_position)
    elif upload:
        raise OverlayError(
            "Upload requested but no logo configured",
            "Set logo_path in metadata JSON or pass --logo on the CLI.",
        )

    render_overlay(
        video_path=visual_path,
        audio_path=audio_output,
        output_path=output_path,
        resolution=resolution,
        logo=logo_arg,
        max_duration=None,
        force=True,
        subtitles_path=subtitles_path,
    )

    uploaded_id: Optional[str] = None
    if upload:
        logger.info("[4/5] Uploading to YouTube...")
        auto_vars = _build_ayah_ranges_auto_vars(
            ranges=ranges,
            reciter_display_name=reciter_display,
            visual_title=visual.title,
        )
        title = metadata.render_title(extra_vars=auto_vars)
        description = metadata.render_description(extra_vars=auto_vars)
        logger.info(f"Resolved title: {title}")

        from .uploader import upload_with_explicit_metadata

        uploaded_id = upload_with_explicit_metadata(
            video_path=output_path,
            title=title,
            description=description,
            tags=metadata.tags,
            category_id=metadata.category_id,
            privacy=metadata.privacy_status,
            playlist_id=playlist_id,
        )
    else:
        logger.info("[4/5] Upload skipped (upload=False)")

    return OverlayResult(
        output_path=output_path,
        uploaded_video_id=uploaded_id,
        audio_url="",
        video_url=visual.url,
    )
