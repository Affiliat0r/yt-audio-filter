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
from .overlay_pipeline import run_overlay


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
            "and optionally uploads the result with metadata from a JSON file."
        ),
    )
    parser.add_argument("--video-url", required=True, help="YouTube URL for visual video")
    parser.add_argument(
        "--audio-url", required=True, help="YouTube URL for Quran recitation audio"
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
        "--cookies-from-browser",
        default=None,
        help="Extract cookies from browser (firefox, chrome, edge, ...)",
    )
    parser.add_argument("--proxy", default=None, help="Proxy URL for downloads")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="Only warnings and errors")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logger = setup_logger(verbose=args.verbose, quiet=args.quiet)

    try:
        metadata = load_metadata(args.metadata)
        metadata = apply_cli_overrides(
            metadata, logo=args.logo, logo_position=args.logo_position
        )

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
        )

        logger.info(f"Done. Output: {result.output_path}")
        if result.uploaded_video_id:
            logger.info(f"Uploaded video: https://youtube.com/watch?v={result.uploaded_video_id}")
        return 0

    except YTAudioFilterError as e:
        logger.error(str(e))
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
