"""YouTube Data API quota usage tracking."""

import json
from datetime import date
from pathlib import Path
from typing import Optional

from .exceptions import QuotaExceededError
from .logger import get_logger

logger = get_logger()

QUOTA_DIR = Path.home() / ".yt-audio-filter"
QUOTA_FILE = QUOTA_DIR / "api_quota_usage.json"


class QuotaTracker:
    """Tracks YouTube Data API quota usage per day.

    YouTube Data API v3 provides 10,000 units/day for free.
    search.list costs 100 units, videos.list costs 1 unit, channels.list costs 1 unit.
    """

    def __init__(self, daily_limit: int = 10_000, file_path: Optional[Path] = None):
        self.daily_limit = daily_limit
        self.file_path = file_path or QUOTA_FILE
        self._data = self._load()

    def _load(self) -> dict:
        """Load quota data from disk."""
        if not self.file_path.exists():
            return {}

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Could not load quota data: {e}")
            return {}

    def _save(self) -> None:
        """Save quota data to disk."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def _today_key(self) -> str:
        return str(date.today())

    def _ensure_today(self) -> dict:
        """Ensure today's entry exists."""
        key = self._today_key()
        if key not in self._data:
            self._data[key] = {"total": 0, "operations": []}
        return self._data[key]

    def get_today_usage(self) -> int:
        """Get total quota units used today."""
        entry = self._ensure_today()
        return entry["total"]

    def can_afford(self, units: int) -> bool:
        """Check if we can afford a given number of quota units."""
        return self.get_today_usage() + units <= self.daily_limit

    def record_usage(self, units: int, operation: str) -> None:
        """Record quota usage for an API operation.

        Raises:
            QuotaExceededError: If recording would exceed daily limit.
        """
        if not self.can_afford(units):
            remaining = self.daily_limit - self.get_today_usage()
            raise QuotaExceededError(
                f"API quota exceeded: need {units} units but only {remaining} remaining",
                f"Daily limit: {self.daily_limit}, used today: {self.get_today_usage()}",
            )

        entry = self._ensure_today()
        entry["total"] += units
        entry["operations"].append({"operation": operation, "units": units})
        self._save()
        logger.debug(f"Quota: {operation} cost {units} units (total today: {entry['total']})")

    def get_remaining(self) -> int:
        """Get remaining quota units for today."""
        return max(0, self.daily_limit - self.get_today_usage())

    def cleanup_old_entries(self, keep_days: int = 30) -> None:
        """Remove entries older than keep_days."""
        today = date.today()
        keys_to_remove = []
        for key in self._data:
            try:
                entry_date = date.fromisoformat(key)
                if (today - entry_date).days > keep_days:
                    keys_to_remove.append(key)
            except ValueError:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._data[key]

        if keys_to_remove:
            self._save()
            logger.debug(f"Cleaned up {len(keys_to_remove)} old quota entries")
