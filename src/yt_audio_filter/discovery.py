"""Autonomous video discovery engine using YouTube Data API v3.

Searches for Turkish children's content, evaluates copyright risk,
and returns ranked candidates for processing.
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .config import (
    DiscoveryConfig,
    get_api_key,
    load_config,
    generate_default_config,
)
from .copyright_scorer import CopyrightScorer
from .exceptions import DiscoveryError, QuotaExceededError
from .logger import get_logger, setup_logger
from .quota_tracker import QuotaTracker

logger = get_logger()


@dataclass
class ChannelInfo:
    """Channel metadata from YouTube Data API."""

    channel_id: str
    title: str
    subscriber_count: int
    video_count: int
    is_verified: bool
    custom_url: str = ""


@dataclass
class VideoCandidate:
    """A discovered video with metadata and risk scoring."""

    video_id: str
    title: str
    url: str
    channel_id: str
    channel_title: str
    duration: int  # seconds
    view_count: int
    published_at: str  # ISO 8601
    description: str = ""
    tags: List[str] = field(default_factory=list)
    license: str = "youtube"  # "youtube" or "creativeCommon"
    thumbnail_url: str = ""
    # Scoring fields
    copyright_risk_score: float = 0.0
    risk_reasons: List[str] = field(default_factory=list)
    # Channel info (populated by enrichment)
    channel_info: Optional[ChannelInfo] = None

    def to_video_info(self):
        """Convert to VideoInfo for compatibility with existing pipeline."""
        from .scraper import VideoInfo

        upload_date = self.published_at[:10].replace("-", "") if self.published_at else ""

        return VideoInfo(
            video_id=self.video_id,
            title=self.title,
            url=self.url,
            duration=self.duration,
            view_count=self.view_count,
            upload_date=upload_date,
            thumbnail_url=self.thumbnail_url,
            channel_id=self.channel_id,
        )


def _parse_iso8601_duration(duration_str: str) -> int:
    """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
    match = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or ""
    )
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


class YouTubeAPIClient:
    """Thin wrapper for YouTube Data API v3 with quota tracking."""

    def __init__(self, api_key: str, quota_tracker: QuotaTracker):
        try:
            from googleapiclient.discovery import build
        except ImportError:
            raise DiscoveryError(
                "google-api-python-client not installed",
                "Install with: pip install google-api-python-client",
            )

        self._service = build("youtube", "v3", developerKey=api_key)
        self._quota = quota_tracker

    def search_videos(
        self,
        query: str,
        max_results: int = 10,
        language: str = "tr",
        region: str = "TR",
        category_id: str = "24",
        video_duration: str = "medium",
        order: str = "date",
        published_after: Optional[str] = None,
    ) -> List[dict]:
        """Execute a search.list call. Costs 100 quota units."""
        self._quota.record_usage(100, f"search: {query}")

        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": max_results,
            "relevanceLanguage": language,
            "regionCode": region,
            "videoDuration": video_duration,
            "order": order,
            "safeSearch": "strict",
        }

        if category_id:
            params["videoCategoryId"] = category_id

        if published_after:
            params["publishedAfter"] = published_after

        try:
            response = self._service.search().list(**params).execute()
            return response.get("items", [])
        except Exception as e:
            raise DiscoveryError(f"YouTube search failed for '{query}': {e}")

    def get_video_details(self, video_ids: List[str]) -> List[dict]:
        """Get video details via videos.list. Costs 1 unit per call (up to 50 IDs)."""
        if not video_ids:
            return []

        results = []
        # Batch in groups of 50
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            self._quota.record_usage(1, f"video_details: {len(batch)} videos")

            try:
                response = (
                    self._service.videos()
                    .list(
                        part="contentDetails,statistics,status,snippet",
                        id=",".join(batch),
                    )
                    .execute()
                )
                results.extend(response.get("items", []))
            except Exception as e:
                logger.warning(f"Failed to get video details: {e}")

        return results

    def get_channel_details(self, channel_ids: List[str]) -> List[dict]:
        """Get channel details via channels.list. Costs 1 unit per call (up to 50 IDs)."""
        if not channel_ids:
            return []

        results = []
        for i in range(0, len(channel_ids), 50):
            batch = channel_ids[i : i + 50]
            self._quota.record_usage(1, f"channel_details: {len(batch)} channels")

            try:
                response = (
                    self._service.channels()
                    .list(
                        part="snippet,statistics,status",
                        id=",".join(batch),
                    )
                    .execute()
                )
                results.extend(response.get("items", []))
            except Exception as e:
                logger.warning(f"Failed to get channel details: {e}")

        return results


