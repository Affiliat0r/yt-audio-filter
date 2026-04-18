"""Persistent state tracking for processed audio+visual pairs.

A tiny JSON store so we don't re-produce the same combination across runs.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .logger import get_logger

logger = get_logger()

DEFAULT_STATE_PATH = Path("state/processed_pairs.json")


@dataclass
class ProcessedPair:
    audio_id: str
    video_id: str
    uploaded_at: str  # ISO-8601 UTC
    uploaded_video_id: Optional[str] = None  # YouTube ID of the upload, if any
    output_path: Optional[str] = None


@dataclass
class PairState:
    pairs: List[ProcessedPair] = field(default_factory=list)

    def contains(self, audio_id: str, video_id: str) -> bool:
        return any(p.audio_id == audio_id and p.video_id == video_id for p in self.pairs)

    def add(
        self,
        audio_id: str,
        video_id: str,
        uploaded_video_id: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> ProcessedPair:
        entry = ProcessedPair(
            audio_id=audio_id,
            video_id=video_id,
            uploaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            uploaded_video_id=uploaded_video_id,
            output_path=output_path,
        )
        self.pairs.append(entry)
        return entry


def load_state(path: Path = DEFAULT_STATE_PATH) -> PairState:
    path = Path(path)
    if not path.exists():
        return PairState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.warning(f"Corrupt state file {path}: {e}. Starting with empty state.")
        return PairState()
    entries = raw.get("pairs", [])
    pairs = [ProcessedPair(**e) for e in entries if isinstance(e, dict)]
    return PairState(pairs=pairs)


def save_state(state: PairState, path: Path = DEFAULT_STATE_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"pairs": [asdict(p) for p in state.pairs]}, indent=2),
        encoding="utf-8",
    )
