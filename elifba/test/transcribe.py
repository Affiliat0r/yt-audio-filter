#!/usr/bin/env python
"""Listen to the synthesised clips and report what was actually said.

The point of this file is that "the pronunciation is wrong" should be a
measurement, not an opinion. edge-tts is handed Arabic script and hands back
audio; nothing in that loop checks that the audio says what the card shows. So
run the audio back through speech recognition and print what comes out beside
what was asked for.

Whisper on a lone syllable is not reliable enough to assert against on its own
-- a bare "ba" out of context can come back as almost anything. What it *is*
reliable at is telling a syllable apart from a word: if the voice is reading
the letter's name instead of its sound, the transcript comes back as "باء" and
the duration is twice what a syllable needs. That is the failure this catches.

    python elifba/test/transcribe.py --role letters --limit 12
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ELIFBA = Path(__file__).resolve().parent.parent
VOICE_CACHE = ELIFBA / "voice"


def load_model(name: str, prefer_cuda: bool = True):
    """Load Whisper, falling back to CPU if CUDA cannot actually run.

    Constructing the model on "cuda" succeeds even when CTranslate2's CUDA
    libraries are missing -- the failure only surfaces on the first encode. So
    the probe below runs one, on a second of silence, and that is what decides.
    """
    import numpy as np
    from faster_whisper import WhisperModel

    if prefer_cuda:
        try:
            model = WhisperModel(name, device="cuda", compute_type="float16")
            list(model.transcribe(np.zeros(16_000, dtype=np.float32), beam_size=1)[0])
            return model, "cuda"
        except Exception as err:  # noqa: BLE001 - any CUDA failure means CPU
            print(f"(cuda unavailable, using cpu: {err})", file=sys.stderr)

    return WhisperModel(name, device="cpu", compute_type="int8"), "cpu"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=VOICE_CACHE)
    parser.add_argument("--role", default=None, help="letters | narration")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--model", default="medium")
    parser.add_argument("--cpu", action="store_true", help="skip the CUDA probe")
    parser.add_argument("--contains", default=None,
                        help="only clips whose asked-for text contains this")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    index_path = args.cache / "index.json"
    if not index_path.exists():
        print(f"no clip index at {index_path}; render something first", file=sys.stderr)
        return 1
    index = json.loads(io.open(index_path, encoding="utf-8").read())

    rows = [
        {"file": args.cache / name, **meta}
        for name, meta in index.items()
        if (args.role is None or meta.get("role") == args.role)
        and (args.contains is None or args.contains in meta["text"])
    ]
    rows.sort(key=lambda r: r["file"].name)
    if args.limit:
        rows = rows[: args.limit]

    model, device = load_model(args.model, prefer_cuda=not args.cpu)

    print(f"{len(rows)} clip(s), model={args.model}, device={device}")
    print()
    print(f"{'asked for':<14} {'role':<10} {'secs':>5}  {'lang':<4} heard")
    print("-" * 78)

    out = []
    for row in rows:
        segments, info = model.transcribe(str(row["file"]), beam_size=5)
        heard = " ".join(s.text.strip() for s in segments).strip()
        out.append(
            {
                "text": row["text"],
                "role": row.get("role"),
                "voice": row.get("voice"),
                "seconds": row["seconds"],
                "language": info.language,
                "heard": heard,
            }
        )
        print(
            f"{row['text']:<14} {str(row.get('role')):<10} {row['seconds']:>5.2f}  "
            f"{info.language:<4} {heard}"
        )

    if args.json_out:
        args.json_out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
