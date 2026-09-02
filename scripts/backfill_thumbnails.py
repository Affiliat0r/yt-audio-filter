"""Give already-published videos the thumbnail of the source they came from.

New uploads carry it across automatically (``uploader.apply_source_thumbnail``,
called from ``upload_to_youtube``). This is the one-off pass for everything
published before that existed, which YouTube gave an auto-generated thumbnail —
usually a frame from the middle of the episode, which for a cartoon is an
unreadable smear of motion.

Dry run by default; ``--apply`` is what actually changes anything::

    python scripts/backfill_thumbnails.py
    python scripts/backfill_thumbnails.py --apply

**YouTube rate-limits thumbnails per channel, and it does not forgive.**
Around ten set back to back earns a ``429 uploadRateLimitExceeded``. The window
is undocumented and turns out to be long: after a run that retried every one to
eight minutes, the channel was still refusing three days later. Retrying
appears to extend the lockout.

So this pass **stops at the first 429** rather than backing off, paces itself
between videos (``--delay``), and records each success to ``--state`` as it
lands. Rerun it a day later and it resumes where it stopped.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yt_audio_filter import uploader  # noqa: E402

class RateLimited(Exception):
    """YouTube is refusing thumbnail uploads for this channel right now."""


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Republish each upload's source thumbnail onto it.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually set the thumbnails. Without this, nothing is changed.",
    )
    parser.add_argument(
        "--skip-custom",
        action="store_true",
        help=(
            "Skip videos that already have a maxres thumbnail, which is a good "
            "proxy for 'a custom one was already set'."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Only touch the first N videos (0 = all).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help=(
            "Pause between sets. YouTube caps how many thumbnails a channel may "
            "set in a short window, so going slowly is what gets a full pass "
            "through in one go."
        ),
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("state") / "thumbnail_backfill.json",
        help="Record of what has already been set, so a rerun resumes.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache") / "thumbnails",
        help="Where fetched thumbnails are written.",
    )
    return parser.parse_args(argv)


def recoverable_sources() -> dict:
    """Every ``source_id -> {uploaded_id, title}`` this machine can prove.

    Two independent records, because neither is complete on its own:

    * **The channel's descriptions.** ``uploader.get_uploaded_source_ids``
      reads back an ``Original: <url>`` line. Authoritative and visible from
      any machine — but only present on uploads that wrote it.
    * **``state/workflow_sources.json``.** Written by the runner the moment a
      render is published, so it covers part of the gap above. Local to this
      machine, which is why it cannot replace the first.
    """
    from yt_audio_filter import workflow_runner

    found = dict(uploader.get_uploaded_source_ids(force_refresh=True))
    for entry in workflow_runner.load_state().sources:
        if not entry.uploaded_video_id:
            continue
        # The channel is the better record where both exist: it reflects what
        # is actually published rather than what this machine last did.
        found.setdefault(
            entry.source_id,
            {"uploaded_id": entry.uploaded_video_id, "title": entry.request},
        )
    return found


def load_done(path: Path) -> set:
    try:
        return set(json.loads(Path(path).read_text(encoding="utf-8")).get("done", []))
    except Exception:  # noqa: BLE001 - no record yet, or a damaged one
        return set()


def save_done(path: Path, done: set) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"done": sorted(done)}, indent=2), encoding="utf-8")


def is_rate_limit(exc: Exception) -> bool:
    text = str(exc)
    return "429" in text or "uploadRateLimitExceeded" in text


def set_one(youtube, uploaded_id: str, source_id: str, cache_dir: Path) -> bool:
    """Set one thumbnail. Raises :class:`RateLimited` if YouTube says no.

    **Do not add a retry loop here.** An earlier version backed off and retried
    for up to fifteen minutes per video. It set nothing, and the channel was
    still refusing three days later — YouTube appears to extend the lockout
    when it keeps being asked, so persistence made the situation worse rather
    than better. On a 429 the only useful move is to stop and come back much
    later.
    """
    try:
        return uploader.apply_source_thumbnail(
            youtube, uploaded_id, source_id, cache_dir, strict=True
        )
    except Exception as exc:  # noqa: BLE001 - classified for the caller
        if is_rate_limit(exc):
            raise RateLimited(str(exc)) from exc
        print(f"     {str(exc)[:140]}", flush=True)
        return False


def has_maxres(youtube, video_id: str) -> bool:
    """Whether YouTube holds a 1280x720 thumbnail for this video.

    Auto-generated thumbnails for a 360p source top out below maxres, so its
    presence is a decent signal that a custom one was uploaded. Not proof —
    hence it only ever gates a deliberate ``--skip-custom``.
    """
    try:
        items = youtube.videos().list(part="snippet", id=video_id).execute()["items"]
        return "maxres" in (items[0]["snippet"].get("thumbnails") or {})
    except Exception:  # noqa: BLE001 - an unknown answer means "do the work"
        return False


def main(argv=None) -> int:
    args = parse_args(argv)
    youtube = uploader.authenticate_youtube()

    published = recoverable_sources()
    if not published:
        print("No uploads with a recoverable source video were found.")
        return 1

    rows = sorted(published.items(), key=lambda kv: str(kv[1].get("title", "")))
    if args.limit:
        rows = rows[: args.limit]

    already = load_done(args.state)
    pending = [r for r in rows if (r[1].get("uploaded_id") or "") not in already]

    print(f"{len(rows)} upload(s) with a known source; {len(already)} already done.")
    if not args.apply:
        print("Dry run - nothing will be changed. Re-run with --apply.")
    print()

    done = skipped = failed = 0
    for index, (source_id, record) in enumerate(pending):
        uploaded_id = record.get("uploaded_id") or record.get("video_id") or ""
        title = str(record.get("title", ""))[:52]
        if not uploaded_id:
            print(f"  ?  {title:<52}  no uploaded id recorded")
            failed += 1
            continue

        if args.skip_custom and has_maxres(youtube, uploaded_id):
            print(f"  =  {title:<52}  already has a custom thumbnail")
            skipped += 1
            continue

        if not args.apply:
            print(f"  >  {title:<52}  {source_id} -> {uploaded_id}")
            continue

        if index and args.delay:
            time.sleep(args.delay)

        try:
            ok = set_one(youtube, uploaded_id, source_id, args.cache_dir)
        except RateLimited:
            # Stop the whole pass, not just this video. Continuing would keep
            # asking a channel that has already said no, which is what turned a
            # short refusal into a multi-day one.
            print(
                f"\nYouTube is rate-limiting thumbnail uploads on this channel.\n"
                f"{done} set this pass; {len(pending) - index} still to do.\n"
                "Stop here and rerun tomorrow - retrying sooner extends the block.",
                flush=True,
            )
            return 2

        if ok:
            print(f"  OK {title:<52}  {source_id} -> {uploaded_id}", flush=True)
            done += 1
            already.add(uploaded_id)
            # Written per video, not at the end: a pass interrupted halfway
            # must not redo the half it finished.
            save_done(args.state, already)
        else:
            print(f"  !! {title:<52}  {source_id} -> {uploaded_id}", flush=True)
            failed += 1

    if args.apply:
        print(f"\n{done} set, {skipped} skipped, {failed} failed.")
        if failed:
            print(f"Rerun the same command to resume; {len(already)} recorded as done.")
    # A failure here is cosmetic, but a non-zero exit lets a rerun be scripted.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
