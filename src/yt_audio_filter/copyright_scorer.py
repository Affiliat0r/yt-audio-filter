"""Heuristic copyright risk scoring for discovered videos."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Set, Tuple

from .config import ChannelConfig, CopyrightConfig
from .logger import get_logger

if TYPE_CHECKING:
    from .discovery import ChannelInfo, VideoCandidate

logger = get_logger()

# Turkish signals that indicate compilation/fan content (lower risk)
# NOTE: "tam bölüm" removed — on official channels it means "full episode" (high risk),
# only truly safe on small fan channels which we detect separately.
COMPILATION_SIGNALS = [
    "derleme",
    "mix",
    "koleksiyon",
    "toplama",
    "compilation",
    "collection",
    "kesintisiz",
    "peş peşe",
    "art arda",
]

# Signals that indicate official/original content (higher risk)
OFFICIAL_SIGNALS = [
    "resmi",
    "official",
    "orijinal",
    "yeni bölüm",
    "ilk gösterim",
    "premiere",
]


class CopyrightScorer:
    """Heuristic copyright risk scoring.

    Score range: 0.0 (safe) to 1.0 (high risk).
    Default threshold: reject if score > 0.6.
    """

    def __init__(self, config: CopyrightConfig, channel_config: ChannelConfig):
        self.config = config
        self.channel_config = channel_config
        self._whitelisted_ids: Optional[Set[str]] = None
        self._blacklisted_ids: Optional[Set[str]] = None

    def _normalize_channel_id(self, channel: str) -> str:
        """Extract a normalized channel identifier for comparison."""
        channel = channel.strip().lower()
        # Remove URL prefix
        for prefix in [
            "https://www.youtube.com/channel/",
            "https://www.youtube.com/",
            "http://www.youtube.com/channel/",
            "http://www.youtube.com/",
        ]:
            if channel.startswith(prefix):
                channel = channel[len(prefix) :]
                break
        return channel.rstrip("/")

    def _is_whitelisted(self, candidate: "VideoCandidate") -> bool:
        """Check if the candidate's channel is whitelisted."""
        channel_ids = {
            self._normalize_channel_id(ch)
            for ch in self.channel_config.whitelisted_channels
        }

        candidate_id = self._normalize_channel_id(candidate.channel_id)
        candidate_title = candidate.channel_title.strip().lower()

        return candidate_id in channel_ids or candidate_title in channel_ids

    def _is_blacklisted(self, candidate: "VideoCandidate") -> bool:
        """Check if the candidate's channel is blacklisted."""
        channel_ids = {
            self._normalize_channel_id(ch)
            for ch in self.channel_config.blacklisted_channels
        }

        candidate_id = self._normalize_channel_id(candidate.channel_id)
        candidate_title = candidate.channel_title.strip().lower()

        return candidate_id in channel_ids or candidate_title in channel_ids

    def _has_blacklisted_keyword(self, candidate: "VideoCandidate") -> bool:
        """Check if title contains blacklisted keywords."""
        title_lower = candidate.title.lower()
        return any(kw.lower() in title_lower for kw in self.channel_config.blacklisted_keywords)

    def score(self, candidate: "VideoCandidate") -> Tuple[float, List[str]]:
        """Calculate copyright risk score for a video candidate.

        Returns:
            (score, reasons) tuple where score is 0.0-1.0 and reasons explain the score.
        """
        reasons: List[str] = []

        # Rule 0: Blacklisted keywords in title always reject
        if self._has_blacklisted_keyword(candidate):
            return (1.0, ["blacklisted keyword in title"])

        # Rule 1: Whitelisted channels always pass
        if self._is_whitelisted(candidate):
            return (0.0, ["whitelisted channel"])

        # Rule 2: Blacklisted channels always fail
        if self._is_blacklisted(candidate):
            return (1.0, ["blacklisted channel"])

        # Start from base score
        score = self.config.base_score

        # Rule 3: Creative Commons license
        if candidate.license == "creativeCommon":
            score += self.config.creative_commons_bonus
            reasons.append("Creative Commons license")

        # Rule 4: Channel subscriber count (primary signal for Content ID risk)
        # High subscriber channels almost always use Content ID.
        title_lower = candidate.title.lower()
        desc_lower = (candidate.description or "").lower()

        if candidate.channel_info:
            subs = candidate.channel_info.subscriber_count

            if subs > self.config.high_sub_count_threshold:
                score += self.config.high_sub_penalty
                reasons.append(
                    f"high subscribers ({subs:,})"
                )

            # Verified channels with significant following are likely official
            if candidate.channel_info.is_verified and subs > 10_000:
                score += self.config.official_channel_penalty
                reasons.append("verified channel with significant following")

        # Rule 5: Compilation signals in title/description (lower risk)
        # Only apply bonus for small channels — on large channels, compilations
        # are still official content.
        is_small_channel = (
            candidate.channel_info is None
            or candidate.channel_info.subscriber_count < self.config.high_sub_count_threshold
        )
        if is_small_channel and any(
            signal in title_lower or signal in desc_lower
            for signal in COMPILATION_SIGNALS
        ):
            score += self.config.compilation_channel_bonus
            reasons.append("compilation/mix content signal (small channel)")

        # Rule 6: Official content signals in title (higher risk)
        if any(signal in title_lower for signal in OFFICIAL_SIGNALS):
            score += self.config.official_title_penalty
            reasons.append("official content signal in title")

        # Rule 7: Custom keyword penalties from config
        for keyword, penalty in self.config.keyword_penalties.items():
            if keyword.lower() in title_lower or keyword.lower() in desc_lower:
                score += penalty
                reasons.append(f"keyword match: {keyword}")

        # Clamp to [0.0, 1.0]
        score = max(0.0, min(1.0, score))

        if not reasons:
            reasons.append(f"base score ({self.config.base_score})")

        return (score, reasons)
