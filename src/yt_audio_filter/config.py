"""YAML-based configuration system for video discovery and scheduling."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .exceptions import ConfigError
from .logger import get_logger

logger = get_logger()

CONFIG_DIR = Path.home() / ".yt-audio-filter"
DISCOVERY_CONFIG_FILE = CONFIG_DIR / "discovery_config.yaml"


@dataclass
class SearchConfig:
    """YouTube Data API search configuration."""

    queries: List[str] = field(
        default_factory=lambda: [
            "cocuk sarkisi turkce animasyon",
            "egitici cocuk videolari turkce",
            "turkce cocuk cizgi film derleme",
            "cocuklar icin renkleri ogreniyorum",
            "hayvanlar cocuk sarkisi turkce",
            "turkce ninni animasyon",
            "cocuk masallari animasyon turkce",
            "sayilari ogreniyorum cocuklar icin",
            "turkce alfabe cocuk sarkisi",
            "bebek sarkilari turkce animasyon",
        ]
    )
    language: str = "tr"
    region: str = "TR"
    category_id: str = "24"  # Entertainment
    max_results_per_query: int = 10
    video_duration: str = "medium"  # short/medium/long
    order: str = "date"
    published_after_days: int = 90


@dataclass
class ChannelConfig:
    """Channel whitelist/blacklist configuration."""

    whitelisted_channels: List[str] = field(default_factory=list)
    blacklisted_channels: List[str] = field(
        default_factory=lambda: [
            # Cartoon Network Turkey
            "@cartoonnetworkturkiye",
            # TRT Cocuk
            "@TRTCocuk",
            # Niloya Official
            "@niloyatv",
            # Kukuli Official
            "@KukuliTurkce",
            # Pepee Official
            "@PepeeCizgiFilm",
            # Rafadan Tayfa / TRT
            "@RafadanTayfa",
            # Kral Sakir Official
            "@KralSakir",
            # Disney Channel Turkey
            "@DisneychannelTR",
            # Minika
            "@MinikaCOCUK",
            "@MinikaGO",
            # Dusyeri (Pepee/Bebe producers)
            "@dusyeri",
            # Other official Turkish animation
            "@BaykusHopHop",
            "@PirilCizgiFilm",
            "@SevimliDostlar",
            # International dubbed (always Content ID)
            "@CoComelon",
            "@BabyBusTurkce",
            "@LittleAngel",
            "@BlippiTurkce",
        ]
    )
    blacklisted_keywords: List[str] = field(
        default_factory=lambda: [
            "trailer",
            "fragman",
            "reklam",
            "Gumball",
            "Clarence",
            "Ben 10",
            "Tom and Jerry",
            "Tom ve Jerry",
            "Scooby",
            "Cartoon Network",
            "Disney",
        ]
    )


@dataclass
class CopyrightConfig:
    """Copyright risk scoring thresholds."""

    max_risk_score: float = 0.5
    official_channel_penalty: float = 0.35
    compilation_channel_bonus: float = -0.15
    creative_commons_bonus: float = -0.3
    high_sub_count_threshold: int = 100_000
    high_sub_penalty: float = 0.2
    official_title_penalty: float = 0.2
    base_score: float = 0.3
    keyword_penalties: Dict[str, float] = field(
        default_factory=lambda: {
            "resmi kanal": 0.3,
            "official": 0.3,
            "trt": 0.3,
        }
    )


@dataclass
class QuotaConfig:
    """YouTube Data API quota management."""

    daily_quota_limit: int = 10_000
    search_cost: int = 100
    video_details_cost: int = 1
    channel_details_cost: int = 1
    max_searches_per_run: int = 20


@dataclass
class DurationConfig:
    """Video duration filtering."""

    min_seconds: int = 600  # 10 minutes
    max_seconds: int = 1800  # 30 minutes


@dataclass
class SchedulerConfig:
    """Scheduler behavior configuration."""

    videos_per_run: int = 1
    device: str = "cpu"
    model_name: str = "htdemucs"
    privacy: str = "unlisted"
    output_dir: str = "output"


@dataclass
class DiscoveryConfig:
    """Top-level configuration combining all sub-configs."""

    search: SearchConfig = field(default_factory=SearchConfig)
    channels: ChannelConfig = field(default_factory=ChannelConfig)
    copyright: CopyrightConfig = field(default_factory=CopyrightConfig)
    quota: QuotaConfig = field(default_factory=QuotaConfig)
    duration: DurationConfig = field(default_factory=DurationConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)


def _dataclass_to_dict(obj) -> dict:
    """Recursively convert a dataclass to a plain dict for YAML serialization."""
    if hasattr(obj, "__dataclass_fields__"):
        result = {}
        for name in obj.__dataclass_fields__:
            result[name] = _dataclass_to_dict(getattr(obj, name))
        return result
    elif isinstance(obj, list):
        return [_dataclass_to_dict(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    return obj


def _dict_to_dataclass(cls, data: dict):
    """Recursively convert a dict to a dataclass instance."""
    if not hasattr(cls, "__dataclass_fields__"):
        return data

    kwargs = {}
    for name, f in cls.__dataclass_fields__.items():
        if name not in data:
            continue
        value = data[name]
        field_type = f.type

        # Resolve string type annotations
        if isinstance(field_type, str):
            field_type = eval(field_type)

        # Handle nested dataclasses
        if hasattr(field_type, "__dataclass_fields__") and isinstance(value, dict):
            kwargs[name] = _dict_to_dataclass(field_type, value)
        else:
            kwargs[name] = value

    return cls(**kwargs)


def load_config(path: Optional[Path] = None) -> DiscoveryConfig:
    """
    Load configuration from YAML file.

    Falls back to defaults if file doesn't exist.
    """
    config_path = path or DISCOVERY_CONFIG_FILE

    if not config_path.exists():
        logger.info(f"No config file at {config_path}, using defaults")
        return DiscoveryConfig()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        config = _dict_to_dataclass(DiscoveryConfig, data)
        logger.info(f"Loaded config from {config_path}")
        return config

    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in config file: {e}")
    except Exception as e:
        raise ConfigError(f"Failed to load config: {e}")


def save_config(config: DiscoveryConfig, path: Optional[Path] = None) -> None:
    """Save configuration to YAML file."""
    config_path = path or DISCOVERY_CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data = _dataclass_to_dict(config)

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    logger.info(f"Config saved to {config_path}")


def generate_default_config(path: Optional[Path] = None) -> Path:
    """Generate a well-commented default configuration file."""
    config_path = path or DISCOVERY_CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)

    content = """\
