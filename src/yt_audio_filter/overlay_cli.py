"""CLI entry point for yt-quran-overlay."""

import argparse
import sys
from pathlib import Path

from .exceptions import YTAudioFilterError
from .logger import setup_logger
from .metadata import (
    DEFAULT_LOGO_POSITION,
    VALID_LOGO_POSITIONS,
    apply_cli_overrides,
    load_metadata,
)
from .overlay_pipeline import run_overlay, run_overlay_batch, run_overlay_surahs
from .pair_state import DEFAULT_STATE_PATH


def _parse_resolution(value: str) -> tuple[int, int]:
    try:
        width_str, height_str = value.lower().split("x", 1)
        width = int(width_str.strip())
        height = int(height_str.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid --resolution {value!r}; expected WIDTHxHEIGHT (e.g. 1920x1080)"
        )
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError(f"--resolution must be positive: {value!r}")
    return (width, height)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt-quran-overlay",
        description=(
            "Combine a YouTube visual video with a separate YouTube Quran "
            "recitation. Mutes the original audio, loops the video to match "
            "the recitation length, normalizes loudness, overlays a logo, "
            "and optionally uploads the result with metadata from a JSON file. "
            "Two modes: manual (--video-url + --audio-url) or discovery "
            "(--video-channel + --audio-channel)."
        ),
    )

    # Source selection: either manual URLs OR channel discovery.
    parser.add_argument("--video-url", help="YouTube URL for visual video (manual mode)")
    parser.add_argument("--audio-url", help="YouTube URL for Quran recitation (manual mode)")
    parser.add_argument(
        "--video-channel",
        help="YouTube channel URL or @handle to draw visual videos from (discovery mode)",
    )
    parser.add_argument(
        "--audio-channel",
        help="YouTube channel URL or @handle to draw Quran audio from (discovery mode)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of videos to produce in discovery mode (default: 1)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=(
            "JSON file tracking already-processed pairs (discovery mode; "
            f"default: {DEFAULT_STATE_PATH})"
        ),
    )
    parser.add_argument(
        "--surah",
        action="append",
        default=None,
        help=(
            "Surah to include (surah mode; repeatable). Each value is "
            "either a canonical name (e.g. 'Al-Fatiha') resolved against "
            "the audio channel, OR a direct YouTube URL used as-is — "
            "useful when the channel doesn't carry a particular surah. "
            "Order is preserved in the concatenated audio. Requires "
            "--audio-channel + --video-channel."
        ),
    )

    parser.add_argument(
        "--metadata",
        required=True,
        type=Path,
        help="Path to metadata JSON (title, description template, tags, etc.)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache"),
        help="Directory for cached downloads (default: cache)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory for rendered MP4 (default: output)",
    )
    parser.add_argument(
        "--logo",
        type=Path,
        default=None,
        help="Path to logo PNG (overrides metadata.logo_path)",
    )
    parser.add_argument(
        "--logo-position",
        choices=sorted(VALID_LOGO_POSITIONS),
        default=None,
        help=f"Logo corner (overrides metadata; default: {DEFAULT_LOGO_POSITION})",
    )
    parser.add_argument(
        "--resolution",
        type=_parse_resolution,
        default=(1920, 1080),
        help="Output resolution WIDTHxHEIGHT (default: 1920x1080)",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=7200.0,
        help="Abort if audio exceeds this many seconds (default: 7200 = 2h)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing output file"
    )
    parser.add_argument("--upload", action="store_true", help="Upload to YouTube after render")
    parser.add_argument(
        "--upscale",
        action="store_true",
        help=(
            "Real-ESRGAN upscale the visual to 1080p before rendering. "
            "Cached per video_id under cache/upscaled_<id>.mp4; first call "
            "for a given visual is slow (~14 fps GPU), subsequent calls "
            "reuse the cache and cost nothing."
        ),
    )
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Extract cookies from browser (firefox, chrome, edge, ...)",
    )
    parser.add_argument("--proxy", default=None, help="Proxy URL for downloads")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="Only warnings and errors")
    return parser


