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
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

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

#: Where a resolved-but-unapproved plan waits between the two halves of a
#: run. Kept beside the state file because it is the same kind of thing: a
#: small record of intent that outlives one invocation.
DEFAULT_PLAN_PATH = Path("state/last_plan.json")

#: Bumped whenever the on-disk plan shape changes. An older file is refused
#: outright rather than half-read: a misread plan publishes the wrong video.
PLAN_VERSION = 1

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


class PlanError(YTAudioFilterError):
    """A saved plan could not be read, or does not apply to this request."""


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
#: 1080p, and it is worth being precise about what that does and does not buy.
#: Protected cartoons come down as format 18 — 360p combined — because of the
#: SABR wall (see CLAUDE.md), so rendering at 1080p invents no detail: those are
#: interpolated pixels, not recovered ones.
#:
#: The gain is real but sits elsewhere. YouTube assigns its encoding ladder by
#: uploaded resolution, so a 1080p upload is given a markedly higher bitrate
#: than the same picture at 720p, and survives YouTube's own re-encode with
#: visibly fewer artefacts. It is also what the platform's own recommendation
#: assumes. Uploading small and letting YouTube upscale on playback gives the
#: worst of both.
DEFAULT_HEIGHT = 1080

#: The floor no render may go under, whatever was asked for.
#:
#: YouTube chooses its encoding ladder from the uploaded resolution, so a 360p
#: upload is handed a bitrate that makes an already-soft cartoon look worse
#: again on playback. 720p is the point where that stops happening, and it is
#: also what a 2x Real-ESRGAN pass over a 360p source produces — so it is
#: reachable with reconstructed detail rather than only by stretching.
MIN_HEIGHT = 720


def clamp_height(height: Optional[int]) -> int:
    """The height to render at: what was asked for, but never below the floor.

    ``None`` means nothing was asked for, so the default applies.
    """
    if height is None:
        return DEFAULT_HEIGHT
    if height <= 0:
        raise ValueError(f"Height must be positive, got {height}")
    return max(int(height), MIN_HEIGHT)


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


#: Turkish letters that ASCII typing flattens. NFKD handles the ones built from
#: a base letter plus a mark, but the dotless i and the soft g have no such
#: decomposition, so they are mapped by hand.
_TURKISH_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
})


def playlist_key(name: str) -> str:
    """Fold a playlist name so spelling differences stop splitting a series.

    Exact matching meant ``hophop baykus`` never found the existing
    ``hop hop baykus``, so a second playlist appeared for the same show. The
    channel writes its titles in Turkish (``Hop Hop Baykuş``) while requests get
    typed in ASCII, so case, spacing, punctuation and Turkish letters all fold
    away.

    Deliberately *not* the vowel-collapsing ``workflow.normalise_name``: that is
    right for surah names, where ``AnNaas`` and ``An-Nas`` are one word, but
    here it would merge ``Quran`` with ``Quraan`` — two playlists someone may
    well have meant to keep apart.
    """
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKD", name.translate(_TURKISH_FOLD))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def pick_playlist(existing: Sequence[dict], name: str) -> Optional[dict]:
    """The playlist to use for ``name``, or None to create one.

    An exact title wins; otherwise the fullest of the folded matches, because
    duplicates already exist on the channel and adding to the emptier one would
    only deepen the split.
    """
    wanted = playlist_key(name)
    if not wanted:
        return None
    for playlist in existing:
        if str(playlist.get("title", "")).strip() == name.strip():
            return playlist
    matches = [p for p in existing if playlist_key(str(p.get("title", ""))) == wanted]
    if not matches:
        return None
    return max(matches, key=lambda p: int(p.get("itemCount") or 0))


def _quran_output_name(item: QuranItem, video: CatalogVideo) -> str:
    numbers = item.surah_numbers
    if len(numbers) > 3:
        tag = f"{numbers[0]:03d}-{numbers[-1]:03d}"
    else:
        tag = "_".join(f"{n:03d}" for n in numbers)
    return f"quran_{item.reciter_slug}_{tag}_{video.video_id}.mp4"


