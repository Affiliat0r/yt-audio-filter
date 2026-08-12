"""Python mirror of the TypeScript job contract in ``web/lib/types.ts``.

The Vercel frontend and this worker exchange nothing but JSON matching those
types. The wire format is camelCase (``surahNumbers``, ``gapSeconds``,
``metadataPath``); Python is snake_case. **Every** conversion happens in this
module and nowhere else, so a contract change on the TypeScript side is a
one-file change on this side.

Each job-input type exposes ``from_json`` / ``to_json`` so the round trip is
testable offline, and ``AyahRangeSpec.to_ayah_range()`` bridges straight into
``ayah_repeater.AyahRange``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Literal, Optional, Union

from yt_audio_filter.cartoon_catalog import CatalogVideo

if TYPE_CHECKING:  # pragma: no cover - typing only
    from yt_audio_filter.ayah_repeater import AyahRange


JobKind = Literal["surah", "ayah", "music_removal", "search"]
JobStatus = Literal["queued", "claimed", "running", "done", "error", "cancelled"]
CacheState = Literal["new", "downloaded", "upscaled"]
Privacy = Literal["private", "unlisted", "public"]

#: Fallback used when a job's ``settings.metadataPath`` is blank.
DEFAULT_METADATA_PATH = "examples/metadata-surah-arrahman.json"

#: Synthetic channel slug used by ``cartoon_search`` for keyword-search picks.
SEARCH_CHANNEL_SLUG = "__search__"

TERMINAL_STATUSES = ("done", "error", "cancelled")


class ContractError(ValueError):
    """A payload from the frontend does not match the documented contract."""


def _require(raw: Dict[str, Any], key: str, *, kind: str) -> Any:
    """Fetch a required key, raising a contract error naming the shape."""
    value = raw.get(key)
    if value is None:
        raise ContractError(f"{kind} payload is missing required field {key!r}")
    return value


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _optional_str(value: Any) -> Optional[str]:
    """Normalise ``null`` / ``""`` to ``None`` — the frontend sends both."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# CatalogVideo  <->  cartoon_catalog.CatalogVideo
# ---------------------------------------------------------------------------


def catalog_video_from_json(raw: Dict[str, Any]) -> CatalogVideo:
    """Build the existing ``cartoon_catalog.CatalogVideo`` from wire JSON.

    ``cacheState`` is deliberately dropped: it is worker-computed metadata for
    the gallery, not part of the frozen dataclass the pipeline consumes.
    """
    if not isinstance(raw, dict):
        raise ContractError(f"CatalogVideo must be an object, got {type(raw).__name__}")
    video_id = str(_require(raw, "videoId", kind="CatalogVideo"))
    return CatalogVideo(
        video_id=video_id,
        url=str(raw.get("url") or f"https://www.youtube.com/watch?v={video_id}"),
        title=str(raw.get("title") or ""),
        duration=int(raw.get("duration") or 0),
        view_count=int(raw.get("viewCount") or 0),
        upload_date=str(raw.get("uploadDate") or ""),
        thumbnail_url=str(
            raw.get("thumbnailUrl") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        ),
        channel_slug=str(raw.get("channelSlug") or ""),
    )


def catalog_video_to_json(
    video: CatalogVideo, cache_state: Optional[str] = None
) -> Dict[str, Any]:
    """Serialise a ``CatalogVideo`` to the camelCase wire shape."""
    payload: Dict[str, Any] = {
        "videoId": video.video_id,
        "url": video.url,
        "title": video.title,
        "duration": int(video.duration),
        "viewCount": int(video.view_count),
        "uploadDate": video.upload_date,
        "thumbnailUrl": video.thumbnail_url,
        "channelSlug": video.channel_slug,
    }
    if cache_state is not None:
        payload["cacheState"] = cache_state
    return payload


