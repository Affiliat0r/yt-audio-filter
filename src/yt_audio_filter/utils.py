"""Utility functions for YT Audio Filter."""

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .exceptions import ValidationError
from .logger import get_logger

logger = get_logger()


@contextmanager
def create_temp_dir(prefix: str = "yt_audio_filter_") -> Iterator[Path]:
    """
    Context manager for creating a temporary directory.

    The directory is automatically cleaned up on exit, even if an error occurs.

    Args:
        prefix: Prefix for the temp directory name

    Yields:
        Path to the temporary directory
    """
    temp_path = Path(tempfile.mkdtemp(prefix=prefix))
    logger.debug(f"Created temp directory: {temp_path}")

    try:
        yield temp_path
    finally:
        if temp_path.exists():
            try:
                shutil.rmtree(temp_path)
                logger.debug(f"Cleaned up temp directory: {temp_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp directory {temp_path}: {e}")


def validate_input_file(path: Path) -> None:
    """
    Validate that the input file exists and is a supported format.

    Args:
        path: Path to the input file

    Raises:
        ValidationError: If the file doesn't exist or is not supported
    """
    if not path.exists():
        raise ValidationError(f"Input file does not exist: {path}")

    if not path.is_file():
        raise ValidationError(f"Input path is not a file: {path}")

    # Check file extension
    supported_extensions = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm"}
    if path.suffix.lower() not in supported_extensions:
        raise ValidationError(
            f"Unsupported file format: {path.suffix}",
            f"Supported formats: {', '.join(sorted(supported_extensions))}"
        )

    # Check if file is readable
    try:
        with open(path, "rb") as f:
            # Read first few bytes to verify it's accessible
            f.read(1024)
    except PermissionError:
        raise ValidationError(f"Cannot read input file (permission denied): {path}")
    except Exception as e:
        raise ValidationError(f"Cannot read input file: {path}", str(e))


def generate_output_path(
    input_path: Path,
    output_path: Optional[Path] = None,
    suffix: str = "_filtered"
) -> Path:
    """
    Generate the output file path.

    If output_path is provided, use it. Otherwise, generate a path based on
    the input path with the given suffix.

    Args:
        input_path: Path to the input file
        output_path: Optional explicit output path
        suffix: Suffix to add before the extension (default: "_filtered")

    Returns:
        Path for the output file
    """
    if output_path is not None:
        return output_path

    # Generate output path: input_filtered.mp4
    stem = input_path.stem
    extension = input_path.suffix
    parent = input_path.parent

    return parent / f"{stem}{suffix}{extension}"


def get_file_size_mb(path: Path) -> float:
    """
    Get the file size in megabytes.

    Args:
        path: Path to the file

    Returns:
        File size in MB
    """
    if not path.exists():
        return 0.0
    return path.stat().st_size / (1024 * 1024)


def ensure_parent_exists(path: Path) -> None:
    """
    Ensure the parent directory of a path exists.

    Args:
        path: Path whose parent should exist
    """
    path.parent.mkdir(parents=True, exist_ok=True)
