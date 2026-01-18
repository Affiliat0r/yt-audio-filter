"""Command-line interface for YT Audio Filter."""

import argparse
import sys
from pathlib import Path

from . import __version__
from .exceptions import YTAudioFilterError
from .ffmpeg_path import setup_ffmpeg_path
from .logger import setup_logger
from .pipeline import process_video
from .utils import create_temp_dir, generate_output_path
from .youtube import VideoMetadata, download_youtube_video, ensure_ytdlp_available, is_youtube_url


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        prog="yt-audio-filter",
        description="Remove background music from MP4 videos using AI (Demucs)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  yt-audio-filter video.mp4                        Process local video file
  yt-audio-filter "https://youtube.com/watch?v=..." Process YouTube video
  yt-audio-filter "https://youtu.be/..."           Process YouTube short URL
  yt-audio-filter video.mp4 -o clean.mp4           Specify output file
  yt-audio-filter video.mp4 --output-dir ./output  Save to output directory
  yt-audio-filter video.mp4 --device cuda          Use GPU acceleration
  yt-audio-filter video.mp4 --upload               Upload result to YouTube
  yt-audio-filter video.mp4 -v                     Enable verbose logging

Notes:
  - FFmpeg is auto-detected (bundled or system PATH)
  - YouTube URLs require yt-dlp (pip install yt-dlp)
  - YouTube upload requires: pip install google-api-python-client google-auth-oauthlib
  - GPU acceleration requires CUDA-capable GPU and PyTorch with CUDA support
        """
    )

    parser.add_argument(
        "input",
        type=str,
        nargs="?",
        default=None,
        help="Path to video file or YouTube URL"
    )

    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Path for output video file (default: input_filtered.mp4)"
    )

    parser.add_argument(
        "-d", "--device",
        type=str,
        choices=["auto", "cpu", "cuda"],
        default="auto",
        help="Device for AI processing (default: auto)"
    )

    parser.add_argument(
        "-m", "--model",
        type=str,
        default="htdemucs",
        help="Demucs model to use (default: htdemucs). "
             "Options: htdemucs (best quality, slow on long videos), "
             "mdx_extra (faster, more consistent speed), "
             "mdx_extra_q (fastest, quantized)"
    )

    parser.add_argument(
        "-b", "--bitrate",
        type=str,
        default="192k",
        help="Output audio bitrate (default: 192k)"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose (debug) logging"
    )

    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress all output except errors"
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for output files (default: ./output/)"
    )

    # YouTube upload options
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload processed video to YouTube"
    )

    parser.add_argument(
        "--playlist",
        type=str,
        default=None,
        help="YouTube playlist ID to add video to"
    )

    parser.add_argument(
        "--privacy",
        type=str,
        choices=["public", "unlisted", "private"],
        default="unlisted",
        help="YouTube video privacy setting (default: unlisted)"
    )

    parser.add_argument(
        "--list-playlists",
        action="store_true",
        help="List your YouTube playlists and exit"
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}"
    )

    # Performance tuning options
    parser.add_argument(
        "--segment",
        type=int,
        default=None,
        help="Segment size in seconds for GPU processing (default: auto, slow on long videos). "
             "Larger values = faster but more VRAM. Recommended: 40-60 for 8GB GPUs, 20-30 for 4GB GPUs. "
             "CRITICAL for long videos (>30min) to avoid 10x slowdown."
    )

    parser.add_argument(
        "--shifts",
        type=int,
        default=1,
        help="Number of random shifts for quality/speed tradeoff (default: 1). Higher values improve quality but increase processing time."
    )

    parser.add_argument(
        "--fp16",
        action="store_true",
        help="[EXPERIMENTAL] Use mixed precision (FP16) for GPU inference. WARNING: Currently causes 7-8x slowdown with Demucs. Do not use."
    )

    parser.add_argument(
        "--compile",
        action="store_true",
        help="[EXPERIMENTAL] Compile model with torch.compile() (requires PyTorch 2.0+). Note: Currently incompatible with htdemucs model."
    )

    parser.add_argument(
        "--watermark",
        action="store_true",
        help="Add a small watermark to help avoid Content ID matching"
    )

    parser.add_argument(
        "--chunk-duration",
        type=int,
        default=None,
        help="Split long videos into chunks of this many seconds for processing (default: auto - 900s/15min for videos >30min). "
             "Enables consistent high-speed processing on long videos. Set to 0 to disable chunking."
    )

    parser.add_argument(
        "--parallel-chunks",
        type=int,
        default=1,
        help="Number of chunks to process in parallel (default: 1 = sequential). "
             "Set to 2 to process 2 chunks simultaneously for 2x speedup. "
             "Requires sufficient VRAM (each chunk uses ~3-4GB). Recommended: 2 for 8GB+ GPUs."
    )

    return parser


def parse_args(args=None) -> argparse.Namespace:
    """Parse and validate command-line arguments."""
    parser = create_parser()
    parsed = parser.parse_args(args)

    # For --list-playlists, input is not required
    if parsed.list_playlists:
        parsed.is_youtube_url = False
        return parsed

    # Validate that input is provided for normal operations
    if parsed.input is None:
        parser.error("the following arguments are required: input")

    # Check if input is a YouTube URL or local file
    parsed.is_youtube_url = is_youtube_url(parsed.input)

    # Resolve paths to absolute (only for local files)
    if not parsed.is_youtube_url:
        parsed.input = Path(parsed.input).resolve()

    if parsed.output is not None:
        parsed.output = parsed.output.resolve()

    # Resolve output directory
    if parsed.output_dir is not None:
        parsed.output_dir = parsed.output_dir.resolve()

    return parsed


def get_output_dir(parsed) -> Path:
    """Determine the output directory based on arguments."""
    if parsed.output_dir is not None:
        output_dir = parsed.output_dir
    else:
        # Default to ./output/ relative to current directory
        output_dir = Path.cwd() / "output"

    # Create directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def main(args=None) -> int:
    """
    Main entry point for the CLI.

    Args:
        args: Command-line arguments (defaults to sys.argv)

    Returns:
        Exit code (0 for success, 1 for error)
    """
    try:
        # Parse arguments
        parsed = parse_args(args)

        # Setup logging
        logger = setup_logger(verbose=parsed.verbose, quiet=parsed.quiet)

        # Auto-detect and configure FFmpeg
        setup_ffmpeg_path()

        # Handle --list-playlists special command
        if parsed.list_playlists:
            from .uploader import list_playlists

            playlists = list_playlists()
            if playlists:
                print("\nYour YouTube Playlists:")
                print("-" * 50)
                for pl in playlists:
                    print(f"  {pl['title']}")
                    print(f"    ID: {pl['id']}")
                print()
            else:
                print("No playlists found (or authentication required)")
            return 0

        # Track video metadata for YouTube uploads
        video_metadata: VideoMetadata | None = None

        if parsed.is_youtube_url:
            # YouTube URL flow: download first, then process
            ensure_ytdlp_available()

            # Use persistent cache directory for YouTube downloads (enables reuse)
            cache_dir = Path.cwd() / "cache" / "youtube"
            cache_dir.mkdir(parents=True, exist_ok=True)

            # Download the video and get metadata (uses cache if available)
            video_metadata = download_youtube_video(parsed.input, cache_dir, use_cache=True)
            downloaded_path = video_metadata.file_path
            logger.debug(f"Using video file: {downloaded_path}")

            # Generate output path
            if parsed.output is not None:
                output_path = parsed.output
            else:
                # Use output directory with _filtered suffix
                output_dir = get_output_dir(parsed)
                output_path = output_dir / f"{downloaded_path.stem}_filtered.mp4"

            # Run the processing pipeline
            result = process_video(
                input_path=downloaded_path,
                output_path=output_path,
                device=parsed.device,
                model_name=parsed.model,
                audio_bitrate=parsed.bitrate,
                segment=parsed.segment,
                shifts=parsed.shifts,
                watermark=parsed.watermark,
                fp16=parsed.fp16,
                compile_model=parsed.compile,
                chunk_duration=parsed.chunk_duration,
                parallel_chunks=parsed.parallel_chunks,
            )
        else:
            # Local file flow
            input_path = parsed.input

            if parsed.output is not None:
                output_path = parsed.output
            else:
                # Use output directory with _filtered suffix
                output_dir = get_output_dir(parsed)
                output_path = output_dir / f"{input_path.stem}_filtered.mp4"

            # Run the processing pipeline
            result = process_video(
                input_path=input_path,
                output_path=output_path,
                device=parsed.device,
                model_name=parsed.model,
                audio_bitrate=parsed.bitrate,
                segment=parsed.segment,
                shifts=parsed.shifts,
                watermark=parsed.watermark,
                fp16=parsed.fp16,
                compile_model=parsed.compile,
                chunk_duration=parsed.chunk_duration,
                parallel_chunks=parsed.parallel_chunks,
            )

        logger.info(f"Success! Output saved to: {result}")

        # Handle YouTube upload if requested
        if parsed.upload:
            from .uploader import upload_to_youtube

            logger.info("Uploading to YouTube...")
            video_id = upload_to_youtube(
                video_path=result,
                original_metadata=video_metadata,
                privacy=parsed.privacy,
                playlist_id=parsed.playlist,
            )
            logger.info(f"Upload complete! https://youtube.com/watch?v={video_id}")

        return 0

    except YTAudioFilterError as e:
        # Handle our custom errors with nice formatting
        logger = setup_logger(quiet=False)
        logger.error(str(e))
        if e.details:
            logger.debug(f"Details: {e.details}")
        return 1

    except KeyboardInterrupt:
        print("\nOperation cancelled by user", file=sys.stderr)
        return 130

    except Exception as e:
        # Handle unexpected errors
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