# ---------------------------------------------------------------------------
# The plan: what a run intends to make, before it makes it
# ---------------------------------------------------------------------------


@dataclass
class PlannedPick:
    """One video the run intends to produce, chosen but not yet produced.

    Approval is given against this, so it has to survive a round trip through
    JSON. That is why the whole ``CatalogVideo`` is carried rather than just an
    id: a second search can legitimately rank different videos, and approving
    pick #2 must produce *the video that was shown*, not whatever comes second
    tomorrow.
    """

    #: Position of the originating item in the request, so a rejected pick can
    #: be re-resolved from the same candidate pool.
    item_index: int
    item: WorkItem
    kind: str  # "cartoon" | "quran"
    label: str
    playlist_name: Optional[str]
    index: int  # 1-based repetition within the item
    total: int  # the item's count
    video: Optional[CatalogVideo] = None
    skipped: List[SkippedSource] = field(default_factory=list)
    #: Set when the pick was deliberately dropped (a pasted link already on the
    #: channel). Not a failure, and nothing to approve.
    skipped_reason: Optional[str] = None
    error: Optional[str] = None

    @property
    def producible(self) -> bool:
        """True when approving this pick would actually make something."""
        return self.video is not None and self.error is None and self.skipped_reason is None

    @property
    def url(self) -> str:
        """The canonical watch URL — what a human opens to check the pick.

        Rebuilt from the id rather than reusing ``video.url``, because a pasted
        link may be a ``youtu.be`` short form or drag a playlist query along,
        and the point of printing it is that it is unambiguous and clickable.
        """
        if self.video is None:
            return ""
        return f"https://www.youtube.com/watch?v={self.video.video_id}"

    def to_result(self) -> ItemResult:
        """The summary row this pick starts from."""
        result = ItemResult(
            kind=self.kind,
            label=self.label,
            playlist_name=self.playlist_name,
            index=self.index,
            total=self.total,
            skipped=list(self.skipped),
            skipped_reason=self.skipped_reason,
            error=self.error,
        )
        if self.video is not None:
            result.source_id = self.video.video_id
            result.source_title = self.video.title
            result.source_url = self.video.url
            result.source_duration = self.video.duration
        return result


@dataclass
class WorkflowPlan:
    """A resolved set of picks, waiting to be approved.

    The request line and the timestamp are part of the record on purpose: an
    approval that could be applied to a *different* request would publish
    videos nobody looked at, which is exactly what this mechanism exists to
    prevent.
    """

    request: str
    picks: List[PlannedPick] = field(default_factory=list)
    created_at: str = ""  # ISO-8601 UTC; filled in below when left blank
    privacy: str = "public"
    target_height: int = DEFAULT_HEIGHT
    #: Whether the approved picks were resolved for a Real-ESRGAN render. Part
    #: of the plan because approval is given against what was shown, and a
    #: sharpened render is a materially different piece of work.
    upscale: bool = False
    version: int = PLAN_VERSION

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @property
    def producible(self) -> List[PlannedPick]:
        return [pick for pick in self.picks if pick.producible]

    def matches(self, request: Optional[str]) -> bool:
        """Whether ``request`` is the one this plan was resolved for.

        ``None`` means the caller named no request at all (a bare ``yt-studio
        --approve``), which cannot contradict the saved one.
        """
        if request is None:
            return True
        return request.strip() == self.request.strip()


def _item_to_dict(item: WorkItem) -> Dict[str, Any]:
    data: Dict[str, Any] = asdict(item)
    data["type"] = _item_kind(item)
    return data


def _item_from_dict(data: Dict[str, Any]) -> WorkItem:
    cls = QuranItem if data.get("type") == "quran" else CartoonItem
    fields = set(cls.__dataclass_fields__)
    try:
        return cls(**{k: v for k, v in data.items() if k in fields})  # type: ignore[arg-type]
    except TypeError as exc:
        raise PlanError(f"Unreadable work item in the plan: {exc}") from exc


