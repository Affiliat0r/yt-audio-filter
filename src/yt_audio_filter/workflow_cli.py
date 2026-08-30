"""CLI entry point for ``yt-studio`` — a night's output from one line.

::

    yt-studio "niloya, Quran (AtTakathur - AnNaas, Ghamdi,
                background: toy factory with train, not scary), riko"
    yt-studio --dry-run "niloya x2"
    yt-studio "niloya: https://youtu.be/U_EhMEOolI0"

The command publishes publicly and unattended, so it is deliberately
front-loaded. Two gates stand in front of any work:

1. the parsed request is echoed back, so a misread item is obvious;
2. the sources it picked are printed **as YouTube URLs you can open**, and
   nothing is downloaded, rendered or published until you say so.

How the second gate behaves depends on who is driving:

* **At a terminal** it asks. Approve everything, reject everything, or name the
  picks you do not want — those get the next candidate and you are asked again.
* **Without a terminal** (an agent, cron, a pipe) a prompt is impossible, so it
  saves the plan to ``state/last_plan.json``, prints the URLs, and exits ``10``
  meaning "awaiting approval". ``yt-studio --approve`` then produces *that saved
  plan verbatim* — it never searches again, because a second search can rank
  different videos and the approval was given to specific ones.

``--yes`` skips the gate entirely (resolve and run in one go, for cron), and
``--dry-run`` never prompts and never writes because it never publishes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from .exceptions import YTAudioFilterError
from .logger import setup_logger
from .workflow import QuranItem, WorkItem, WorkflowParseError, parse_request
from . import workflow_runner
from .workflow_runner import (
    DEFAULT_CACHE_DIR,
    DEFAULT_METADATA_PATH,
    DEFAULT_PLAN_PATH,
    ItemResult,
    PlanError,
    PlannedPick,
    WorkflowSummary,
    clamp_height,
    create_planner,
    describe_item,
    discard_plan,
    is_link_item,
    load_plan,
    run_plan,
    run_workflow,
    save_plan,
    surah_names,
)

#: Wide enough for a surah range plus a reciter, narrow enough to stay in an
#: 80-column terminal alongside the status column.
LABEL_WIDTH = 46

#: "I resolved the picks, nobody has approved them yet, and I did nothing."
#: Distinct from 1 (something failed) and 2 (the request was unreadable) so a
#: script can tell "go and look at the URLs" apart from "this went wrong".
EXIT_AWAITING_APPROVAL = 10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt-studio",
        description=(
            "Produce and publish a whole request in one line. Each item is "
            "either a cartoon to strip the background music from (a search "
            "term or a pasted YouTube link), or a Quran recitation to lay "
            "over a looping visual. The sources it picks are shown as URLs "
            "for approval before anything is downloaded."
        ),
        epilog=(
            "Examples:\n"
            '  yt-studio "niloya, riko x2"            # pick, show URLs, ask\n'
            '  yt-studio --approve "niloya, riko x2"  # produce the picks you saw\n'
            '  yt-studio --yes "niloya"               # no gate, for cron\n'
            '  yt-studio --dry-run "niloya: https://youtu.be/U_EhMEOolI0"\n'
            "\n"
            f"Exit codes: 0 done, 1 failed or rejected, 2 bad request, "
            f"{EXIT_AWAITING_APPROVAL} awaiting approval.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "request",
        nargs="?",
        help=(
            "The request line, e.g. 'niloya, Quran (An-Nas, Ghamdi), riko'. "
            "Optional with --approve, where it is checked against the saved plan."
        ),
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help=(
            "Produce the plan saved by an earlier run. Resolves nothing: "
            "exactly the picks whose URLs you were shown are made."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "With --approve, produce the saved plan even though it was "
            "resolved for a different request line."
        ),
    )
    parser.add_argument(
        "--plan-file",
        type=Path,
        default=DEFAULT_PLAN_PATH,
        metavar="PATH",
        help=f"Where the plan awaiting approval lives (default: {DEFAULT_PLAN_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Resolve the sources and run the duplicate check, then stop before "
            "rendering, uploading, or writing anything — not even a plan file."
        ),
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Output height (default: {workflow_runner.DEFAULT_HEIGHT}, floor: "
            f"{workflow_runner.MIN_HEIGHT}). Overlay renders target it; a "
            "music-removal source smaller than it is enlarged to match."
        ),
    )
    parser.add_argument(
        "--upscale",
        "--sharp",
        dest="upscale",
        action="store_true",
        default=True,
        help=(
            "Reconstruct detail with Real-ESRGAN rather than stretching (the "
            "default). Runs before music removal and targets "
            f"{workflow_runner.MIN_HEIGHT}p unless --height says otherwise. "
            "Long sources are upscaled in chunks; a machine without a Vulkan "
            "GPU falls back to a plain scale rather than failing."
        ),
    )
    parser.add_argument(
        "--no-upscale",
        dest="upscale",
        action="store_false",
        help=(
            "Scale instead of reconstructing. Minutes rather than most of an "
            "hour per episode, at the cost of interpolated detail."
        ),
    )
    parser.add_argument(
        "--privacy",
        choices=["public", "unlisted", "private"],
        default=None,
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
        help="Skip the approval gate entirely and produce every pick (for cron)",
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


def _duration_label(seconds: int) -> str:
    """``mm:ss``, or an honest shrug when nothing probed the video yet."""
    if seconds <= 0:
        return "length unknown"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}:{remainder:02d}"


def _pick_lines(number: int, pick: PlannedPick) -> List[str]:
    """One pick, with the URL on its own line so a terminal makes it clickable."""
    repetition = f" ({pick.index}/{pick.total})" if pick.total > 1 else ""
    if pick.error:
        tag = "unresolved"
    elif pick.skipped_reason:
        tag = "skipped"
    else:
        tag = pick.kind
    lines = [f"  {number}. {_ellipsis(pick.label + repetition, LABEL_WIDTH)}  [{tag}]"]
    indent = " " * 5

    if pick.error:
        lines.append(indent + pick.error.splitlines()[0])
        return lines
    if pick.skipped_reason:
        lines.append(indent + f"not producing it: {pick.skipped_reason}")
        return lines

    item = pick.item
    if isinstance(item, QuranItem):
        # The recitation is the point of a Quran item, and it comes from a
        # manifest rather than a URL — so name it, then show the visual that
        # actually needs eyes on it.
        lines.append(indent + f"surahs: {', '.join(surah_names(item.surah_numbers))}")
        lines.append(indent + f"reciter: {item.reciter_name}")
        prefix = "background: "
    else:
        prefix = ""

    video = pick.video
    if video is not None:
        title = _ellipsis(video.title, 66)
        lines.append(indent + f"{prefix}{title}  ·  {_duration_label(video.duration)}")
        lines.append(indent + pick.url)
    if pick.playlist_name:
        lines.append(indent + f'→ playlist "{pick.playlist_name}"')
    else:
        lines.append(indent + "→ playlist named after the video's channel")
    if pick.skipped:
        lines.append(indent + f"(passed over {len(pick.skipped)} other candidate(s))")
    return lines


def format_picks(picks: Sequence[PlannedPick], *, heading: str) -> List[str]:
    """The resolved picks as openable URLs.

    This is the whole point of the gate: nothing has been downloaded, so the
    only way to check a pick is to watch the video it points at.
    """
    lines = [heading, ""]
    for number, pick in enumerate(picks, start=1):
        lines.extend(_pick_lines(number, pick))
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
            "produce": "\n▶",
            "skip": "  -",
            "pick": "  ✓",
            "dry-run": "  =",
            "item-failed": "  !",
            "playlist-failed": "  !",
        }.get(kind, "  ·")
        print(f"{prefix} {message}", flush=True)

    return printer


# ---------------------------------------------------------------------------
# The approval gate
# ---------------------------------------------------------------------------

#: What the interactive prompt accepts, beyond a list of pick numbers.
_APPROVE_WORDS = {"y", "yes", "a", "all", "approve", "ok", "go"}
_REJECT_WORDS = {"n", "no", "q", "quit", "abort", "stop", "none"}

#: A decision: one of the words below, or the pick numbers to push back on.
Decision = Union[str, List[int]]

APPROVED = "approved"
REJECTED = "rejected"
#: ``isatty()`` said yes but stdin is at EOF — a script, a pipe, or an agent
#: driving the command. Windows in particular reports a console handle as a
#: terminal even when nothing will ever be typed into it, so the prompt itself
#: is the only reliable test.
UNREADABLE = "unreadable"


def _parse_decision(answer: str, count: int) -> Optional[Decision]:
    """Read one answer, or None when it made no sense and should be re-asked."""
    cleaned = answer.strip().lower()
    if not cleaned:
        return None
    if cleaned in _APPROVE_WORDS:
        return APPROVED
    if cleaned in _REJECT_WORDS:
        return REJECTED

    # "2", "1,3", "1 3", "no 2" — anything that is a list of pick numbers is a
    # push-back on those picks specifically.
    tokens = [t for t in cleaned.replace(",", " ").split() if t]
    numbers: List[int] = []
    for token in tokens:
        if not token.isdigit():
            return None
        number = int(token)
        if not 1 <= number <= count:
            return None
        numbers.append(number)
    return numbers or None


#: How many unreadable answers to sit through before giving up. A person gets
#: several tries; a stdin that only ever returns the same thing (a pipe that
#: claims to be a terminal) must not spin forever in front of an upload.
MAX_PROMPT_ATTEMPTS = 5


def _ask_decision(count: int) -> Decision:
    """Prompt until the answer is understood. Never guesses on ambiguity."""
    print("Open the URLs above, then decide:")
    print("  y        approve every pick and start rendering")
    print("  n        reject everything; nothing is rendered or published")
    print("  1 3      reject those picks and take the next candidate for each")
    for _ in range(MAX_PROMPT_ATTEMPTS):
        try:
            answer = input("Your call [y/n/numbers]: ")
        except EOFError:
            print()
            return UNREADABLE
        decision = _parse_decision(answer, count)
        if decision is not None:
            return decision
        print(f"Answer y, n, or the number(s) of the picks to reject (1-{count}).")
    # Answers nobody could read are not consent. Defaulting to "reject" costs a
    # re-run; defaulting the other way would publish.
    print("No usable answer, so nothing was approved.")
    return REJECTED


def _approval_loop(planner, picks: List[PlannedPick]) -> Tuple[str, List[PlannedPick]]:
    """Show, ask, re-pick, repeat.

    Returns the outcome and the picks as they stand — which after a push-back
    are not the ones first shown, and are what gets produced or saved.
    """
    while True:
        for line in format_picks(picks, heading="Picks — open each URL before approving:"):
            print(line)
        decision = _ask_decision(len(picks))
        if decision == APPROVED:
            return APPROVED, picks
        if decision in (REJECTED, UNREADABLE):
            return str(decision), picks
        assert isinstance(decision, list)
        print(f"Rejected pick(s) {', '.join(str(n) for n in decision)}; finding replacements…")
        picks = planner.reject(picks, decision)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _approve_saved_plan(args: argparse.Namespace, logger) -> int:
    """Produce the plan an earlier run left behind."""
    try:
        plan = load_plan(args.plan_file)
    except PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not plan.matches(args.request) and not args.force:
        print(
            "The saved plan was resolved for a different request.\n"
            f"  saved:   {plan.request!r}\n"
            f"  current: {args.request!r}\n"
            "Re-run yt-studio with the current request to resolve a fresh plan, "
            "or pass --force to produce the saved one anyway.",
            file=sys.stderr,
        )
        return 1

    print(f"Approved: {len(plan.producible)} pick(s) from {plan.created_at}")
    for line in format_picks(plan.picks, heading=f"Producing the plan for {plan.request!r}:"):
        print(line)

    # Consumed before any work starts. An approval is good exactly once: the
    # duplicate check ran when the plan was resolved, so re-approving the same
    # file would render and publish the same sources a second time.
    discard_plan(args.plan_file)

    try:
        summary = run_plan(
            plan,
            cache_dir=args.cache_dir,
            metadata_path=args.metadata,
            privacy=args.privacy,
            target_height=clamp_height(args.height) if args.height else None,
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


def _widen_output_encoding() -> None:
    """Stop a narrow console killing the run mid-way.

    Windows still hands Python a cp1252 stdout when the output is piped, and
    this command prints arrows, bullets and whatever YouTube put in a video
    title. A ``UnicodeEncodeError`` three items into a render would lose the
    run over a decoration, so switch to UTF-8 and, failing that, to replacing
    the characters that will not fit.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - a replaced stream in tests
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - already closed/detached
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _widen_output_encoding()
    parser = build_parser()
    args = parser.parse_args(argv)

    # The runner logs every event, and the printer below shows the same lines
    # on stdout; keeping the logger quiet unless --verbose stops each step
    # appearing twice, while warnings and errors always get through.
    logger = setup_logger(verbose=args.verbose, quiet=not args.verbose)

    if args.approve:
        if args.dry_run:
            parser.error("--approve produces a saved plan, so it cannot be a --dry-run")
        return _approve_saved_plan(args, logger)
    if args.force:
        parser.error("--force only means something with --approve")
    if args.request is None:
        parser.error("a request is required (or --approve to produce a saved plan)")

    privacy = args.privacy or "public"
    try:
        height = _target_height(args)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        items = parse_request(args.request)
    except WorkflowParseError as exc:
        print(f"Could not read the request: {exc}", file=sys.stderr)
        return 2

    for line in format_plan(items, privacy, args.dry_run):
        print(line)

    # --dry-run and --yes both go straight through: one publishes nothing, the
    # other is the explicit "I have already decided" switch.
    if args.dry_run or args.yes:
        return _run_in_one_go(args, items, privacy, height, logger)

    planner = create_planner(
        items,
        cache_dir=args.cache_dir,
        metadata_path=args.metadata,
        privacy=privacy,
        target_height=height,
        upscale=args.upscale,
        # The picks are about to be shown to a person, so a pasted link is
        # worth one cheap metadata lookup: "https://youtu.be/xyz" tells the
        # approver nothing that the URL underneath it does not.
        peek_links=True,
        on_event=make_printer(args.verbose),
    )
    try:
        picks = planner.resolve()
    except YTAudioFilterError as exc:
        logger.error(str(exc))
        print(f"Could not resolve the picks: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted; nothing was rendered or published.", file=sys.stderr)
        return 130

    if not any(pick.producible for pick in picks):
        # Nothing to approve — every item was already ours, or none resolved.
        # Report the outcome rather than gate on a decision with no effect.
        summary = planner.produce(picks)
        for line in format_summary(summary):
            print(line)
        return summary.exit_code

    if sys.stdin.isatty():
        status, picks = _approval_loop(planner, picks)
        if status == APPROVED:
            return _produce(planner, picks, logger)
        if status == REJECTED:
            print("Rejected; nothing was rendered or published.")
            return 1
        # The terminal turned out not to be one. Leave a plan behind rather
        # than throw the resolved picks away — but do not print them twice.
        print("Nothing could be read from stdin, so this is not an interactive session.")
        return _await_approval(args, planner, picks, show=False)
    return _await_approval(args, planner, picks)