# ---------------------------------------------------------------------------
# AyahRangeSpec  <->  ayah_repeater.AyahRange
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AyahRangeSpec:
    """Mirrors ``AyahRangeSpec`` in types.ts and ``ayah_repeater.AyahRange``."""

    surah: int
    start: int
    end: int
    repeats: int = 1
    gap_seconds: float = 0.0

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "AyahRangeSpec":
        if not isinstance(raw, dict):
            raise ContractError(
                f"AyahRangeSpec must be an object, got {type(raw).__name__}"
            )
        return cls(
            surah=int(_require(raw, "surah", kind="AyahRangeSpec")),
            start=int(_require(raw, "start", kind="AyahRangeSpec")),
            end=int(_require(raw, "end", kind="AyahRangeSpec")),
            repeats=int(raw.get("repeats") or 1),
            gap_seconds=float(raw.get("gapSeconds") or 0.0),
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "surah": self.surah,
            "start": self.start,
            "end": self.end,
            "repeats": self.repeats,
            "gapSeconds": self.gap_seconds,
        }

    def to_ayah_range(self) -> "AyahRange":
        """Convert to the pipeline's frozen ``AyahRange`` (which validates)."""
        from yt_audio_filter.ayah_repeater import AyahRange

        return AyahRange(
            surah=self.surah,
            start=self.start,
            end=self.end,
            repeats=self.repeats,
            gap_seconds=self.gap_seconds,
        )


# ---------------------------------------------------------------------------
# RenderSettings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderSettings:
    """Mirrors ``RenderSettings`` — the old Streamlit sidebar, serialised."""

    preset_slug: str = ""
    burn_subtitles: bool = False
    upscale: bool = False
    metadata_path: str = DEFAULT_METADATA_PATH
    playlist_id: Optional[str] = None

    @classmethod
    def from_json(cls, raw: Optional[Dict[str, Any]]) -> "RenderSettings":
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ContractError(
                f"RenderSettings must be an object, got {type(raw).__name__}"
            )
        return cls(
            preset_slug=str(raw.get("presetSlug") or ""),
            burn_subtitles=_as_bool(raw.get("burnSubtitles")),
            upscale=_as_bool(raw.get("upscale")),
            metadata_path=str(raw.get("metadataPath") or DEFAULT_METADATA_PATH),
            playlist_id=_optional_str(raw.get("playlistId")),
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "presetSlug": self.preset_slug,
            "burnSubtitles": self.burn_subtitles,
            "upscale": self.upscale,
            "metadataPath": self.metadata_path,
            "playlistId": self.playlist_id,
        }


# ---------------------------------------------------------------------------
# Job inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SurahJobInput:
    """``kind: "surah"`` — full-surah overlay render."""

    kind: ClassVar[JobKind] = "surah"

    surah_numbers: List[int]
    reciter_slug: str
    visual: CatalogVideo
    settings: RenderSettings = field(default_factory=RenderSettings)

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "SurahJobInput":
        numbers = _require(raw, "surahNumbers", kind="SurahJobInput")
        if not isinstance(numbers, list) or not numbers:
            raise ContractError("SurahJobInput.surahNumbers must be a non-empty array")
        return cls(
            surah_numbers=[int(n) for n in numbers],
            reciter_slug=str(_require(raw, "reciterSlug", kind="SurahJobInput")),
            visual=catalog_video_from_json(_require(raw, "visual", kind="SurahJobInput")),
            settings=RenderSettings.from_json(raw.get("settings")),
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "surahNumbers": list(self.surah_numbers),
            "reciterSlug": self.reciter_slug,
            "visual": catalog_video_to_json(self.visual),
            "settings": self.settings.to_json(),
        }


@dataclass(frozen=True)
class AyahJobInput:
    """``kind: "ayah"`` — ayah-range repetition render (memorisation)."""

    kind: ClassVar[JobKind] = "ayah"

    ranges: List[AyahRangeSpec]
    reciter_slug: str
    visual: CatalogVideo
    settings: RenderSettings = field(default_factory=RenderSettings)

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "AyahJobInput":
        ranges = _require(raw, "ranges", kind="AyahJobInput")
        if not isinstance(ranges, list) or not ranges:
            raise ContractError("AyahJobInput.ranges must be a non-empty array")
        return cls(
            ranges=[AyahRangeSpec.from_json(r) for r in ranges],
            reciter_slug=str(_require(raw, "reciterSlug", kind="AyahJobInput")),
            visual=catalog_video_from_json(_require(raw, "visual", kind="AyahJobInput")),
            settings=RenderSettings.from_json(raw.get("settings")),
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "ranges": [r.to_json() for r in self.ranges],
            "reciterSlug": self.reciter_slug,
            "visual": catalog_video_to_json(self.visual),
            "settings": self.settings.to_json(),
        }