def _search_all_queries(
    client: YouTubeAPIClient,
    config: DiscoveryConfig,
) -> List[dict]:
    """Run all configured search queries and collect raw results."""
    all_results = []
    search_cfg = config.search

    # Calculate published_after date
    published_after = None
    if search_cfg.published_after_days > 0:
        after_date = datetime.now(timezone.utc) - timedelta(days=search_cfg.published_after_days)
        published_after = after_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    searches_done = 0
    max_searches = config.quota.max_searches_per_run

    for query in search_cfg.queries:
        if searches_done >= max_searches:
            logger.info(f"Reached max searches per run ({max_searches}), stopping discovery")
            break

        try:
            results = client.search_videos(
                query=query,
                max_results=search_cfg.max_results_per_query,
                language=search_cfg.language,
                region=search_cfg.region,
                category_id=search_cfg.category_id,
                video_duration=search_cfg.video_duration,
                order=search_cfg.order,
                published_after=published_after,
            )
            all_results.extend(results)
            searches_done += 1
            logger.debug(f"Search '{query}': {len(results)} results")

        except QuotaExceededError:
            logger.warning("API quota exhausted, stopping discovery")
            break
        except DiscoveryError as e:
            logger.warning(f"Search failed for '{query}': {e}")
            continue

    logger.info(f"Completed {searches_done} searches, {len(all_results)} raw results")
    return all_results


def _deduplicate_search_results(results: List[dict]) -> Dict[str, dict]:
    """Deduplicate search results by video ID."""
    unique = {}
    for item in results:
        video_id = item.get("id", {}).get("videoId")
        if video_id and video_id not in unique:
            unique[video_id] = item
    return unique


def _enrich_candidates(
    client: YouTubeAPIClient,
    candidates: Dict[str, dict],
) -> List[VideoCandidate]:
    """Enrich search results with full video and channel details."""
    video_ids = list(candidates.keys())
    if not video_ids:
        return []

    # Get video details (duration, license, tags, etc.)
    video_details = client.get_video_details(video_ids)
    video_detail_map = {v["id"]: v for v in video_details}

    # Collect unique channel IDs for batch lookup
    channel_ids = set()
    for vid in video_details:
        ch_id = vid.get("snippet", {}).get("channelId", "")
        if ch_id:
            channel_ids.add(ch_id)

    # Get channel details
    channel_details = client.get_channel_details(list(channel_ids))
    channel_map: Dict[str, ChannelInfo] = {}
    for ch in channel_details:
        stats = ch.get("statistics", {})
        status = ch.get("status", {})
        snippet = ch.get("snippet", {})
        channel_map[ch["id"]] = ChannelInfo(
            channel_id=ch["id"],
            title=snippet.get("title", ""),
            subscriber_count=int(stats.get("subscriberCount", 0)),
            video_count=int(stats.get("videoCount", 0)),
            is_verified=status.get("isLinked", False),
            custom_url=snippet.get("customUrl", ""),
        )

    # Build VideoCandidate list
    enriched = []
    for video_id, search_item in candidates.items():
        snippet = search_item.get("snippet", {})
        detail = video_detail_map.get(video_id)

        if not detail:
            logger.debug(f"No details for video {video_id}, skipping")
            continue

        detail_snippet = detail.get("snippet", {})
        content_details = detail.get("contentDetails", {})
        statistics = detail.get("statistics", {})
        status = detail.get("status", {})

        duration = _parse_iso8601_duration(content_details.get("duration", ""))
        channel_id = detail_snippet.get("channelId", snippet.get("channelId", ""))

        # Get best thumbnail
        thumbnails = detail_snippet.get("thumbnails", {})
        thumbnail_url = (
            thumbnails.get("high", {}).get("url")
            or thumbnails.get("medium", {}).get("url")
            or thumbnails.get("default", {}).get("url", "")
        )

        candidate = VideoCandidate(
            video_id=video_id,
            title=detail_snippet.get("title", snippet.get("title", "")),
            url=f"https://www.youtube.com/watch?v={video_id}",
            channel_id=channel_id,
            channel_title=detail_snippet.get("channelTitle", snippet.get("channelTitle", "")),
            duration=duration,
            view_count=int(statistics.get("viewCount", 0)),
            published_at=detail_snippet.get("publishedAt", snippet.get("publishedAt", "")),
            description=detail_snippet.get("description", ""),
            tags=detail_snippet.get("tags", []) or [],
            license=status.get("license", "youtube"),
            thumbnail_url=thumbnail_url,
            channel_info=channel_map.get(channel_id),
        )
        enriched.append(candidate)

    logger.info(f"Enriched {len(enriched)} candidates with full metadata")
    return enriched