# YT Audio Filter - Discovery Configuration
# This file controls autonomous video discovery and processing.

search:
  # Turkish search queries — focused on independent/educational content
  # Avoids official show names (which trigger Content ID)
  queries:
    - "cocuk sarkisi turkce animasyon"
    - "egitici cocuk videolari turkce"
    - "turkce cocuk cizgi film derleme"
    - "cocuklar icin renkleri ogreniyorum"
    - "hayvanlar cocuk sarkisi turkce"
    - "turkce ninni animasyon"
    - "cocuk masallari animasyon turkce"
    - "sayilari ogreniyorum cocuklar icin"
    - "turkce alfabe cocuk sarkisi"
    - "bebek sarkilari turkce animasyon"

  language: "tr"           # YouTube relevanceLanguage
  region: "TR"             # YouTube regionCode
  category_id: "24"        # Entertainment
  max_results_per_query: 10
  video_duration: "medium" # short (<4min), medium (4-20min), long (>20min)
  order: "date"            # date, relevance, viewCount
  published_after_days: 90 # Only search videos from last N days

channels:
  # Channels to always include (bypasses copyright scoring)
  whitelisted_channels: []

  # Channels to NEVER process (official/studio channels with Content ID)
  blacklisted_channels:
    - "@cartoonnetworkturkiye"
    - "@TRTCocuk"
    - "@niloyatv"
    - "@KukuliTurkce"
    - "@PepeeCizgiFilm"
    - "@RafadanTayfa"
    - "@KralSakir"
    - "@DisneychannelTR"
    - "@MinikaCOCUK"
    - "@MinikaGO"
    - "@dusyeri"
    - "@BaykusHopHop"
    - "@PirilCizgiFilm"
    - "@SevimliDostlar"
    - "@CoComelon"
    - "@BabyBusTurkce"
    - "@LittleAngel"
    - "@BlippiTurkce"

  # Videos with these keywords in title are skipped
  blacklisted_keywords:
    - "trailer"
    - "fragman"
    - "reklam"
    - "Gumball"
    - "Clarence"
    - "Ben 10"
    - "Tom and Jerry"
    - "Tom ve Jerry"
    - "Scooby"
    - "Cartoon Network"
    - "Disney"

copyright:
  # Videos with risk score above this are rejected (0.0 = safe, 1.0 = high risk)
  max_risk_score: 0.5
  base_score: 0.3

  # Score adjustments (stricter to avoid Content ID claims)
  official_channel_penalty: 0.35   # Verified channels with >10K subs
  compilation_channel_bonus: -0.15 # Only applies to small channels
  creative_commons_bonus: -0.3     # CC license gets -0.3
  high_sub_count_threshold: 100000 # Channels above this get penalty
  high_sub_penalty: 0.2
  official_title_penalty: 0.2      # "resmi"/"official" in title

  # Custom keyword penalties (keyword: score_delta)
  keyword_penalties:
    "resmi kanal": 0.3
    "official": 0.3
    "trt": 0.3

quota:
  daily_quota_limit: 10000   # YouTube Data API free tier
  search_cost: 100           # Cost per search.list call
  video_details_cost: 1      # Cost per videos.list call
  channel_details_cost: 1    # Cost per channels.list call
  max_searches_per_run: 20   # Safety cap (20 * 100 = 2000 units)

duration:
  min_seconds: 600   # 10 minutes
  max_seconds: 1800  # 30 minutes

scheduler:
  videos_per_run: 1
  device: "cpu"        # auto, cpu, cuda
  model_name: "htdemucs"
  privacy: "unlisted"  # public, unlisted, private
  output_dir: "output"
"""

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"Default config generated at {config_path}")
    return config_path


def get_api_key() -> Optional[str]:
    """Get YouTube Data API key from environment."""
    return os.environ.get("YOUTUBE_API_KEY")