@dataclass(frozen=True)
class MusicRemovalJobInput:
    """``kind: "music_removal"`` — Demucs background-music strip."""

    kind: ClassVar[JobKind] = "music_removal"

    visual: CatalogVideo
    privacy: str = "unlisted"
    playlist_id: Optional[str] = None

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "MusicRemovalJobInput":
        return cls(
            visual=catalog_video_from_json(
                _require(raw, "visual", kind="MusicRemovalJobInput")
            ),
            privacy=str(raw.get("privacy") or "unlisted"),
            playlist_id=_optional_str(raw.get("playlistId")),
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "visual": catalog_video_to_json(self.visual),
            "privacy": self.privacy,
            "playlistId": self.playlist_id,
        }


@dataclass(frozen=True)
class SearchJobInput:
    """``kind: "search"`` — live YouTube keyword search for the gallery."""

    kind: ClassVar[JobKind] = "search"

    query: str
    max_results: int = 25

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "SearchJobInput":
        query = str(_require(raw, "query", kind="SearchJobInput")).strip()
        if not query:
            raise ContractError("SearchJobInput.query must be a non-empty string")
        return cls(query=query, max_results=int(raw.get("maxResults") or 25))

    def to_json(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "query": self.query,
            "maxResults": self.max_results,
        }


JobInput = Union[SurahJobInput, AyahJobInput, MusicRemovalJobInput, SearchJobInput]

_INPUT_PARSERS = {
    "surah": SurahJobInput.from_json,
    "ayah": AyahJobInput.from_json,
    "music_removal": MusicRemovalJobInput.from_json,
    "search": SearchJobInput.from_json,
}


def parse_job_input(raw: Dict[str, Any]) -> JobInput:
    """Parse the ``input`` object of a job into the matching dataclass."""
    if not isinstance(raw, dict):
        raise ContractError(f"Job input must be an object, got {type(raw).__name__}")
    kind = raw.get("kind")
    parser = _INPUT_PARSERS.get(str(kind))
    if parser is None:
        raise ContractError(f"Unknown job kind: {kind!r}")
    return parser(raw)