def _pick_to_dict(pick: PlannedPick) -> Dict[str, Any]:
    return {
        "item_index": pick.item_index,
        "item": _item_to_dict(pick.item),
        "kind": pick.kind,
        "label": pick.label,
        "playlist_name": pick.playlist_name,
        "index": pick.index,
        "total": pick.total,
        "video": asdict(pick.video) if pick.video is not None else None,
        "skipped": [asdict(s) for s in pick.skipped],
        "skipped_reason": pick.skipped_reason,
        "error": pick.error,
    }


def _pick_from_dict(data: Dict[str, Any]) -> PlannedPick:
    video = data.get("video")
    try:
        return PlannedPick(
            item_index=int(data.get("item_index") or 0),
            item=_item_from_dict(dict(data.get("item") or {})),
            kind=str(data.get("kind") or ""),
            label=str(data.get("label") or ""),
            playlist_name=data.get("playlist_name") or None,
            index=int(data.get("index") or 1),
            total=int(data.get("total") or 1),
            video=CatalogVideo(**video) if isinstance(video, dict) else None,
            skipped=[
                SkippedSource(**s) for s in (data.get("skipped") or []) if isinstance(s, dict)
            ],
            skipped_reason=data.get("skipped_reason") or None,
            error=data.get("error") or None,
        )
    except TypeError as exc:
        raise PlanError(f"Unreadable pick in the plan: {exc}") from exc