def discover_videos(
    config: DiscoveryConfig,
    api_key: str,
    processed_ids: Set[str],
    max_candidates: int = 20,
) -> List[VideoCandidate]:
    """Main discovery pipeline.

    1. Run search queries from config
    2. Deduplicate results
    3. Exclude already processed IDs
    4. Enrich with video and channel details
    5. Filter by duration
    6. Score copyright risk
    7. Filter by risk threshold
    8. Sort by score (lowest risk first)
    9. Return top N candidates

    Args:
        config: Discovery configuration
        api_key: YouTube Data API key
        processed_ids: Set of already processed video IDs
        max_candidates: Maximum candidates to return

    Returns:
        List of VideoCandidate objects sorted by risk score (lowest first)
    """
    quota_tracker = QuotaTracker(daily_limit=config.quota.daily_quota_limit)
    client = YouTubeAPIClient(api_key, quota_tracker)
    scorer = CopyrightScorer(config.copyright, config.channels)

    # Step 1: Search
    raw_results = _search_all_queries(client, config)
    if not raw_results:
        logger.warning("No search results found")
        return []

    # Step 2: Deduplicate
    unique = _deduplicate_search_results(raw_results)
    logger.info(f"Deduplicated to {len(unique)} unique videos")

    # Step 3: Exclude already processed
    for vid_id in list(unique.keys()):
        if vid_id in processed_ids:
            del unique[vid_id]
    logger.info(f"After excluding processed: {len(unique)} candidates")

    if not unique:
        return []

    # Step 4: Enrich with details
    candidates = _enrich_candidates(client, unique)

    # Step 5: Filter by duration
    duration_cfg = config.duration
    candidates = [
        c
        for c in candidates
        if duration_cfg.min_seconds <= c.duration <= duration_cfg.max_seconds
    ]
    logger.info(
        f"After duration filter ({duration_cfg.min_seconds}-{duration_cfg.max_seconds}s): "
        f"{len(candidates)} candidates"
    )

    # Step 6: Score copyright risk
    for candidate in candidates:
        score, reasons = scorer.score(candidate)
        candidate.copyright_risk_score = score
        candidate.risk_reasons = reasons

    # Step 7: Filter by risk threshold
    max_risk = config.copyright.max_risk_score
    passed = [c for c in candidates if c.copyright_risk_score <= max_risk]
    rejected = [c for c in candidates if c.copyright_risk_score > max_risk]

    if rejected:
        logger.info(f"Rejected {len(rejected)} videos exceeding risk threshold {max_risk}:")
        for r in rejected[:5]:
            logger.debug(
                f"  REJECTED [{r.copyright_risk_score:.2f}] {r.title} "
                f"({', '.join(r.risk_reasons)})"
            )

    # Step 8: Sort by risk score (lowest first)
    passed.sort(key=lambda c: c.copyright_risk_score)

    # Step 9: Return top N
    result = passed[:max_candidates]

    logger.info(f"Discovery complete: {len(result)} candidates selected")
    for i, c in enumerate(result, 1):
        logger.info(
            f"  {i}. [{c.copyright_risk_score:.2f}] {c.title} "
            f"({c.duration // 60}min, {c.channel_title})"
        )

    # Log quota usage
    logger.info(
        f"API quota used this run: {quota_tracker.get_today_usage()} / "
        f"{config.quota.daily_quota_limit}"
    )

    return result


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for discovery CLI."""
    parser = argparse.ArgumentParser(
        prog="yt-discover",
        description="Discover Turkish children's videos from YouTube for processing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  yt-discover --dry-run                     Preview what would be discovered
  yt-discover --api-key YOUR_KEY            Run discovery with API key
  yt-discover --config my_config.yaml       Use custom config
  yt-discover --init-config                 Generate default config file
  yt-discover --max-candidates 10           Limit output to 10 videos
        """,
    )

    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="YouTube Data API key (or set YOUTUBE_API_KEY env var)",
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to discovery config YAML file",
    )

    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Generate default config file and exit",
    )

    parser.add_argument(
        "--max-candidates",
        type=int,
        default=20,
        help="Maximum number of candidates to return (default: 20)",
    )

    parser.add_argument(
        "--max-risk",
        type=float,
        default=None,
        help="Override max copyright risk score (0.0-1.0)",
    )

    parser.add_argument(
        "--processed-file",
        type=Path,
        default=Path("processed_videos.json"),
        help="Path to processed videos tracking file",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only discover and print, don't save anything",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress output except errors",
    )

    return parser