def _target_height(args: argparse.Namespace) -> int:
    """The height this run renders at.

    ``--upscale`` without an explicit height targets the floor rather than the
    default, because Real-ESRGAN is a 2x model: a 360p source becomes exactly
    720p. Asking for 1080p on top of that would add a second, interpolating
    scale over the reconstructed picture — paying for the GPU hour and then
    partly undoing what it bought.
    """
    if args.height is None and args.upscale:
        return workflow_runner.MIN_HEIGHT
    return clamp_height(args.height)


def _run_in_one_go(
    args: argparse.Namespace, items: Sequence[WorkItem], privacy: str, height: int, logger
) -> int:
    """``--dry-run`` and ``--yes``: resolve and finish without a gate."""
    try:
        summary = run_workflow(
            items,
            dry_run=args.dry_run,
            cache_dir=args.cache_dir,
            metadata_path=args.metadata,
            privacy=privacy,
            target_height=height,
            upscale=args.upscale,
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


def _await_approval(
    args: argparse.Namespace, planner, picks: List[PlannedPick], *, show: bool = True
) -> int:
    """No terminal to ask, so save the plan and stop having done nothing.

    ``show`` is off when the prompt already printed the picks, which happens
    when ``isatty()`` claimed a terminal that turned out to be at EOF.
    """
    if show:
        for line in format_picks(picks, heading="Picks — open each URL to check them:"):
            print(line)
    plan = planner.plan(picks, args.request)
    path = save_plan(plan, args.plan_file)
    print("Nothing has been downloaded, rendered, or published.")
    print(f"Plan saved to {path}")
    print("Approve it with:")
    print(f'  yt-studio --approve "{args.request}"')
    return EXIT_AWAITING_APPROVAL


def _produce(planner, picks: Sequence[PlannedPick], logger) -> int:
    try:
        summary = planner.produce(picks)
    except YTAudioFilterError as exc:  # pragma: no cover - per-pick errors are caught inside
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
