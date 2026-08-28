"""Turn parsed work items into finished, published videos.

``workflow.parse_request`` says *what* the evening's output should be; this
module makes it. Per item it picks a source on YouTube, checks the source has
not been published already, renders it with the existing pipeline, uploads it,
and files it in a playlist.

Nothing here is new machinery — every step is a call into code that already
ships (``cartoon_search``, ``pipeline``, ``overlay_pipeline``, ``uploader``).
What this module adds is the ordering, the duplicate checks, and the promise
that **one bad item cannot take the run down with it**: a failure is recorded
against its item and the next one starts.

Two habits worth knowing before reading the code:

* ``dry_run=True`` resolves sources and runs the duplicate check for real, and
  only then stops. It is meant to be trusted before spending GPU hours, so it
  reports the actual pick, the actual title that would be published, and
  whether the playlist already exists — not a stub.
* Renders always run with ``upload=False`` and upload as a separate, explicit
  step, so a half-finished render never publishes itself.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from . import cartoon_catalog, cartoon_search, overlay_pipeline, uploader, youtube, yt_metadata
from .cartoon_catalog import CatalogVideo
from .exceptions import YTAudioFilterError
from .logger import get_logger
from .metadata import OverlayMetadata, load_metadata
from .workflow import CartoonItem, QuranItem, WorkItem, _title_case

logger = get_logger()

DEFAULT_STATE_PATH = Path("state/workflow_sources.json")
DEFAULT_METADATA_PATH = Path("examples/metadata-surah-arrahman.json")
DEFAULT_CACHE_DIR = Path("cache")
DEFAULT_OUTPUT_DIR = Path("output")

#: A full episode rather than a clip or a ten-hour compilation. Outside this
#: band the result is still usable, so it is a preference and not a filter —
#: nearest-miss candidates stay in the list, just further down.
PREFERRED_DURATION_SECONDS: Tuple[int, int] = (20 * 60, 45 * 60)

#: Enough candidates that a run can walk past the already-published ones.
#: Scaled by ``count`` because every repetition consumes at least one.
SEARCH_RESULTS_BASE = 25

#: ``on_event(kind, message, data)``. ``kind`` is a stable machine-readable
#: token; ``message`` is the human line; ``data`` carries the details.
EventCallback = Callable[[str, str, Dict[str, object]], None]


class WorkflowRunError(YTAudioFilterError):
    """An item could not be produced."""


# ---------------------------------------------------------------------------
# Local record of what this tool has already made
# ---------------------------------------------------------------------------


@dataclass
class ProcessedSource:
    """One source video that has been rendered from, and possibly published."""

    source_id: str
    kind: str  # "cartoon" | "quran"
    request: str  # the item text that led here, for forensics
    rendered_at: str  # ISO-8601 UTC
    uploaded_video_id: Optional[str] = None
    output_path: Optional[str] = None


@dataclass
class WorkflowState:
    """The on-disk memory that stops a rerun redoing last night's work.

    ``uploader.get_uploaded_source_ids`` is the authority on what is published,
    but it can only see finished uploads. A source that rendered and then
    failed to upload would otherwise be picked again on the next run, so it is
    recorded here the moment the render succeeds.
    """

    sources: List[ProcessedSource] = field(default_factory=list)

    def get(self, source_id: str) -> Optional[ProcessedSource]:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        return None

    def add(
        self,
        source_id: str,
        kind: str,
        request: str,
        output_path: Optional[str] = None,
        uploaded_video_id: Optional[str] = None,
    ) -> ProcessedSource:
        entry = ProcessedSource(
            source_id=source_id,
            kind=kind,
            request=request,
            rendered_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            uploaded_video_id=uploaded_video_id,
            output_path=output_path,
        )
        self.sources.append(entry)
        return entry


_STATE_FIELDS = set(ProcessedSource.__dataclass_fields__)


def load_state(path: Path = DEFAULT_STATE_PATH) -> WorkflowState:
    """Read the state file, treating any damage as "no history".

    A corrupt or newer-schema state file must not stop a night's production;
    the worst case of ignoring it is re-rendering something, which the channel
    scan then catches anyway.
    """
    path = Path(path)
    if not path.exists():
        return WorkflowState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Corrupt workflow state file {path}: {e}. Starting with empty state.")
        return WorkflowState()

    entries = raw.get("sources", []) if isinstance(raw, dict) else []
    sources: List[ProcessedSource] = []
    for entry in entries:
        if not isinstance(entry, dict) or "source_id" not in entry:
            continue
        known = {k: v for k, v in entry.items() if k in _STATE_FIELDS}
        known.setdefault("kind", "")
        known.setdefault("request", "")
        known.setdefault("rendered_at", "")
        sources.append(ProcessedSource(**known))
    return WorkflowState(sources=sources)


def save_state(state: WorkflowState, path: Path = DEFAULT_STATE_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sources": [asdict(s) for s in state.sources]}, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class SkippedSource:
    """A candidate that was passed over, and why."""

    video_id: str
    title: str
    reason: str


@dataclass
class ItemResult:
    """The outcome of one video: one repetition of one work item."""

    kind: str  # "cartoon" | "quran"
    label: str  # the request, as a human would say it
    #: None until derived: an unlabelled pasted link has no name of its own, so
    #: the playlist is named after the video's channel once that is known.
    playlist_name: Optional[str]
    index: int  # 1-based repetition within the item
    total: int  # the item's count
    dry_run: bool = False
    source_id: Optional[str] = None
    source_title: str = ""
    source_url: str = ""
    source_duration: int = 0
    planned_title: Optional[str] = None
    skipped: List[SkippedSource] = field(default_factory=list)
    #: Set when the item was deliberately not produced (a pasted link that is
    #: already on the channel). Not a failure — the run did the right thing.
    skipped_reason: Optional[str] = None
    rendered_path: Optional[Path] = None
    uploaded_video_id: Optional[str] = None
    playlist_id: Optional[str] = None
    playlist_created: bool = False
    playlist_error: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def uploaded_url(self) -> Optional[str]:
        if not self.uploaded_video_id:
            return None
        return f"https://youtube.com/watch?v={self.uploaded_video_id}"


@dataclass
class WorkflowSummary:
    """Everything the run did, in the order it did it."""

    results: List[ItemResult] = field(default_factory=list)
    dry_run: bool = False

    @property
    def failures(self) -> List[ItemResult]:
        return [r for r in self.results if not r.ok]

    @property
    def uploaded(self) -> List[ItemResult]:
        return [r for r in self.results if r.uploaded_video_id]

    @property
    def skipped(self) -> List[ItemResult]:
        return [r for r in self.results if r.ok and r.skipped_reason]

    @property
    def exit_code(self) -> int:
        """Non-zero when anything failed, so a scheduler notices.

        A deliberate skip is not a failure: refusing to republish something the
        channel already has is the correct outcome, not an error.
        """
        return 1 if self.failures else 0


# ---------------------------------------------------------------------------
# Candidate ranking and filtering
# ---------------------------------------------------------------------------


def _duration_distance(video: CatalogVideo) -> int:
    low, high = PREFERRED_DURATION_SECONDS
    if low <= video.duration <= high:
        return 0
    return low - video.duration if video.duration < low else video.duration - high


def prefer_episode_length(videos: Sequence[CatalogVideo]) -> List[CatalogVideo]:
    """Full-length episodes first, then the nearest misses.

    ``sorted`` is stable, so inside the preferred band YouTube's own relevance
    ordering survives — we only demote clips and compilations.
    """
    return sorted(videos, key=_duration_distance)


@lru_cache(maxsize=256)
def _term_pattern(term: str) -> "re.Pattern[str]":
    """Whole-word matcher for one exclusion term.

    ``\\b`` is wrong here: ``_`` is a word character, so ``\\bscary\\b`` misses
    ``Toys_Scary_Fun``. The lookarounds below use a letter class instead — the
    same fix the short surah names in ``surah_detector`` needed. Multi-word
    terms tolerate the separators titles actually use ("not too scary" matches
    "Too-Scary").
    """
    body = r"[\s_\-]+".join(re.escape(word) for word in term.split())
    return re.compile(rf"(?<![a-z]){body}(?![a-z])", re.IGNORECASE)


def excluded_by(title: str, terms: Sequence[str]) -> Optional[str]:
    """The first exclusion term present in ``title``, or None."""
    for term in terms:
        cleaned = term.strip()
        if not cleaned:
            continue
        if _term_pattern(cleaned).search(title):
            return cleaned
    return None


# ---------------------------------------------------------------------------
# Description helpers (shared with the CLI's plan printout)
# ---------------------------------------------------------------------------


def surah_names(numbers: Sequence[int]) -> List[str]:
    from .surah_detector import get_surah_info

    names: List[str] = []
    for number in numbers:
        try:
            names.append(get_surah_info(number).name)
        except (ValueError, KeyError):  # pragma: no cover - parser rejects these first
            names.append(str(number))
    return names


def describe_item(item: WorkItem) -> str:
    """One line naming what the item asks for."""
    if isinstance(item, CartoonItem):
        if item.url and item.label:
            return f"{item.label} ({item.url})"
        return item.query
    names = surah_names(item.surah_numbers)
    if len(names) > 2:
        span = f"{names[0]} … {names[-1]} ({len(names)} surahs)"
    else:
        span = " + ".join(names)
    return f"Quran ({span}, {item.reciter_name})"


def _item_kind(item: WorkItem) -> str:
    return "cartoon" if isinstance(item, CartoonItem) else "quran"


def is_link_item(item: WorkItem) -> bool:
    """True when the user pasted a link instead of describing a search."""
    return isinstance(item, CartoonItem) and bool(item.url)


#: Channel slug for a pasted link — it belongs to no scraped channel.
LINK_CHANNEL_SLUG = "__link__"


def _link_video(item: CartoonItem) -> CatalogVideo:
    """A stand-in ``CatalogVideo`` for a pasted link.

    Only the id and the url are real; the title and duration stay unknown until
    something fetches them. That is already enough for the duplicate check,
    which is the one thing that has to happen before any work starts.
    """
    return CatalogVideo(
        video_id=str(item.video_id),
        url=str(item.url),
        title=item.label or str(item.url),
        duration=0,
        view_count=0,
        upload_date="",
        thumbnail_url="",
        channel_slug=LINK_CHANNEL_SLUG,
    )


#: What every render targets unless told otherwise.
#:
#: 720p rather than 1080p because of what YouTube actually gives us. Protected
#: cartoons come down as format 18 — 360p combined — thanks to the SABR wall
#: (see CLAUDE.md). Rendering those at 1080p does not add a pixel of detail; it
#: just makes a bigger file out of the same picture. 720p is the honest ceiling
#: for a 2x enlargement of a 360p source, and it is still a large step up from
#: uploading 360p, because YouTube gives a 720p upload a noticeably better
#: bitrate ladder than a 360p one.
DEFAULT_HEIGHT = 720


def resolution_for(height: int) -> Tuple[int, int]:
    """A 16:9 resolution for the given height."""
    return (round(height * 16 / 9 / 2) * 2, height)


def scale_height_for(source_height: Optional[int], target: int) -> Optional[int]:
    """The height to re-encode a music-removal output to, or None to copy.

    Music removal copies the video stream untouched, which is lossless and
    fast. Enlarging means re-encoding, so it is only worth it when the source
    is genuinely smaller than the target: never downscale someone's good
    source, and never re-encode when ffprobe could not tell us the height.
    """
    if not source_height or source_height >= target:
        return None
    return target


def _probe_height(path: Path) -> Optional[int]:
    """The source's pixel height, or None when ffprobe cannot say.

    Never raises: a failed probe means "copy the stream", which is the safe
    outcome, not a reason to abandon a render.
    """
    try:
        from .ffmpeg import get_video_info

        info = get_video_info(path)
        height = info.get("height") if isinstance(info, dict) else None
        return int(height) if height else None
    except Exception as exc:  # noqa: BLE001 - falls back to copying
        logger.debug("Could not probe %s: %s", path, exc)
        return None


def _quran_output_name(item: QuranItem, video: CatalogVideo) -> str:
    numbers = item.surah_numbers
    if len(numbers) > 3:
        tag = f"{numbers[0]:03d}-{numbers[-1]:03d}"
    else:
        tag = "_".join(f"{n:03d}" for n in numbers)
    return f"quran_{item.reciter_slug}_{tag}_{video.video_id}.mp4"


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


class _Run:
    """One invocation. Holds the caches every item shares.

    The expensive lookups — the channel scan behind ``get_uploaded_source_ids``
    and the playlist list — are resolved once and reused, because they are the
    same answer for every item and each one costs an API round trip.
    """

    def __init__(
        self,
        *,
        dry_run: bool,
        cache_dir: Path,
        output_dir: Path,
        state_path: Path,
        metadata_path: Path,
        privacy: str,
        on_event: Optional[EventCallback],
        target_height: int = DEFAULT_HEIGHT,
    ) -> None:
        self.dry_run = dry_run
        self.cache_dir = Path(cache_dir)
        self.output_dir = Path(output_dir)
        self.state_path = Path(state_path)
        self.metadata_path = Path(metadata_path)
        self.privacy = privacy
        self.target_height = target_height
        self.on_event = on_event
        self.state = load_state(self.state_path)
        self._uploaded: Optional[Dict[str, dict]] = None
        self._playlists: Optional[Dict[str, str]] = None
        self._metadata: Optional[OverlayMetadata] = None
        # Sources handed out during this run. Two items with overlapping
        # searches must not both render the same video, and a repetition must
        # not re-pick a source whose render just failed.
        self._claimed: set = set()

    # -- plumbing ---------------------------------------------------------

    def emit(self, kind: str, message: str, **data: object) -> None:
        logger.info(message)
        if self.on_event is not None:
            self.on_event(kind, message, data)

    def uploaded_sources(self) -> Dict[str, dict]:
        """Source ids already published on the channel.

        Fail-soft: without credentials this returns ``{}`` and the local state
        file becomes the only duplicate check. Refusing to run would be worse —
        the channel scan is an optimisation over re-uploading, not a gate.
        """
        if self._uploaded is None:
            try:
                self._uploaded = uploader.get_uploaded_source_ids()
            except Exception as exc:  # noqa: BLE001 - a scan failure must not stop the run
                logger.warning(f"Could not read the channel's uploads: {exc}")
                self._uploaded = {}
        return self._uploaded

    def playlist_index(self) -> Dict[str, str]:
        """Lower-cased playlist title → id."""
        if self._playlists is None:
            index: Dict[str, str] = {}
            try:
                for playlist in uploader.list_playlists():
                    title = str(playlist.get("title", "")).strip().lower()
                    if title:
                        index[title] = str(playlist["id"])
            except Exception as exc:  # noqa: BLE001 - playlists never fail an item
                logger.warning(f"Could not list playlists: {exc}")
            self._playlists = index
        return self._playlists

    def metadata(self) -> OverlayMetadata:
        """The overlay metadata template, with the run's privacy applied.

        ``--privacy`` is the operator's intent for this run, so it wins over
        ``privacy_status`` in the JSON; otherwise a private smoke test would
        quietly publish because the template says ``public``.
        """
        if self._metadata is None:
            loaded = load_metadata(self.metadata_path)
            self._metadata = replace(loaded, privacy_status=self.privacy)
        return self._metadata

    # -- sourcing ---------------------------------------------------------

    def candidates(self, item: WorkItem) -> List[CatalogVideo]:
        max_results = max(SEARCH_RESULTS_BASE, item.count * 5)
        if isinstance(item, CartoonItem):
            self.emit("search", f"Searching YouTube for {item.query!r}", query=item.query)
            found = cartoon_search.search_videos(
                item.query, max_results=max_results, cache_dir=self.cache_dir
            )
            return prefer_episode_length(found)

        if item.background_query:
            self.emit(
                "search",
                f"Searching YouTube for background {item.background_query!r}",
                query=item.background_query,
            )
            found = cartoon_search.search_videos(
                item.background_query, max_results=max_results, cache_dir=self.cache_dir
            )
        else:
            # No background asked for, so use the curated channels the project
            # already trusts rather than guessing a search term.
            self.emit("search", "Using the curated cartoon catalog for the background")
            found = cartoon_catalog.list_videos(cache_dir=self.cache_dir)
        return list(found)

    def duplicate_reason(self, video: CatalogVideo) -> Optional[str]:
        """Why this source must not be produced again, or None if it is new."""
        published = self.uploaded_sources().get(video.video_id)
        if published:
            where = published.get("url") or published.get("uploaded_id") or "the channel"
            return f"already published as {where}"
        known = self.state.get(video.video_id)
        if known is not None:
            return f"already rendered ({known.rendered_at or 'an earlier run'})"
        return None

    def pick(
        self,
        candidates: Iterator[CatalogVideo],
        result: ItemResult,
        exclude_terms: Sequence[str],
    ) -> CatalogVideo:
        """Take the first candidate that is new, recording why the rest were not.

        The iterator is shared across an item's repetitions, so it never offers
        the same video twice and ``count`` naturally yields distinct sources.
        """
        for video in candidates:
            term = excluded_by(video.title, exclude_terms)
            if term is not None:
                self._skip(result, video, f"title matches excluded term {term!r}")
                continue
            if video.video_id in self._claimed:
                self._skip(result, video, "already picked earlier in this run")
                continue
            reason = self.duplicate_reason(video)
            if reason is not None:
                self._skip(result, video, reason)
                continue

            self._claimed.add(video.video_id)
            return video

        raise WorkflowRunError(
            f"No unused source left for {result.label!r}",
            f"{len(result.skipped)} candidate(s) were skipped; widen the search "
            "or clear the state file if this was intentional.",
        )

    def resolve_link(self, item: CartoonItem, result: ItemResult) -> Optional[CatalogVideo]:
        """The exact video a pasted link names, or None if it is already ours.

        No search and no duration preference: the user has already chosen. The
        duplicate check still applies — a link is as easy to paste twice as it
        is to paste once — but a duplicate here is a deliberate skip rather than
        a failure, because not republishing is the correct outcome.
        """
        video = _link_video(item)
        if self.dry_run:
            # A plan that only echoes the URL back is not worth reading, so
            # spend one cheap metadata lookup on the real title and channel.
            peeked = self.peek_link(video.url)
            if peeked is not None:
                video = replace(
                    video,
                    title=peeked.title or video.title,
                    duration=peeked.duration,
                )
                if not result.playlist_name and peeked.channel:
                    result.playlist_name = _title_case(peeked.channel)

        reason = self.duplicate_reason(video)
        if reason is None and video.video_id in self._claimed:
            reason = "already picked earlier in this run"
        if reason is not None:
            result.source_id = video.video_id
            result.source_title = video.title
            result.source_url = video.url
            result.skipped_reason = reason
            self._skip(result, video, reason)
            return None

        self._claimed.add(video.video_id)
        return video

    def peek_link(self, url: str) -> Optional["yt_metadata.YouTubeMetadata"]:
        """Best-effort title/channel for a link, without downloading it."""
        try:
            return yt_metadata.fetch_yt_metadata(url)
        except Exception as exc:  # noqa: BLE001 - a plan must still print
            logger.debug(f"Could not read metadata for {url}: {exc}")
            return None

    def _skip(self, result: ItemResult, video: CatalogVideo, reason: str) -> None:
        result.skipped.append(SkippedSource(video.video_id, video.title, reason))
        self.emit(
            "skip",
            f"Skipping {video.title!r}: {reason}",
            video_id=video.video_id,
            title=video.title,
            reason=reason,
        )

    # -- rendering --------------------------------------------------------

    def render_cartoon(self, video: CatalogVideo, result: ItemResult):
        """Download the episode and strip its background music.

        Returns the rendered path plus the source's ``VideoMetadata``, which the
        upload step needs to build its SEO title and description.
        """
        # Imported here rather than at module scope: ``pipeline`` pulls in the
        # Demucs/torch stack, which a --dry-run has no use for.
        from . import pipeline

        meta = youtube.download_video_with_metadata(video.url, self.cache_dir, use_cache=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"{Path(meta.file_path).stem}_filtered.mp4"

        def progress(stage: str, percent: int) -> None:
            self.emit("progress", f"{stage} ({percent}%)", stage=stage, percent=percent)

        source_height = _probe_height(Path(meta.file_path))
        scale_height = scale_height_for(source_height, self.target_height)
        if scale_height:
            self.emit(
                "render",
                f"Removing background music from {video.title!r} "
                f"(enlarging {source_height}p to {scale_height}p)",
            )
        else:
            self.emit("render", f"Removing background music from {video.title!r}")
        rendered = pipeline.process_video(
            input_path=Path(meta.file_path),
            output_path=output_path,
            progress_callback=progress,
            scale_height=scale_height,
        )
        return Path(rendered), meta

    def render_quran(self, item: QuranItem, video: CatalogVideo, result: ItemResult) -> Path:
        """Lay the recitation over the chosen visual.

        ``add_pick_to_catalog`` first, always: ``_resolve_visual_video`` only
        sees ids that ``cartoon_catalog.list_videos`` returns, so a search hit
        has to be persisted before the render can resolve it. It is idempotent,
        so re-adding a curated video costs nothing.
        """
        cartoon_search.add_pick_to_catalog(video, cache_dir=self.cache_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / _quran_output_name(item, video)

        self.emit("render", f"Rendering {result.label} over {video.title!r}")
        overlay_result = overlay_pipeline.run_overlay_from_surah_numbers(
            surah_numbers=item.surah_numbers,
            reciter_slug=item.reciter_slug,
            visual_video_id=video.video_id,
            metadata=self.metadata(),
            output_path=output_path,
            cache_dir=self.cache_dir,
            resolution=resolution_for(self.target_height),
            # Never here. Publishing is a separate, explicit step below so a
            # render that succeeds but is wrong can still be caught.
            upload=False,
        )
        return Path(overlay_result.output_path)

    # -- publishing -------------------------------------------------------

    def upload(self, item: WorkItem, video: CatalogVideo, rendered: Path, source_meta) -> str:
        self.emit("upload", f"Uploading {rendered.name} ({self.privacy})")
        if isinstance(item, CartoonItem):
            return uploader.upload_to_youtube(
                video_path=rendered,
                original_metadata=source_meta,
                privacy=self.privacy,
            )
        return overlay_pipeline.upload_rendered(
            rendered,
            self.metadata(),
            surah_numbers=item.surah_numbers,
            reciter_slug=item.reciter_slug,
            visual_title=video.title,
        )

    def attach_to_playlist(self, name: Optional[str], video_id: str, result: ItemResult) -> None:
        """File the published video under its playlist.

        Deliberately swallows everything: the video is already public by this
        point, so a playlist problem is a note in the summary, never a failed
        item.
        """
        if not name:
            result.playlist_error = "no playlist name could be derived from the video"
            self.emit("playlist-failed", f"Published, but {result.playlist_error}")
            return
        try:
            playlist_id, created = self.resolve_playlist(name)
            service = uploader.authenticate_youtube()
            uploader.add_to_playlist(service, video_id, playlist_id)
        except Exception as exc:  # noqa: BLE001 - see docstring
            result.playlist_error = str(exc)
            self.emit(
                "playlist-failed",
                f"Published, but could not add to playlist {name!r}: {exc}",
                playlist=name,
                error=str(exc),
            )
            return
        result.playlist_id = playlist_id
        result.playlist_created = created
        verb = "Created playlist and added to" if created else "Added to playlist"
        self.emit("playlist", f"{verb} {name!r}", playlist=name, playlist_id=playlist_id)

    def resolve_playlist(self, name: str) -> Tuple[str, bool]:
        """Playlist id for ``name``, creating it if the channel has none."""
        index = self.playlist_index()
        key = name.strip().lower()
        existing = index.get(key)
        if existing:
            return existing, False
        playlist_id = uploader.create_playlist(
            title=name,
            description=f"Auto-created by yt-studio for {name}.",
            privacy=self.privacy,
        )
        if not playlist_id:
            raise WorkflowRunError(f"Could not create playlist {name!r}")
        index[key] = playlist_id
        return playlist_id, True

    # -- item loop --------------------------------------------------------

    def run_item(self, item: WorkItem) -> List[ItemResult]:
        label = describe_item(item)
        kind = _item_kind(item)
        results: List[ItemResult] = []

        def blank(index: int) -> ItemResult:
            return ItemResult(
                kind=kind,
                label=label,
                playlist_name=item.playlist_name,
                index=index,
                total=item.count,
                dry_run=self.dry_run,
            )

        # Announced before the search, so the transcript reads as "this item,
        # then what it took to produce it" rather than the other way round.
        self.emit("item", f"{label} — {item.count} video(s)", label=label, count=item.count)

        if is_link_item(item):
            # A link names its own video, so there is nothing to search for.
            pool: Iterator[CatalogVideo] = iter(())
        else:
            try:
                pool = iter(self.candidates(item))
            except Exception as exc:  # noqa: BLE001 - a dead search fails only this item
                message = str(exc)
                self.emit("item-failed", f"{label}: {message}", label=label, error=message)
                return [replace(blank(i), error=message) for i in range(1, item.count + 1)]

        exclude_terms = item.exclude_terms if isinstance(item, QuranItem) else []
        for index in range(1, item.count + 1):
            result = blank(index)
            results.append(result)
            if item.count > 1:
                self.emit("item-step", f"{label} {index}/{item.count}", label=label, index=index)
            try:
                self.run_once(item, pool, exclude_terms, result)
            except Exception as exc:  # noqa: BLE001 - the whole point: keep going
                result.error = str(exc)
                self.emit("item-failed", f"{label}: {exc}", label=label, error=str(exc))
        return results

    def run_once(
        self,
        item: WorkItem,
        pool: Iterator[CatalogVideo],
        exclude_terms: Sequence[str],
        result: ItemResult,
    ) -> None:
        if is_link_item(item):
            video = self.resolve_link(item, result)
            if video is None:  # already on the channel; deliberately not redone
                return
        else:
            video = self.pick(pool, result, exclude_terms)
        result.source_id = video.video_id
        result.source_title = video.title
        result.source_url = video.url
        result.source_duration = video.duration
        self.emit(
            "pick",
            f"Picked {video.title!r} ({video.duration // 60} min)",
            video_id=video.video_id,
            title=video.title,
            url=video.url,
        )

        if self.dry_run:
            self.plan_only(item, video, result)
            return

        if isinstance(item, CartoonItem):
            rendered, source_meta = self.render_cartoon(video, result)
            # An unlabelled link has no name of its own, so the channel it came
            # from names the playlist. The metadata is already in hand from the
            # download, so this costs nothing.
            if not result.playlist_name and getattr(source_meta, "channel", ""):
                result.playlist_name = _title_case(source_meta.channel)
            if item.url and getattr(source_meta, "title", ""):
                result.source_title = source_meta.title
        else:
            rendered, source_meta = self.render_quran(item, video, result), None
        result.rendered_path = rendered
        self.emit("rendered", f"Rendered {rendered.name}", path=str(rendered))

        # Recorded before the upload, not after: a render that fails to publish
        # is still work done, and re-picking the same source tomorrow would
        # waste the same GPU hours again.
        entry = self.state.add(
            source_id=video.video_id,
            kind=result.kind,
            request=result.label,
            output_path=str(rendered),
        )
        save_state(self.state, self.state_path)

        video_id = self.upload(item, video, rendered, source_meta)
        result.uploaded_video_id = video_id
        entry.uploaded_video_id = video_id
        save_state(self.state, self.state_path)
        self.emit(
            "uploaded",
            f"Published https://youtube.com/watch?v={video_id}",
            video_id=video_id,
        )

        self.attach_to_playlist(result.playlist_name, video_id, result)

    def plan_only(self, item: WorkItem, video: CatalogVideo, result: ItemResult) -> None:
        """Report what a real run would do, touching nothing.

        Everything cheap and read-only is still done for real — the title is
        rendered through the same template the upload would use, so a broken
        placeholder surfaces here instead of at publish time.
        """
        if isinstance(item, CartoonItem):
            result.rendered_path = self.output_dir / f"{video.video_id}_filtered.mp4"
            try:
                # Must match what upload_to_youtube actually publishes, or the dry run
                # previews a title the real upload no longer produces.
                result.planned_title = uploader.resolve_upload_title(video.title)
            except Exception as exc:  # noqa: BLE001 - a preview must not fail the plan
                logger.debug(f"Could not preview the title: {exc}")
        else:
            result.rendered_path = self.output_dir / _quran_output_name(item, video)
            metadata = self.metadata()  # raises if the template file is unusable
            auto_vars = overlay_pipeline._build_surah_numbers_auto_vars(
                surah_numbers=item.surah_numbers,
                reciter_display_name=item.reciter_name,
                visual_title=video.title,
            )
            result.planned_title = overlay_pipeline.fit_title(
                metadata.render_title(extra_vars=auto_vars)
            )

        if result.playlist_name:
            key = result.playlist_name.strip().lower()
            index = self.playlist_index()
            result.playlist_id = index.get(key)
            result.playlist_created = key not in index  # i.e. *would* be created
            playlist = repr(result.playlist_name)
        else:
            # Only reachable for an unlabelled link whose metadata lookup
            # failed; the real run still names it after the channel.
            playlist = "the source video's channel"
        self.emit(
            "dry-run",
            f"Would publish {result.planned_title or result.label!r} to playlist {playlist}",
            title=result.planned_title,
            playlist=result.playlist_name,
        )


def run_workflow(
    items: Sequence[WorkItem],
    *,
    dry_run: bool = False,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    metadata_path: Path = DEFAULT_METADATA_PATH,
    privacy: str = "public",
    on_event: Optional[EventCallback] = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    state_path: Path = DEFAULT_STATE_PATH,
    target_height: int = DEFAULT_HEIGHT,
) -> WorkflowSummary:
    """Produce every item, and report what happened.

    Args:
        items: Work items from ``workflow.parse_request``.
        dry_run: Resolve sources and check for duplicates, then stop before
            rendering, uploading, or writing anything.
        cache_dir: Download / search cache.
        metadata_path: Overlay metadata template (Quran items only).
        privacy: Privacy for uploads and any playlist created.
        on_event: ``(kind, message, data)`` progress callback.
        output_dir: Where rendered MP4s land.
        state_path: JSON record of sources already rendered from.
        target_height: Output height. Overlay renders target it directly; a
            music-removal source smaller than it is enlarged to match.

    Returns:
        A :class:`WorkflowSummary`; ``exit_code`` is non-zero if any item
        failed. Individual failures never abort the run.
    """
    run = _Run(
        dry_run=dry_run,
        cache_dir=Path(cache_dir),
        output_dir=Path(output_dir),
        state_path=Path(state_path),
        metadata_path=Path(metadata_path),
        privacy=privacy,
        on_event=on_event,
        target_height=target_height,
    )
    summary = WorkflowSummary(dry_run=dry_run)
    for item in items:
        summary.results.extend(run.run_item(item))
    return summary
