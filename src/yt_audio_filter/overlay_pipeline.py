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
from .youtube import download_stream, extract_video_id

logger = get_logger()


@dataclass
class OverlayResult:
    output_path: Path
    uploaded_video_id: Optional[str] = None


def _output_filename(audio_url: str, video_url: str) -> str:
    audio_id = extract_video_id(audio_url)
    video_id = extract_video_id(video_url)
    return f"{audio_id}_{video_id}.mp4"


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
        logger.info("[4/4] Uploading to YouTube...")
        from .uploader import upload_with_explicit_metadata

        uploaded_id = upload_with_explicit_metadata(
            video_path=output_path,
            title=metadata.title,
            description=metadata.description,
            tags=metadata.tags,
            category_id=metadata.category_id,
            privacy=metadata.privacy_status,
        )
    else:
        logger.info("[4/4] Upload skipped (no --upload flag)")

    return OverlayResult(output_path=output_path, uploaded_video_id=uploaded_id)
