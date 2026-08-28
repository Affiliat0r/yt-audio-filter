"""Parse a one-line production request into work items.

The command exists so a whole evening's output can be described the way you
would say it out loud::

    niloya, Quran (AtTakathur - AnNaas, Ghamdi,
                   background: toy factory with train, not scary),
    riko, abc alfabet

Everything here is pure parsing — no network, no rendering. The runner in
``workflow_runner`` turns these items into finished, published videos.

Two things make this more than ``split(",")``:

* a Quran item carries its own commas inside parentheses, so splitting has to
  respect nesting;
* surah and reciter names arrive in whatever transliteration was to hand.
  ``AnNaas``, ``An-Nas`` and ``annas`` are one surah; ``Ghamdi`` is
  ``Saad Al-Ghamdi``. Matching is therefore on a normalised form rather than
  the literal string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Union

from .exceptions import YTAudioFilterError

#: A guard against a typo turning into a night of rendering. `x3` is a normal
#: request; `x999` is a slip, and would publish 999 videos to a real channel.
MAX_COUNT = 20


class WorkflowParseError(YTAudioFilterError):
    """The request could not be understood."""


@dataclass
class CartoonItem:
    """A cartoon to strip the background music from.

    Either a search (``niloya``) or a pasted link. A link removes the guesswork
    when you already know the exact episode you want.
    """

    query: str
    count: int = 1
    #: Set when the user pasted a link instead of a search term.
    url: Optional[str] = None
    video_id: Optional[str] = None
    #: Optional name given as ``niloya: <url>``.
    label: Optional[str] = None

    @property
    def playlist_name(self) -> Optional[str]:
        """None for an unlabelled link — a URL carries no name of its own, so
        the runner derives one from the video's channel after fetching it."""
        if self.label:
            return _title_case(self.label)
        if self.url:
            return None
        return _title_case(self.query)


@dataclass
class QuranItem:
    """A recitation to lay over a looping visual."""

    surah_numbers: List[int]
    reciter_slug: str
    reciter_name: str
    background_query: Optional[str] = None
    exclude_terms: List[str] = field(default_factory=list)
    count: int = 1

    @property
    def playlist_name(self) -> str:
        # Every Quran render lands in one playlist regardless of which surahs
        # it covers — a playlist per surah range would be unusable.
        return "Quran"


WorkItem = Union[CartoonItem, QuranItem]


def _title_case(text: str) -> str:
    return " ".join(word[:1].upper() + word[1:] for word in text.split())


def normalise_name(name: str) -> str:
    """Fold a transliteration down to something comparable.

    Separators vary (``An-Nas`` / ``An Nas`` / ``AnNas``) and so do doubled
    vowels (``AnNaas``), which is the bulk of the disagreement between how
    people write surah names and how the manifest spells them.
    """
    folded = re.sub(r"[^a-z]", "", name.lower())
    # Collapse runs of the same vowel: "naas" -> "nas", "ikhlaas" -> "ikhlas".
    return re.sub(r"([aeiou])\1+", r"\1", folded)


def _surah_index() -> dict:
    from .surah_detector import _SURAHS

    index = {}
    for name, number, _patterns in _SURAHS:
        if number is None:
            continue
        index.setdefault(normalise_name(name), number)
        # "An-Nas" is also written without its article.
        bare = re.sub(r"^(al|an|as|at|ar|ash|adh|az)", "", normalise_name(name))
        index.setdefault(bare, number)
    return index


def parse_surah_ref(text: str) -> int:
    """A surah name or number to its number."""
    text = text.strip()
    if text.isdigit():
        number = int(text)
        if not 1 <= number <= 114:
            raise WorkflowParseError(f"Surah number out of range: {number}")
        return number

    index = _surah_index()
    key = normalise_name(text)
    if key in index:
        return index[key]
    raise WorkflowParseError(
        f"Unknown surah: {text!r}",
        "Use a name like 'An-Nas' or 'AtTakathur', or a number 1-114.",
    )


def _resolve_reciter(text: str):
    """Match a loosely written reciter name against the manifest."""
    from .quran_audio_source import list_reciters

    key = normalise_name(text)
    reciters = list_reciters()
    for reciter in reciters:
        if normalise_name(reciter.slug) == key:
            return reciter
    # Substring both ways: "Ghamdi" is inside "Saad Al-Ghamdi", and someone
    # typing the full name should match the shorter slug too.
    for reciter in reciters:
        display = normalise_name(reciter.display_name)
        if key and (key in display or normalise_name(reciter.slug) in key):
            return reciter
    raise WorkflowParseError(
        f"Unknown reciter: {text!r}",
        "Known reciters: " + ", ".join(sorted(r.slug for r in reciters)),
    )


def split_top_level(text: str, separator: str = ",") -> List[str]:
    """Split on ``separator``, ignoring anything inside parentheses."""
    parts, depth, current = [], 0, []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise WorkflowParseError(
                    "Unexpected ')' in the request",
                    "Check the brackets around a Quran item.",
                )
        if char == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if depth != 0:
        raise WorkflowParseError(
            "Missing a closing ')' in the request",
            "A Quran item looks like: Quran (An-Nas, Ghamdi)",
        )
    parts.append("".join(current))
    return [p.strip() for p in parts]


#: A trailing repeat count. The space before `x` is optional because people
#: type `niloyax2` as readily as `niloya x2`. That does mean a search term
#: genuinely ending in "x<number>" would be misread — the printed plan shows
#: the resolved count for exactly that reason, and nothing runs before you
#: have seen it.
_COUNT_RE = re.compile(r"\s*x\s*(\d+)\s*$", re.IGNORECASE)


