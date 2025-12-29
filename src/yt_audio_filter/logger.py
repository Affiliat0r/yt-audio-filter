"""Logging configuration for YT Audio Filter."""

import logging
import sys
from typing import Optional


def setup_logger(
    verbose: bool = False,
    quiet: bool = False,
    name: str = "yt_audio_filter"
) -> logging.Logger:
    """
    Configure and return the application logger.

    Args:
        verbose: If True, set log level to DEBUG
        quiet: If True, set log level to WARNING (overrides verbose)
        name: Logger name

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Determine log level
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Create console handler
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    # Create formatter
    if verbose:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%H:%M:%S"
        )
    else:
        formatter = logging.Formatter("%(levelname)s: %(message)s")

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def get_logger(name: str = "yt_audio_filter") -> logging.Logger:
    """Get the application logger."""
    return logging.getLogger(name)


class ProgressLogger:
    """Helper class for logging pipeline progress."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or get_logger()
        self.stages = ["Extract Audio", "Isolate Vocals", "Remux Video"]
        self.current_stage = 0

    def start_stage(self, stage_name: str) -> None:
        """Log the start of a processing stage."""
        self.current_stage += 1
        total = len(self.stages)
        self.logger.info(f"[{self.current_stage}/{total}] {stage_name}...")

    def complete_stage(self, stage_name: str) -> None:
        """Log the completion of a processing stage."""
        total = len(self.stages)
        self.logger.info(f"[{self.current_stage}/{total}] {stage_name} complete")

    def log_detail(self, message: str) -> None:
        """Log a detail message (shown only in verbose mode)."""
        self.logger.debug(message)
