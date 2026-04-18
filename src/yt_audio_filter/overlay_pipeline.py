"""Orchestration for the yt-quran-overlay workflow.

Four stages: download video-only, download audio-only, render, optional upload.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .exceptions import OverlayError
from .ffmpeg_overlay import render_overlay
from .logger import get_logger
from .metadata import OverlayMetadata
from .surah_detector import ReciterMatch, SurahMatch, detect_reciter, detect_surah
from .youtube import download_stream, extract_video_id
from .yt_metadata import YouTubeMetadata, fetch_yt_metadata

logger = get_logger()


@dataclass
class OverlayResult:
    output_path: Path
    uploaded_video_id: Optional[str] = None


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

    return OverlayResult(output_path=output_path, uploaded_video_id=uploaded_id)
