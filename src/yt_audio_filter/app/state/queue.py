"""Processing queue state management."""

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional
from uuid import uuid4


# Queue persistence file
QUEUE_FILE = Path(__file__).parent.parent.parent.parent.parent / "data" / "queue.json"


class QueueStatus(Enum):
    """Status of a queue item."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class QueueItem:
    """A single item in the processing queue."""
    id: str
    url: str
    title: str
    thumbnail_url: str
    status: QueueStatus = QueueStatus.PENDING
    progress: int = 0
    current_stage: str = ""
    error_message: str = ""
    output_path: str = ""
    uploaded_url: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "thumbnail_url": self.thumbnail_url,
            "status": self.status.value,
            "progress": self.progress,
            "current_stage": self.current_stage,
            "error_message": self.error_message,
            "output_path": self.output_path,
            "uploaded_url": self.uploaded_url,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QueueItem":
        """Create from dictionary."""
        data = data.copy()
        data["status"] = QueueStatus(data["status"])
        return cls(**data)


class QueueManager:
    """Manages the processing queue with persistence."""

    def __init__(self):
        self._items: List[QueueItem] = []
        self._lock = threading.Lock()
        self._processing = False
        self._current_item: Optional[QueueItem] = None
        self._load()

    def _load(self) -> None:
        """Load queue from file."""
        if QUEUE_FILE.exists():
            try:
                with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._items = [QueueItem.from_dict(item) for item in data]
            except Exception:
                self._items = []

    def _save(self) -> None:
        """Save queue to file."""
        QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump([item.to_dict() for item in self._items], f, indent=2)

    def add(self, url: str, title: str, thumbnail_url: str = "") -> QueueItem:
        """
        Add an item to the queue.

        Args:
            url: YouTube video URL
            title: Video title
            thumbnail_url: Thumbnail URL

        Returns:
            Created QueueItem
        """
        with self._lock:
            item = QueueItem(
                id=str(uuid4()),
                url=url,
                title=title,
                thumbnail_url=thumbnail_url or f"https://i.ytimg.com/vi/{url.split('v=')[-1].split('&')[0]}/hqdefault.jpg",
            )
            self._items.append(item)
            self._save()
            return item

    def add_batch(self, videos: List[Dict]) -> List[QueueItem]:
        """
        Add multiple videos to queue.

        Args:
            videos: List of dicts with url, title, thumbnail_url

        Returns:
            List of created QueueItems
        """
        items = []
        with self._lock:
            for video in videos:
                item = QueueItem(
                    id=str(uuid4()),
                    url=video["url"],
                    title=video["title"],
                    thumbnail_url=video.get("thumbnail_url", ""),
                )
                self._items.append(item)
                items.append(item)
            self._save()
        return items

    def get_all(self) -> List[QueueItem]:
        """Get all queue items."""
        with self._lock:
            return list(self._items)

    def get_by_id(self, item_id: str) -> Optional[QueueItem]:
        """Get item by ID."""
        with self._lock:
            for item in self._items:
                if item.id == item_id:
                    return item
        return None

    def get_pending(self) -> List[QueueItem]:
        """Get all pending items."""
        with self._lock:
            return [item for item in self._items if item.status == QueueStatus.PENDING]

    def get_next_pending(self) -> Optional[QueueItem]:
        """Get next pending item."""
        with self._lock:
            for item in self._items:
                if item.status == QueueStatus.PENDING:
                    return item
        return None

    def get_next_pending_batch(self, max_items: int = 2) -> List[QueueItem]:
        """Get up to max_items pending items for parallel processing."""
        with self._lock:
            pending = [item for item in self._items if item.status == QueueStatus.PENDING]
            return pending[:max_items]

    def count_processing(self) -> int:
        """Count currently processing items."""
        with self._lock:
            return sum(1 for item in self._items if item.status == QueueStatus.PROCESSING)

    def update_status(
        self,
        item_id: str,
        status: QueueStatus,
        progress: int = None,
        current_stage: str = None,
        error_message: str = None,
        output_path: str = None,
        uploaded_url: str = None,
    ) -> None:
        """Update item status."""
        with self._lock:
            for item in self._items:
                if item.id == item_id:
                    item.status = status
                    if progress is not None:
                        item.progress = progress
                    if current_stage is not None:
                        item.current_stage = current_stage
                    if error_message is not None:
                        item.error_message = error_message
                    if output_path is not None:
                        item.output_path = output_path
                    if uploaded_url is not None:
                        item.uploaded_url = uploaded_url
                    if status in (QueueStatus.COMPLETED, QueueStatus.FAILED, QueueStatus.CANCELLED):
                        item.completed_at = datetime.now().isoformat()
                    self._save()
                    break

    def remove(self, item_id: str) -> bool:
        """Remove item from queue."""
        with self._lock:
            for i, item in enumerate(self._items):
                if item.id == item_id:
                    del self._items[i]
                    self._save()
                    return True
        return False

    def clear_completed(self) -> int:
        """Remove all completed items. Returns count removed."""
        with self._lock:
            original_count = len(self._items)
            self._items = [
                item for item in self._items
                if item.status not in (QueueStatus.COMPLETED, QueueStatus.FAILED, QueueStatus.CANCELLED)
            ]
            self._save()
            return original_count - len(self._items)

    def cancel_all(self) -> int:
        """Cancel all pending items. Returns count cancelled."""
        count = 0
        with self._lock:
            for item in self._items:
                if item.status == QueueStatus.PENDING:
                    item.status = QueueStatus.CANCELLED
                    count += 1
            self._save()
        return count

    @property
    def is_processing(self) -> bool:
        """Check if queue is currently processing."""
        return self._processing

    @property
    def current_item(self) -> Optional[QueueItem]:
        """Get currently processing item."""
        return self._current_item

    def stats(self) -> Dict:
        """Get queue statistics."""
        with self._lock:
            stats = {
                "total": len(self._items),
                "pending": 0,
                "processing": 0,
                "completed": 0,
                "failed": 0,
                "cancelled": 0,
            }
            for item in self._items:
                stats[item.status.value] += 1
            return stats