def save_plan(plan: WorkflowPlan, path: Path = DEFAULT_PLAN_PATH) -> Path:
    """Write the plan out so a later ``--approve`` can produce it verbatim."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": plan.version,
                "request": plan.request,
                "created_at": plan.created_at,
                "privacy": plan.privacy,
                "target_height": plan.target_height,
                "upscale": plan.upscale,
                "picks": [_pick_to_dict(pick) for pick in plan.picks],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def load_plan(path: Path = DEFAULT_PLAN_PATH) -> WorkflowPlan:
    """Read a saved plan back.

    Damage is fatal here, unlike in the state file. The state file is an
    optimisation and ignoring it only costs a re-render; a plan file *is* the
    approval, so anything short of reading it exactly has to stop the run.
    """
    path = Path(path)
    if not path.exists():
        raise PlanError(
            f"No plan is waiting at {path}",
            "Run yt-studio with a request first: it resolves the picks, prints "
            "their URLs, and saves the plan for --approve.",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise PlanError(
            f"Could not read the plan at {path}: {exc}",
            "Re-run the request to resolve a fresh plan.",
        ) from exc
    if not isinstance(raw, dict) or raw.get("version") != PLAN_VERSION:
        raise PlanError(
            f"The plan at {path} was written by a different version of yt-studio",
            "Re-run the request to resolve a fresh plan.",
        )
    return WorkflowPlan(
        request=str(raw.get("request") or ""),
        picks=[_pick_from_dict(p) for p in raw.get("picks") or [] if isinstance(p, dict)],
        created_at=str(raw.get("created_at") or ""),
        privacy=str(raw.get("privacy") or "public"),
        target_height=int(raw.get("target_height") or DEFAULT_HEIGHT),
        upscale=bool(raw.get("upscale")),
    )


def discard_plan(path: Path = DEFAULT_PLAN_PATH) -> None:
    """Delete a plan that has been acted on.

    An approval is good exactly once. Producing the same plan a second time
    would render and publish the same sources again, because the duplicate
    check ran when the plan was resolved, not now.
    """
    try:
        Path(path).unlink()
    except OSError:  # pragma: no cover - a plan we cannot delete is not fatal
        logger.debug("Could not remove the plan file at %s", path)


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
        peek_links: Optional[bool] = None,
        upscale: bool = False,
    ) -> None:
        self.dry_run = dry_run
        self.upscale = upscale
        self.cache_dir = Path(cache_dir)
        self.output_dir = Path(output_dir)
        self.state_path = Path(state_path)
        self.metadata_path = Path(metadata_path)
        self.privacy = privacy
        self.target_height = target_height
        self.on_event = on_event
        # A pasted link has no title until something fetches it, which is fine
        # for a render (the download brings one) but useless in a list someone
        # is being asked to approve. Default to the old behaviour and let the
        # approval path turn it on.
        self.peek_links = dry_run if peek_links is None else peek_links
        self.state = load_state(self.state_path)
        self._uploaded: Optional[Dict[str, dict]] = None
        self._playlists: Optional[List[dict]] = None
        self._metadata: Optional[OverlayMetadata] = None
        # Sources handed out during this run. Two items with overlapping
        # searches must not both render the same video, and a repetition must
        # not re-pick a source whose render just failed.
        self._claimed: set = set()
        # Videos the user looked at and turned down. Held in memory for this
        # run only: the state file records what has been *produced*, and
        # writing a rejection there would hide the video from every future run
        # as though it had already been published.
        self._rejected: set = set()
        # One candidate iterator per item, kept alive across repetitions and
        # across re-resolves. That shared position is what makes "reject this
        # one, give me the next" work without restarting the search.
        self._pools: Dict[int, Iterator[CatalogVideo]] = {}
        self._pool_errors: Dict[int, str] = {}

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

    def playlist_index(self) -> List[dict]:
        """The channel's playlists, kept whole.

        The record rather than a title→id map, because choosing between two
        playlists that fold to the same name needs their item counts.
        """
        if self._playlists is None:
            found: List[dict] = []
            try:
                found = [dict(p) for p in uploader.list_playlists()]
            except Exception as exc:  # noqa: BLE001 - playlists never fail an item
                logger.warning(f"Could not list playlists: {exc}")
            self._playlists = found
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
            if video.video_id in self._rejected:
                self._skip(result, video, "rejected during approval")
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

    def resolve_link(
        self, item: CartoonItem, result: ItemResult
    ) -> Tuple[CatalogVideo, Optional[str]]:
        """The exact video a pasted link names, and why it must not be redone.

        No search and no duration preference: the user has already chosen. The
        duplicate check still applies — a link is as easy to paste twice as it
        is to paste once — but a duplicate here is a deliberate skip rather than
        a failure, because not republishing is the correct outcome. The video is
        returned either way so the summary can name what was skipped.
        """
        video = _link_video(item)
        if self.peek_links:
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
        if reason is None and video.video_id in self._rejected:
            reason = "rejected during approval"
        if reason is None and video.video_id in self._claimed:
            reason = "already picked earlier in this run"
        if reason is not None:
            self._skip(result, video, reason)
            return video, reason

        self._claimed.add(video.video_id)
        return video, None

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

    def sharpen(self, source: Path, video_id: str) -> Optional[Path]:
        """A Real-ESRGAN upscale of the downloaded source, or None.

        Best-effort by design. Sharpening needs a Vulkan GPU and refuses videos
        over ``upscale.MAX_UPSCALE_FRAMES`` — which a full 25-minute episode
        comfortably exceeds — so failing the item here would mean the long
        cartoons could never be produced at all. Falling back to a plain scale
        still clears the 720p floor; it just interpolates instead of
        reconstructing, and says so rather than letting the difference pass
        unnoticed.
        """
        try:
            from . import upscale as upscale_module

            return Path(
                upscale_module.get_or_create_sharpened(
                    source, video_id=video_id, cache_dir=self.cache_dir
                )
            )
        except Exception as exc:  # noqa: BLE001 - see docstring
            self.emit(
                "upscale-skipped",
                f"Real-ESRGAN sharpening skipped ({exc}); scaling the source instead",
                video_id=video_id,
                error=str(exc),
            )
            return None

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

        # Sharpening comes first: it rebuilds the picture from PNG frames, so
        # it has to happen while the file still carries the original audio that
        # Demucs is about to separate.
        source_path = Path(meta.file_path)
        if self.upscale:
            self.emit("upscale", f"Sharpening {video.title!r} with Real-ESRGAN")
            source_path = self.sharpen(source_path, video.video_id) or source_path

        source_height = _probe_height(source_path)
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
            input_path=source_path,
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
        match = pick_playlist(index, name)
        if match is not None:
            return str(match["id"]), False
        playlist_id = uploader.create_playlist(
            title=name,
            description=f"Auto-created by yt-studio for {name}.",
            privacy=self.privacy,
        )
        if not playlist_id:
            raise WorkflowRunError(f"Could not create playlist {name!r}")
        # Remember it so a second item in the same run reuses it rather than
        # creating a third.
        index.append({"id": playlist_id, "title": name, "itemCount": 0})
        return playlist_id, True

    # -- resolving --------------------------------------------------------

    def reject_video(self, video_id: str) -> None:
        """Take a video out of the running for the rest of this run."""
        self._rejected.add(video_id)

    def pool_for(self, item: WorkItem, item_index: int) -> Tuple[Iterator[CatalogVideo], str]:
        """The item's candidate iterator, created once and shared.

        Returns the iterator and an error string (empty when fine). A search
        that dies fails only its own item, and is remembered so a later
        re-resolve does not retry a dead search per repetition.
        """
        if item_index in self._pool_errors:
            return iter(()), self._pool_errors[item_index]
        if item_index not in self._pools:
            if is_link_item(item):
                # A link names its own video, so there is nothing to search for.
                self._pools[item_index] = iter(())
            else:
                try:
                    self._pools[item_index] = iter(self.candidates(item))
                except Exception as exc:  # noqa: BLE001 - a dead search fails one item
                    label, message = describe_item(item), str(exc)
                    self._pool_errors[item_index] = message
                    self.emit("item-failed", f"{label}: {message}", label=label, error=message)
                    return iter(()), message
        return self._pools[item_index], ""

    def blank_pick(self, item: WorkItem, item_index: int, index: int) -> PlannedPick:
        return PlannedPick(
            item_index=item_index,
            item=item,
            kind=_item_kind(item),
            label=describe_item(item),
            playlist_name=item.playlist_name,
            index=index,
            total=item.count,
        )

    def resolve_item(self, item: WorkItem, item_index: int) -> List[PlannedPick]:
        """Choose a source for every repetition of one item, rendering nothing.

        Everything read-only still happens for real — the search, the exclusion
        terms, and the duplicate check — so the picks that come out are picks
        against the channel as it actually is, not a guess.
        """
        label = describe_item(item)
        # Announced before the search, so the transcript reads as "this item,
        # then what it took to source it" rather than the other way round.
        self.emit("item", f"{label} — {item.count} video(s)", label=label, count=item.count)

        pool, pool_error = self.pool_for(item, item_index)
        picks: List[PlannedPick] = []
        for index in range(1, item.count + 1):
            pick = self.blank_pick(item, item_index, index)
            picks.append(pick)
            if pool_error:
                pick.error = pool_error
                continue
            if item.count > 1:
                self.emit("item-step", f"{label} {index}/{item.count}", label=label, index=index)
            self.resolve_pick(pick, pool)
        return picks

    def resolve_pick(self, pick: PlannedPick, pool: Iterator[CatalogVideo]) -> None:
        """Fill in ``pick.video``, or record why no source could be chosen."""
        item = pick.item
        # ``pick`` and ``resolve_link`` both report through an ``ItemResult``;
        # this scratch one carries their findings back onto the plan entry.
        scratch = pick.to_result()
        exclude_terms = item.exclude_terms if isinstance(item, QuranItem) else []
        try:
            if isinstance(item, CartoonItem) and is_link_item(item):
                video, reason = self.resolve_link(item, scratch)
            else:
                video, reason = self.pick(pool, scratch, exclude_terms), None
        except Exception as exc:  # noqa: BLE001 - the whole point: keep going
            pick.skipped = list(scratch.skipped)
            pick.error = str(exc)
            self.emit("item-failed", f"{pick.label}: {exc}", label=pick.label, error=str(exc))
            return

        pick.skipped = list(scratch.skipped)
        pick.playlist_name = scratch.playlist_name
        pick.video = video
        pick.skipped_reason = reason
        if reason is not None:  # already ours; deliberately not redone
            return
        self.emit(
            "pick",
            f"Picked {video.title!r} ({video.duration // 60} min)",
            video_id=video.video_id,
            title=video.title,
            url=pick.url,
        )

    # -- producing --------------------------------------------------------

    def produce(self, pick: PlannedPick) -> ItemResult:
        """Render, publish and file one approved pick.

        Never raises: a failure is recorded against its own row so the next
        approved pick still gets its turn.
        """
        result = pick.to_result()
        if not pick.producible or pick.video is None:
            return result
        self.emit(
            "produce",
            f"{pick.label} ({pick.index}/{pick.total}) — {pick.video.title!r}",
            label=pick.label,
            video_id=pick.video.video_id,
            url=pick.url,
        )
        try:
            self.produce_once(pick.item, pick.video, result)
        except Exception as exc:  # noqa: BLE001 - one bad item cannot end the run
            result.error = str(exc)
            self.emit("item-failed", f"{pick.label}: {exc}", label=pick.label, error=str(exc))
        return result

    def produce_once(self, item: WorkItem, video: CatalogVideo, result: ItemResult) -> None:
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
            # Same folded lookup the real run uses, so the dry run predicts
            # "would create" correctly instead of promising a new playlist that
            # a spelling variant would actually have matched.
            match = pick_playlist(self.playlist_index(), result.playlist_name)
            result.playlist_id = str(match["id"]) if match else None
            result.playlist_created = match is None  # i.e. *would* be created
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


# ---------------------------------------------------------------------------
# Planning, approving, producing
# ---------------------------------------------------------------------------


class WorkflowPlanner:
    """Resolve the picks, let them be approved, then produce them.

    Approval sits between two halves that used to be one function: the sources
    have to be chosen (and shown as URLs) before anything is downloaded, and
    the *same* sources have to be produced afterwards. Keeping both halves on
    one object keeps the candidate iterators alive between them, which is what
    lets a rejected pick fall through to the next candidate rather than
    restarting the search.
    """

    def __init__(self, items: Sequence[WorkItem], run: "_Run") -> None:
        self.items = list(items)
        self.run = run

    def resolve(self) -> List[PlannedPick]:
        """Every repetition of every item, sourced but untouched."""
        picks: List[PlannedPick] = []
        for item_index, item in enumerate(self.items):
            picks.extend(self.run.resolve_item(item, item_index))
        return picks

    def reject(self, picks: Sequence[PlannedPick], positions: Sequence[int]) -> List[PlannedPick]:
        """Swap the picks at ``positions`` (1-based) for the next candidates.

        The rejection lasts for this run only, on purpose. It is deliberately
        *not* written to the state file: that file records what has been
        produced, and marking a merely-unwanted video as produced would hide it
        from every future run too.
        """
        updated = list(picks)
        for position in positions:
            if not 1 <= position <= len(updated):
                raise PlanError(
                    f"There is no pick {position} to reject",
                    f"The plan has {len(updated)} pick(s).",
                )
            old = updated[position - 1]
            if old.video is not None:
                self.run.reject_video(old.video.video_id)
            fresh = self.run.blank_pick(old.item, old.item_index, old.index)
            pool, pool_error = self.run.pool_for(old.item, old.item_index)
            if pool_error:
                fresh.error = pool_error
            else:
                self.run.resolve_pick(fresh, pool)
            updated[position - 1] = fresh
        return updated

    def plan(self, picks: Sequence[PlannedPick], request: str) -> WorkflowPlan:
        """Package the picks for the approval gate."""
        return WorkflowPlan(
            request=request,
            picks=list(picks),
            privacy=self.run.privacy,
            target_height=self.run.target_height,
            upscale=self.run.upscale,
        )

    def report(self, picks: Sequence[PlannedPick]) -> WorkflowSummary:
        """A dry run's summary: what each pick *would* produce, touching nothing."""
        summary = WorkflowSummary(dry_run=True)
        for pick in picks:
            result = pick.to_result()
            result.dry_run = True
            if pick.producible and pick.video is not None:
                try:
                    self.run.plan_only(pick.item, pick.video, result)
                except Exception as exc:  # noqa: BLE001 - a preview cannot fail a run
                    result.error = str(exc)
            summary.results.append(result)
        return summary

    def produce(self, picks: Sequence[PlannedPick]) -> WorkflowSummary:
        """Make every producible pick, in order."""
        summary = WorkflowSummary()
        for pick in picks:
            summary.results.append(self.run.produce(pick))
        return summary