def _split_count(text: str) -> tuple:
    """Peel a trailing ``xN`` off an item. ``"abc x"`` is not a count."""
    match = _COUNT_RE.search(text)
    if not match:
        return text.strip(), 1
    count = int(match.group(1))
    if not 1 <= count <= MAX_COUNT:
        raise WorkflowParseError(
            f"Count must be between 1 and {MAX_COUNT}, got {count}",
            "This guard exists so a typo cannot publish a hundred videos.",
        )
    return text[: match.start()].strip(), count


_QURAN_RE = re.compile(r"^quran\s*\((.*)\)\s*$", re.IGNORECASE | re.DOTALL)


def _parse_quran(body: str, count: int) -> QuranItem:
    surahs: List[int] = []
    reciter = None
    background: Optional[str] = None
    excludes: List[str] = []

    for raw in split_top_level(body):
        if not raw:
            continue
        lowered = raw.lower()

        if lowered.startswith("background:"):
            background = raw.split(":", 1)[1].strip() or None
            continue
        if lowered.startswith(("not ", "no ", "geen ")):
            excludes.append(raw.split(" ", 1)[1].strip().lower())
            continue

        expanded = _parse_surah_expression(raw)
        if expanded is not None:
            surahs.extend(expanded)
            continue

        # Not surahs, so it must be the reciter.
        if reciter is not None:
            raise WorkflowParseError(
                f"Could not read {raw!r} as a surah or a reciter",
                "A Quran item looks like: Quran (At-Takathur - An-Nas, Ghamdi)",
            )
        reciter = _resolve_reciter(raw)

    if not surahs:
        raise WorkflowParseError(
            "A Quran item needs at least one surah",
            "For example: Quran (At-Takathur - An-Nas, Ghamdi)",
        )
    if reciter is None:
        raise WorkflowParseError(
            "A Quran item needs a reciter",
            "For example: Quran (An-Nas, Ghamdi)",
        )

    return QuranItem(
        surah_numbers=surahs,
        reciter_slug=reciter.slug,
        reciter_name=reciter.display_name,
        background_query=background,
        exclude_terms=excludes,
        count=count,
    )


def _try_surah(text: str) -> Optional[int]:
    try:
        return parse_surah_ref(text)
    except WorkflowParseError:
        return None


def _parse_surah_expression(raw: str) -> Optional[List[int]]:
    """Read a surah, a ``+`` list, or a ``-`` range. None if it is neither.

    Order matters. Most surah names contain a hyphen of their own
    (``Al-Fatiha``), so a range has to be recognised without mistaking that
    internal hyphen for the range separator — splitting on the first ``-``
    would read ``Al-Fatiha`` as "Al" through "Fatiha".
    """
    # A whole name first: settles Al-Fatiha before any splitting happens.
    single = _try_surah(raw)
    if single is not None:
        return [single]

    if "+" in raw:
        parts = [_try_surah(p) for p in raw.split("+")]
        if all(p is not None for p in parts):
            return [p for p in parts if p is not None]
        return None

    # A range: try every hyphen and accept the split where both halves are
    # surahs. "Al-Fatiha - An-Nas" only works at the middle one.
    for index, char in enumerate(raw):
        if char != "-":
            continue
        start = _try_surah(raw[:index])
        end = _try_surah(raw[index + 1 :])
        if start is None or end is None:
            continue
        step = 1 if end >= start else -1
        return list(range(start, end + step, step))
    return None


#: Matches a YouTube watch link, a youtu.be short link, or a /shorts/ link.
_YOUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s]*v=|shorts/|embed/)"
    r"|youtu\.be/)([A-Za-z0-9_-]{11})[^\s]*",
    re.IGNORECASE,
)
_ANY_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _parse_url_item(chunk: str, count: int) -> Optional[CartoonItem]:
    """A pasted link, optionally labelled ``name: <url>``. None if no URL."""
    if not _ANY_URL_RE.search(chunk):
        return None

    match = _YOUTUBE_URL_RE.search(chunk)
    if not match:
        raise WorkflowParseError(
            "Only YouTube links are supported",
            f"Could not read a YouTube video id from: {chunk.strip()!r}",
        )
    if count != 1:
        raise WorkflowParseError(
            "A link already names one video, so xN makes no sense",
            "Drop the xN, or paste the other links you want as separate items.",
        )

    url = match.group(0)
    # Anything before the link is a label. The separator may be ':', '-' or
    # '->', but 'https:' must not be mistaken for one — hence slicing at the
    # match rather than splitting on ':'.
    label = chunk[: match.start()].strip().rstrip(":-→>").strip()
    return CartoonItem(
        query=label or url,
        count=1,
        url=url,
        video_id=match.group(1),
        label=label or None,
    )


def parse_request(text: str) -> List[WorkItem]:
    """Turn a request line into work items, in the order given."""
    if not text or not text.strip():
        raise WorkflowParseError(
            "Nothing to do",
            'Try: niloya, Quran (An-Nas, Ghamdi), riko',
        )

    items: List[WorkItem] = []
    for chunk in split_top_level(text):
        if not chunk:
            continue
        body, count = _split_count(chunk)
        if not body:
            continue
        match = _QURAN_RE.match(body)
        if match:
            items.append(_parse_quran(match.group(1), count))
            continue
        url_item = _parse_url_item(body, count)
        if url_item is not None:
            items.append(url_item)
            continue
        items.append(CartoonItem(query=body, count=count))

    if not items:
        raise WorkflowParseError("Nothing to do")
    return items
