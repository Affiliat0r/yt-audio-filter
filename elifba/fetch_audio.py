"""Download every recording the curriculum refers to, into elifba/audio/.

    python -m elifba.fetch_audio [--unit N ...]

Idempotent: files already on disk are skipped. A recording the site does not
have (the site itself points ``ب`` in "Harfler ve Şekilleri" at a ``btn_1000``
that does not exist) is listed at the end rather than failing the run; the
renderer shows such a tile silently.

The audio is Diyanet's and is not committed (see .gitignore); this script is
how a fresh checkout gets it back.
"""

from __future__ import annotations

import argparse
import http.client
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional, Tuple

from elifba.curriculum import SOUND_ROOT, build, iter_sounds, sound_path

AUDIO_DIR = Path(__file__).resolve().parent / "audio"
MISSING_FILE = AUDIO_DIR / "missing.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) elifba-video-builder"


def fetch(rel: str) -> Tuple[str, Optional[str]]:
    """Download one file; returns (rel, error or None)."""
    dst = AUDIO_DIR / rel
    if dst.exists() and dst.stat().st_size > 0:
        return rel, None
    dst.parent.mkdir(parents=True, exist_ok=True)
    url = f"{SOUND_ROOT}/{rel}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if not data:
                raise OSError("empty response")
            tmp = dst.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(dst)
            return rel, None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return rel, "404"
            err = f"HTTP {exc.code}"
        except (OSError, http.client.HTTPException) as exc:  # resets, truncated bodies
            err = type(exc).__name__
        time.sleep(1.5 * (attempt + 1))
    return rel, err


def wanted(units: Optional[List[int]] = None) -> List[str]:
    paths: List[str] = []
    for unit in build():
        if units and unit["number"] not in units:
            continue
        for les in unit["lessons"]:
            paths += [sound_path(les, name) for name in iter_sounds(les)]
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--unit", type=int, action="append", help="only these unit numbers")
    args = parser.parse_args()

    paths = wanted(args.unit)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(fetch, paths))
    missing = sorted(rel for rel, err in results if err == "404")
    failed = sorted(f"{rel} ({err})" for rel, err in results if err and err != "404")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    known = set(json.loads(MISSING_FILE.read_text())) if MISSING_FILE.exists() else set()
    MISSING_FILE.write_text(json.dumps(sorted(known | set(missing)), indent=1))
    print(f"{len(paths)} recordings, {len(missing)} not on the site, {len(failed)} failed")
    for line in missing + failed:
        print("  ", line)


if __name__ == "__main__":
    main()
