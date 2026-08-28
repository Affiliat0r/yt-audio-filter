"""CLI entry point for ``yt-studio`` — a night's output from one line.

::

    yt-studio "niloya, Quran (AtTakathur - AnNaas, Ghamdi,
                background: toy factory with train, not scary), riko"
    yt-studio --dry-run "niloya x2"
    yt-studio "niloya: https://youtu.be/U_EhMEOolI0"

The command publishes publicly and unattended, so it is deliberately
front-loaded: the parsed plan is printed **before** anything happens, and on a
terminal it asks once before the first upload. ``--yes`` skips the prompt for
cron; ``--dry-run`` never prompts because it never publishes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .exceptions import YTAudioFilterError
from .logger import setup_logger
from .workflow import QuranItem, WorkItem, WorkflowParseError, parse_request
from .workflow_runner import (
    DEFAULT_CACHE_DIR,
    DEFAULT_METADATA_PATH,
    ItemResult,
    WorkflowSummary,
    describe_item,
    is_link_item,
    run_workflow,
)

#: Wide enough for a surah range plus a reciter, narrow enough to stay in an
#: 80-column terminal alongside the status column.
LABEL_WIDTH = 46


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt-studio",
        description=(
            "Produce and publish a whole request in one line. Each item is "
            "either a cartoon to strip the background music from (a search "
            "term or a pasted YouTube link), or a Quran recitation to lay "
            "over a looping visual."
        ),
        epilog=(
            "Examples:\n"
            '  yt-studio "niloya, riko x2"\n'
            '  yt-studio "Quran (An-Nas, Ghamdi, background: toy train, not scary)"\n'
            '  yt-studio --dry-run "niloya: https://youtu.be/U_EhMEOolI0"\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "request",
        help="The request line, e.g. 'niloya, Quran (An-Nas, Ghamdi), riko'",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Resolve the sources and run the duplicate check, then stop before "
            "rendering, uploading, or writing anything."
        ),
    )
    parser.add_argument(
        "--privacy",
        choices=["public", "unlisted", "private"],
        default="public",
        help="Privacy for uploads and any playlist created (default: public)",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=DEFAULT_METADATA_PATH,
        help=f"Overlay metadata template for Quran items (default: {DEFAULT_METADATA_PATH})",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"Download and search cache (default: {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation prompt (for unattended runs)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show the full pipeline log alongside the progress lines",
    )
    return parser


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------


def _ellipsis(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _playlist_label(item: WorkItem) -> str:
    name = item.playlist_name
    if name:
        return f'playlist "{name}"'
    # Only an unlabelled link gets here; the channel supplies the name once the
    # video has been fetched.
    return "playlist named after the video's channel"


def format_plan(items: Sequence[WorkItem], privacy: str, dry_run: bool) -> List[str]:
    """The parsed request, written back out so mistakes are obvious."""
    total = sum(item.count for item in items)
    heading = f"Plan: {total} video(s) from {len(items)} item(s)"
    heading += "  [dry run]" if dry_run else f"  [privacy: {privacy}]"
    lines = [heading, ""]

    for number, item in enumerate(items, start=1):
        if is_link_item(item):
            kind = "link"
        else:
            kind = "quran" if isinstance(item, QuranItem) else "cartoon"
        label = _ellipsis(describe_item(item), LABEL_WIDTH)
        lines.append(
            f"  {number}. {label:<{LABEL_WIDTH}} {kind:<8} x{item.count}  {_playlist_label(item)}"
        )
        if isinstance(item, QuranItem):
            detail = []
            if item.background_query:
                detail.append(f"background {item.background_query!r}")
            else:
                detail.append("background from the curated catalog")
            if item.exclude_terms:
                detail.append("excluding " + ", ".join(repr(t) for t in item.exclude_terms))
            lines.append(" " * 6 + "; ".join(detail))
    lines.append("")
    return lines


def _result_status(result: ItemResult) -> str:
    if result.error:
        return f"FAILED: {result.error.splitlines()[0]}"
    if result.skipped_reason:
        return f"skipped: {result.skipped_reason}"
    if result.dry_run:
        title = result.planned_title or result.source_title
        return f"would publish {title!r}"
    if result.uploaded_url:
        where = f' → "{result.playlist_name}"' if result.playlist_id else ""
        return f"published {result.uploaded_url}{where}"
    if result.rendered_path:
        return f"rendered {result.rendered_path.name} (not published)"
    return "nothing produced"


def format_summary(summary: WorkflowSummary) -> List[str]:
    lines = ["", "Summary" + ("  [dry run — nothing was published]" if summary.dry_run else "")]
    for number, result in enumerate(summary.results, start=1):
        label = _ellipsis(f"{result.label} ({result.index}/{result.total})", LABEL_WIDTH)
        lines.append(f"  {number}. {label:<{LABEL_WIDTH}} {_result_status(result)}")
        if result.source_title and not result.error:
            lines.append(" " * 6 + f"source: {_ellipsis(result.source_title, 64)}")
        if result.skipped and not result.skipped_reason:
            lines.append(" " * 6 + f"passed over {len(result.skipped)} candidate(s)")
        if result.playlist_error:
            lines.append(" " * 6 + f"playlist: {result.playlist_error}")

    done = [r for r in summary.results if r.ok and not r.skipped_reason]
    verb = "planned" if summary.dry_run else "produced"
    lines.append("")
    lines.append(
        f"  {len(done)} {verb}, {len(summary.skipped)} skipped, {len(summary.failures)} failed"
    )
    for result in summary.failures:
        lines.append(f"  ! {result.label}: {result.error}")
    return lines


def make_printer(verbose: bool):
    """An ``on_event`` callback that prints readable live progress.

    Progress from a render fires many times a second; it is throttled to a new
    stage or a 10-point jump so the transcript stays readable.
    """
    state: Dict[str, object] = {"stage": None, "percent": -100}

    def printer(kind: str, message: str, data: Dict[str, object]) -> None:
        if kind == "render":
            # Each render restarts the bar, so the throttle has to restart too
            # or the next item's first progress line is swallowed.
            state["stage"] = None
            state["percent"] = -100
        if kind == "progress":
            stage = data.get("stage")
            percent = int(data.get("percent") or 0)
            last = int(state["percent"])  # type: ignore[arg-type]
            if stage == state["stage"] and percent - last < 10 and not verbose:
                return
            state["stage"] = stage
            state["percent"] = percent
        prefix = {
            "item": "\n▶",
            "item-step": "\n▶",
            "skip": "  -",
            "pick": "  ✓",
            "dry-run": "  =",
            "item-failed": "  !",
            "playlist-failed": "  !",
        }.get(kind, "  ·")
        print(f"{prefix} {message}", flush=True)

    return printer


def _confirm(items: Sequence[WorkItem], privacy: str) -> bool:
    total = sum(item.count for item in items)
    print(
        f"This will render and publish {total} video(s) to YouTube as {privacy}.",
        flush=True,
    )
    try:
        answer = input("Continue? [y/N] ").strip().lower()
    except EOFError:  # pragma: no cover - only when stdin dies mid-prompt
        return False
    return answer in {"y", "yes"}


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # The runner logs every event, and the printer below shows the same lines
    # on stdout; keeping the logger quiet unless --verbose stops each step
    # appearing twice, while warnings and errors always get through.
    logger = setup_logger(verbose=args.verbose, quiet=not args.verbose)

    try:
        items = parse_request(args.request)
    except WorkflowParseError as exc:
        print(f"Could not read the request: {exc}", file=sys.stderr)
        return 2

    for line in format_plan(items, args.privacy, args.dry_run):
        print(line)

    if not args.dry_run and not args.yes and sys.stdin.isatty():
        if not _confirm(items, args.privacy):
            print("Aborted; nothing was rendered or published.")
            return 1

    try:
        summary = run_workflow(
            items,
            dry_run=args.dry_run,
            cache_dir=args.cache_dir,
            metadata_path=args.metadata,
            privacy=args.privacy,
            on_event=make_printer(args.verbose),
        )
    except YTAudioFilterError as exc:
        logger.error(str(exc))
        print(f"Run failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted; anything already published stays published.", file=sys.stderr)
        return 130

    for line in format_summary(summary):
        print(line)
    return summary.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