def _validate_source_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    """Return 'manual', 'discovery', or 'surah' based on which args are set."""
    manual = bool(args.video_url and args.audio_url)
    surah = bool(args.surah)
    channels = bool(args.video_channel and args.audio_channel)
    discovery = channels and not surah

    active = [m for m in (manual, discovery, surah) if m]
    if len(active) > 1:
        parser.error(
            "Pick exactly one mode: manual (--video-url + --audio-url), "
            "discovery (--video-channel + --audio-channel), or "
            "surah (--surah ... + --video-channel + --audio-channel)."
        )
    if len(active) == 0:
        parser.error(
            "Must supply one of: --video-url + --audio-url (manual), "
            "--video-channel + --audio-channel (discovery), or "
            "--surah ... + --video-channel + --audio-channel (surah)."
        )
    if surah and not channels:
        parser.error(
            "Surah mode requires both --video-channel AND --audio-channel "
            "(the channels to resolve surahs and source visuals from)."
        )
    if (manual or surah) and args.count != 1:
        parser.error("--count > 1 only applies in discovery mode")
    if manual:
        return "manual"
    if surah:
        return "surah"
    return "discovery"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    mode = _validate_source_args(args, parser)

    logger = setup_logger(verbose=args.verbose, quiet=args.quiet)

    try:
        metadata = load_metadata(args.metadata)
        metadata = apply_cli_overrides(
            metadata, logo=args.logo, logo_position=args.logo_position
        )

        if mode == "manual":
            result = run_overlay(
                video_url=args.video_url,
                audio_url=args.audio_url,
                metadata=metadata,
                cache_dir=args.cache_dir,
                output_dir=args.output_dir,
                resolution=args.resolution,
                max_duration=args.max_duration,
                force=args.force,
                upload=args.upload,
                cookies_from_browser=args.cookies_from_browser,
                proxy=args.proxy,
                upscale=args.upscale,
            )
            logger.info(f"Done. Output: {result.output_path}")
            if result.uploaded_video_id:
                logger.info(
                    f"Uploaded video: https://youtube.com/watch?v={result.uploaded_video_id}"
                )
        elif mode == "surah":
            result = run_overlay_surahs(
                surah_names=args.surah,
                audio_channel=args.audio_channel,
                video_channel=args.video_channel,
                metadata=metadata,
                cache_dir=args.cache_dir,
                output_dir=args.output_dir,
                resolution=args.resolution,
                max_duration=args.max_duration,
                force=args.force,
                upload=args.upload,
                cookies_from_browser=args.cookies_from_browser,
                proxy=args.proxy,
                upscale=args.upscale,
            )
            logger.info(f"Done. Output: {result.output_path}")
            if result.uploaded_video_id:
                logger.info(
                    f"Uploaded video: https://youtube.com/watch?v={result.uploaded_video_id}"
                )
        else:
            results = run_overlay_batch(
                audio_channel=args.audio_channel,
                video_channel=args.video_channel,
                metadata=metadata,
                cache_dir=args.cache_dir,
                output_dir=args.output_dir,
                count=args.count,
                resolution=args.resolution,
                max_duration=args.max_duration,
                force=args.force,
                upload=args.upload,
                cookies_from_browser=args.cookies_from_browser,
                proxy=args.proxy,
                state_path=args.state_file,
                upscale=args.upscale,
            )
            logger.info(f"Batch done: {len(results)} video(s) produced")
            for i, r in enumerate(results, start=1):
                extra = (
                    f" -> https://youtube.com/watch?v={r.uploaded_video_id}"
                    if r.uploaded_video_id
                    else ""
                )
                logger.info(f"  [{i}] {r.output_path.name}{extra}")
        return 0

    except YTAudioFilterError as e:
        logger.error(str(e))
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
