"""Application configuration management."""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

# Default config file location
CONFIG_DIR = Path.home() / ".yt-audio-filter"
CONFIG_FILE = CONFIG_DIR / "app_config.json"


@dataclass
class AppConfig:
    """Application configuration settings."""

    # Processing options
    device: str = "auto"  # auto, cpu, cuda
    audio_bitrate: str = "192k"
    model_name: str = "htdemucs"
    max_parallel_workers: int = 2  # Number of videos to process simultaneously

    # Output options
    output_dir: str = ""  # Empty = use default temp dir
    keep_local_copies: bool = True
    auto_delete_after_upload: bool = False

    # Upload defaults
    default_privacy: str = "public"
    add_attribution_footer: bool = True

    # Recent channels for quick access
    recent_channels: List[str] = field(default_factory=list)

    # UI preferences
    videos_per_page: int = 20


def load_config() -> AppConfig:
    """
    Load configuration from file.

    Returns:
        AppConfig with saved or default settings
    """
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return AppConfig(**data)
        except Exception:
            pass
    return AppConfig()


def save_config(config: AppConfig) -> None:
    """
    Save configuration to file.

    Args:
        config: AppConfig to save
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(asdict(config), f, indent=2)


def add_recent_channel(config: AppConfig, channel: str, max_recent: int = 10) -> AppConfig:
    """
    Add a channel to recent channels list.

    Args:
        config: Current config
        channel: Channel handle to add
        max_recent: Maximum recent channels to keep

    Returns:
        Updated config
    """
    # Remove if already exists (to move to front)
    if channel in config.recent_channels:
        config.recent_channels.remove(channel)

    # Add to front
    config.recent_channels.insert(0, channel)

    # Trim to max
    config.recent_channels = config.recent_channels[:max_recent]

    return config