def create_planner(
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
    peek_links: Optional[bool] = None,
    upscale: bool = False,
) -> WorkflowPlanner:
    """A planner bound to one run's directories and settings.

    ``peek_links`` defaults to ``dry_run``; pass ``True`` when the picks are
    going to be shown to someone, so a pasted link is presented with its real
    title instead of its own URL.
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
        peek_links=peek_links,
        upscale=upscale,
    )
    return WorkflowPlanner(items, run)


def run_plan(
    plan: WorkflowPlan,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    metadata_path: Path = DEFAULT_METADATA_PATH,
    privacy: Optional[str] = None,
    on_event: Optional[EventCallback] = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    state_path: Path = DEFAULT_STATE_PATH,
    target_height: Optional[int] = None,
    upscale: Optional[bool] = None,
) -> WorkflowSummary:
    """Produce exactly the picks in ``plan``, resolving nothing again.

    This is the far side of the approval gate. Nothing here searches: the
    videos were chosen, shown as URLs and signed off, and re-resolving now
    could quietly produce something the approver never saw. ``privacy`` and
    ``target_height`` default to what the plan was resolved under, for the same
    reason.
    """
    run = _Run(
        dry_run=False,
        cache_dir=Path(cache_dir),
        output_dir=Path(output_dir),
        state_path=Path(state_path),
        metadata_path=Path(metadata_path),
        privacy=privacy or plan.privacy,
        on_event=on_event,
        target_height=target_height or plan.target_height,
        upscale=plan.upscale if upscale is None else upscale,
    )
    summary = WorkflowSummary()
    for pick in plan.picks:
        summary.results.append(run.produce(pick))
    return summary


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
    upscale: bool = False,
) -> WorkflowSummary:
    """Produce every item in one go, and report what happened.

    Resolve and produce back to back, with no approval gate — the CLI uses this
    for ``--yes`` and for ``--dry-run``. When someone is going to look at the
    picks first, use :func:`create_planner` instead.

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
        upscale: Run Real-ESRGAN over a music-removal source before separating
            it, so the extra pixels are reconstructed rather than stretched.
            Best-effort: a source too long to sharpen is scaled instead.

    Returns:
        A :class:`WorkflowSummary`; ``exit_code`` is non-zero if any item
        failed. Individual failures never abort the run.
    """
    planner = create_planner(
        items,
        dry_run=dry_run,
        cache_dir=cache_dir,
        metadata_path=metadata_path,
        privacy=privacy,
        on_event=on_event,
        output_dir=output_dir,
        state_path=state_path,
        target_height=target_height,
        upscale=upscale,
    )
    picks = planner.resolve()
    return planner.report(picks) if dry_run else planner.produce(picks)