def main(args=None) -> int:
    """Main entry point for discovery CLI."""
    try:
        parser = create_parser()
        parsed = parser.parse_args(args)

        setup_logger(verbose=parsed.verbose, quiet=parsed.quiet)

        # Handle --init-config
        if parsed.init_config:
            path = generate_default_config(parsed.config)
            print(f"Default config generated at: {path}")
            return 0

        # Load config
        config = load_config(parsed.config)

        # Override max risk if specified
        if parsed.max_risk is not None:
            config.copyright.max_risk_score = parsed.max_risk

        # Get API key
        api_key = parsed.api_key or get_api_key()
        if not api_key:
            print(
                "Error: YouTube Data API key required.\n"
                "Set YOUTUBE_API_KEY environment variable or use --api-key flag.\n\n"
                "To get an API key:\n"
                "1. Go to https://console.cloud.google.com/\n"
                "2. Create project -> Enable YouTube Data API v3\n"
                "3. Create an API key under Credentials",
                file=sys.stderr,
            )
            return 1

        # Load processed IDs
        from .scheduler import load_processed_videos

        processed_ids = load_processed_videos(parsed.processed_file)

        # Run discovery
        candidates = discover_videos(
            config=config,
            api_key=api_key,
            processed_ids=processed_ids,
            max_candidates=parsed.max_candidates,
        )

        if not candidates:
            print("No eligible videos found.")
            return 0

        # Print results
        print(f"\n{'=' * 70}")
        print(f" Discovered {len(candidates)} video candidates")
        print(f"{'=' * 70}\n")

        for i, c in enumerate(candidates, 1):
            risk_label = "LOW" if c.copyright_risk_score < 0.3 else (
                "MED" if c.copyright_risk_score < 0.6 else "HIGH"
            )
            print(f"{i:2d}. [{risk_label} {c.copyright_risk_score:.2f}] {c.title}")
            print(f"    Channel: {c.channel_title} | Duration: {c.duration // 60}min")
            print(f"    URL: {c.url}")
            print(f"    Reasons: {', '.join(c.risk_reasons)}")
            print()

        return 0

    except DiscoveryError as e:
        print(f"Discovery error: {e}", file=sys.stderr)
        return 1

    except KeyboardInterrupt:
        print("\nCancelled by user", file=sys.stderr)
        return 130

    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