# ---------------------------------------------------------------------------
# Job envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobProgress:
    """Mirrors ``JobProgress``."""

    stage: str = "Queued"
    percent: Optional[int] = None
    log: List[str] = field(default_factory=list)
    updated_at: int = 0

    @classmethod
    def from_json(cls, raw: Optional[Dict[str, Any]]) -> "JobProgress":
        raw = raw or {}
        percent = raw.get("percent")
        log = raw.get("log")
        return cls(
            stage=str(raw.get("stage") or "Queued"),
            percent=None if percent is None else int(percent),
            log=[str(line) for line in log] if isinstance(log, list) else [],
            updated_at=int(raw.get("updatedAt") or 0),
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "percent": self.percent,
            "log": list(self.log),
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True)
class JobResult:
    """Mirrors ``JobResult``. ``to_json`` omits unset fields entirely, so a
    merge on the frontend never clobbers a previous value with ``null``."""

    blob_url: Optional[str] = None
    #: Blob pathname; the Studio needs it to mint presigned preview URLs,
    #: because the render store is private and blob_url alone 403s.
    blob_pathname: Optional[str] = None
    file_name: Optional[str] = None
    size_bytes: Optional[int] = None
    youtube_video_id: Optional[str] = None
    search_results: Optional[List[Dict[str, Any]]] = None

    @classmethod
    def from_json(cls, raw: Optional[Dict[str, Any]]) -> "JobResult":
        raw = raw or {}
        size = raw.get("sizeBytes")
        results = raw.get("searchResults")
        return cls(
            blob_url=_optional_str(raw.get("blobUrl")),
            blob_pathname=_optional_str(raw.get("blobPathname")),
            file_name=_optional_str(raw.get("fileName")),
            size_bytes=None if size is None else int(size),
            youtube_video_id=_optional_str(raw.get("youtubeVideoId")),
            search_results=list(results) if isinstance(results, list) else None,
        )

    def to_json(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self.blob_url is not None:
            payload["blobUrl"] = self.blob_url
        if self.blob_pathname is not None:
            payload["blobPathname"] = self.blob_pathname
        if self.file_name is not None:
            payload["fileName"] = self.file_name
        if self.size_bytes is not None:
            payload["sizeBytes"] = self.size_bytes
        if self.youtube_video_id is not None:
            payload["youtubeVideoId"] = self.youtube_video_id
        if self.search_results is not None:
            payload["searchResults"] = list(self.search_results)
        return payload

    def merged_with(self, other: "JobResult") -> "JobResult":
        """Overlay ``other``'s set fields on top of this result.

        Used by the upload path so the blob preview URL from the original
        render survives alongside the new ``youtubeVideoId``.
        """
        return JobResult(
            blob_url=other.blob_url if other.blob_url is not None else self.blob_url,
            blob_pathname=(
                other.blob_pathname
                if other.blob_pathname is not None
                else self.blob_pathname
            ),
            file_name=other.file_name if other.file_name is not None else self.file_name,
            size_bytes=(
                other.size_bytes if other.size_bytes is not None else self.size_bytes
            ),
            youtube_video_id=(
                other.youtube_video_id
                if other.youtube_video_id is not None
                else self.youtube_video_id
            ),
            search_results=(
                other.search_results
                if other.search_results is not None
                else self.search_results
            ),
        )


@dataclass(frozen=True)
class Job:
    """Mirrors ``Job`` — the full envelope handed back by ``/api/worker/claim``."""

    id: str
    kind: JobKind
    status: JobStatus
    input: JobInput
    progress: JobProgress = field(default_factory=JobProgress)
    result: Optional[JobResult] = None
    error: Optional[str] = None
    error_details: Optional[str] = None
    created_at: int = 0
    started_at: Optional[int] = None
    finished_at: Optional[int] = None
    upload_requested: bool = False
    #: Restricts the job to one machine. ``None`` means "any worker". The
    #: Studio does the filtering at claim time; the worker only double-checks
    #: so a routing bug fails loudly instead of rendering on the wrong laptop.
    target_worker_id: Optional[str] = None
    #: Worker that actually claimed the job, echoed back by the Studio.
    claimed_by: Optional[str] = None

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "Job":
        if not isinstance(raw, dict):
            raise ContractError(f"Job must be an object, got {type(raw).__name__}")
        job_input = parse_job_input(_require(raw, "input", kind="Job"))
        result_raw = raw.get("result")
        return cls(
            id=str(_require(raw, "id", kind="Job")),
            kind=str(raw.get("kind") or job_input.kind),  # type: ignore[arg-type]
            status=str(raw.get("status") or "claimed"),  # type: ignore[arg-type]
            input=job_input,
            progress=JobProgress.from_json(raw.get("progress")),
            result=JobResult.from_json(result_raw) if result_raw else None,
            error=_optional_str(raw.get("error")),
            error_details=_optional_str(raw.get("errorDetails")),
            created_at=int(raw.get("createdAt") or 0),
            started_at=raw.get("startedAt"),
            finished_at=raw.get("finishedAt"),
            upload_requested=_as_bool(raw.get("uploadRequested")),
            target_worker_id=_optional_str(raw.get("targetWorkerId")),
            claimed_by=_optional_str(raw.get("claimedBy")),
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "targetWorkerId": self.target_worker_id,
            "claimedBy": self.claimed_by,
            "input": self.input.to_json(),
            "progress": self.progress.to_json(),
            "result": self.result.to_json() if self.result is not None else None,
            "error": self.error,
            "errorDetails": self.error_details,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "uploadRequested": self.upload_requested,
        }


@dataclass(frozen=True)
class WorkerHeartbeat:
    """Mirrors ``WorkerHeartbeat`` (the body of ``POST /api/worker/claim``).

    ``worker_id`` is what makes targeted jobs possible: the Studio matches it
    against a job's ``targetWorkerId`` and only hands over work meant for this
    machine.
    """

    hostname: str
    gpu: Optional[str]
    version: str
    worker_id: str = ""
    current_job_id: Optional[str] = None
    #: False on a light install with no Demucs/PyTorch.
    can_remove_music: bool = True

    def to_json(self) -> Dict[str, Any]:
        return {
            "workerId": self.worker_id,
            "hostname": self.hostname,
            "gpu": self.gpu,
            "version": self.version,
            "currentJobId": self.current_job_id,
            "canRemoveMusic": self.can_remove_music,
        }
