"""Main orchestration logic for the audio filtering pipeline."""

from pathlib import Path
from typing import Callable, Optional

from .demucs_processor import ensure_demucs_available, isolate_vocals
from .exceptions import YTAudioFilterError
from .ffmpeg import ensure_ffmpeg_available, extract_audio, get_audio_info, remux_video
from .logger import ProgressLogger, get_logger
from .utils import create_temp_dir, get_file_size_mb, validate_input_file

logger = get_logger()


def validate_prerequisites() -> None:
    """
    Validate that all required tools are available.

    Raises:
        PrerequisiteError: If any required tool is missing
    """
    ensure_ffmpeg_available()
    ensure_demucs_available()


def process_video(
    input_path: Path,
    output_path: Path,
    device: str = "auto",
    model_name: str = "htdemucs",
    audio_bitrate: str = "192k",
    progress_callback: Optional[Callable[[str, int], None]] = None
) -> Path:
    """
    Process a video to isolate vocals and remove background music.

    This is the main entry point for the audio filtering pipeline.

    Args:
        input_path: Path to input MP4 file
        output_path: Path for output MP4 file
        device: Device for AI processing ("auto", "cpu", "cuda")
        model_name: Demucs model variant (default: "htdemucs")
        audio_bitrate: Output audio bitrate (default: "192k")
        progress_callback: Optional callback(stage_name, progress_percent)

    Returns:
        Path to the processed output file

    Raises:
        YTAudioFilterError: On any processing failure
    """
    progress = ProgressLogger()

    # Log input file info
    input_size = get_file_size_mb(input_path)
    logger.info(f"Processing: {input_path.name} ({input_size:.1f} MB)")

    # Validate prerequisites
    logger.debug("Validating prerequisites...")
    validate_prerequisites()

    # Validate input file
    validate_input_file(input_path)

    # Get audio info for logging
    audio_info = get_audio_info(input_path)
    if audio_info:
        logger.debug(
            f"Audio info: {audio_info.get('sample_rate', 'unknown')} Hz, "
            f"{audio_info.get('channels', 'unknown')} channels, "
            f"duration: {audio_info.get('duration', 0):.1f}s"
        )

    # Create temp directory for intermediate files
    with create_temp_dir() as temp_dir:
        try:
            # Stage 1: Extract audio from video
            progress.start_stage("Extract Audio")
            if progress_callback:
                progress_callback("Extract Audio", 0)

            audio_wav = temp_dir / "audio.wav"
            extract_audio(input_path, audio_wav)

            if progress_callback:
                progress_callback("Extract Audio", 100)
            progress.complete_stage("Extract Audio")

            # Stage 2: Isolate vocals using Demucs AI
            progress.start_stage("Isolate Vocals")
            if progress_callback:
                progress_callback("Isolate Vocals", 0)

            vocals_wav = temp_dir / "vocals.wav"

            # Create a sub-callback for granular Demucs progress
            def demucs_progress(info: dict):
                if progress_callback:
                    progress_callback("Isolate Vocals", info.get('percent', 0), info)

            isolate_vocals(
                audio_wav,
                vocals_wav,
                device=device,
                model_name=model_name,
                progress_callback=demucs_progress
            )

            if progress_callback:
                progress_callback("Isolate Vocals", 100)
            progress.complete_stage("Isolate Vocals")

            # Stage 3: Remux video with processed vocals
            progress.start_stage("Remux Video")
            if progress_callback:
                progress_callback("Remux Video", 0)

            remux_video(
                input_path,
                vocals_wav,
                output_path,
                audio_bitrate=audio_bitrate
            )

            if progress_callback:
                progress_callback("Remux Video", 100)
            progress.complete_stage("Remux Video")

            # Log output info
            output_size = get_file_size_mb(output_path)
            logger.info(f"Output saved: {output_path.name} ({output_size:.1f} MB)")

            return output_path

        except YTAudioFilterError:
            # Re-raise our custom errors
            raise
        except Exception as e:
            # Wrap unexpected errors
            raise YTAudioFilterError(f"Unexpected error during processing: {e}")
