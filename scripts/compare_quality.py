"""Score one render against another, so "no quality loss" is a number.

Any change to the sharpening path has to prove it did not degrade the picture,
and "looks the same to me" is not evidence — the differences that matter here
are exactly the ones that survive a glance: softened line art, smeared flat
areas, ringing around subtitles.

Three metrics, because they fail differently — and in this particular
comparison one of them lies.

**SSIM is the authoritative one here.** It is structural, so it notices when a
candidate stops being the same picture.

**VMAF is reported but must not be trusted alone against an ESRGAN reference.**
Measured on this content: VMAF does not penalise *excess* sharpness, so a
candidate that cranks unsharp closes the acutance gap and scores 92.4 while its
SSIM falls to 0.888 — visibly haloed and blotchy, and 78% larger at the same
CQ. Meanwhile a clean lanczos scores 75 on VMAF at SSIM 0.959. Sorted by VMAF,
SSIM moves the *opposite* way. VMAF also cannot separate any sharpened
candidate from plain stretching (all score 99.4-100 against a bicubic
baseline), while it rates real ESRGAN at 92.3 against that same baseline.

**PSNR is the tie-breaker**, because it is unforgiving of exactly the
amplified-noise failure VMAF rewards.

A candidate has to hold all three. Where they disagree, SSIM and PSNR win.

Comparing against the *current pipeline's output* rather than the source is
deliberate. The source is 640x360; every candidate "loses" to it in the trivial
sense of not being it. What we actually need to know is whether a faster path
lands in the same place the slow path did.

    python scripts/compare_quality.py candidate.mp4 --reference current.mp4
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

from pathlib import Path
from typing import Optional

#: Below this, a viewer would see the difference.
#:
#: Calibrated on this machine rather than assumed, because the scale is not the
#: intuitive one. Measured on the 750-frame bench clip, FFmpeg 8.0.1:
#:
#:   file against itself          VMAF 98.2   (not 100 — the default model is
#:                                            trained for 1080p viewing, so
#:                                            360p content caps below the top)
#:   round-tripped through 320p   VMAF 84.2
#:
#: So 95 sits about three points under the identical case and eleven above a
#: clearly visible loss. Treat those three points as the whole budget: a
#: candidate scoring 96 has already given up a third of it.
VMAF_FLOOR = 95.0

#: SSIM is far less forgiving of scale/alignment errors, which is what we want
#: it for. Two encodes of the same frames sit at ~0.99.
SSIM_FLOOR = 0.98

#: dB. Two encodes of the same frames sit near 50; a clean but genuinely
#: different upscale lands around 35, and an over-sharpened one around 26.
#: 45 keeps the gate on "same picture" rather than "arguably similar".
PSNR_FLOOR = 45.0


class ComparisonError(RuntimeError):
    """The two videos could not be compared."""


def _run(cmd: list, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )


def probe(path: Path) -> dict:
    """Resolution, frame count and durations — the cheap checks, run first."""
    video = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-count_frames", "-show_entries",
        "stream=width,height,nb_read_frames,duration",
        "-of", "json", str(path),
    ])
    if video.returncode != 0:
        raise ComparisonError(f"ffprobe failed on {path}: {video.stderr[-300:]}")
    stream = (json.loads(video.stdout).get("streams") or [{}])[0]

    audio = _run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=duration", "-of", "json", str(path),
    ])
    audio_streams = json.loads(audio.stdout).get("streams") or [] if audio.returncode == 0 else []

    def _float(value) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "frames": int(stream.get("nb_read_frames") or 0),
        "video_duration": _float(stream.get("duration")),
        "audio_duration": _float(audio_streams[0].get("duration")) if audio_streams else None,
    }


def score_ssim(candidate: Path, reference: Path) -> float:
    result = _run([
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(candidate), "-i", str(reference),
        "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-",
    ])
    match = re.search(r"All:([0-9.]+)", result.stderr)
    if not match:
        raise ComparisonError(f"No SSIM in ffmpeg output: {result.stderr[-400:]}")
    return float(match.group(1))


def score_psnr(candidate: Path, reference: Path) -> float:
    """PSNR in dB — the tie-breaker when SSIM and VMAF disagree.

    Included because VMAF rewards the one failure mode this content is prone
    to: amplifying the source's compression mottling into "detail". PSNR does
    not. On this material a clean candidate sits near 35 dB against the ESRGAN
    reference and an over-sharpened one falls to 26 dB, while VMAF ranks the
    over-sharpened one higher.
    """
    result = _run([
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(candidate), "-i", str(reference),
        "-lavfi", "[0:v][1:v]psnr", "-f", "null", "-",
    ])
    match = re.search(r"average:([0-9.]+)", result.stderr)
    if not match:
        raise ComparisonError(f"No PSNR in ffmpeg output: {result.stderr[-400:]}")
    return float(match.group(1))


def has_libvmaf() -> bool:
    """Whether this FFmpeg was built with libvmaf at all."""
    result = _run(["ffmpeg", "-hide_banner", "-filters"], timeout=60)
    return "libvmaf" in result.stdout


def score_vmaf(candidate: Path, reference: Path) -> Optional[float]:
    """VMAF, or None when this FFmpeg has no libvmaf.

    Deliberately *not* written through ``log_path``. A Windows path starts
    ``C:``, and a colon separates options inside an FFmpeg filter description —
    so passing one silently breaks the filter, VMAF comes back as None, and the
    gate then skips the perceptual check while still reporting PASS. Parsing the
    pooled score off stderr has no such trap.

    A build without libvmaf returns None and the gate proceeds on SSIM alone.
    A build *with* libvmaf that fails to produce a score raises, because that is
    a broken measurement rather than an absent capability, and silently
    downgrading the gate is how a regression ships.
    """
    result = _run([
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(candidate), "-i", str(reference),
        "-lavfi", "[0:v][1:v]libvmaf",
        "-f", "null", "-",
    ])
    match = re.search(r"VMAF score:\s*([0-9.]+)", result.stderr)
    if match:
        return float(match.group(1))
    if has_libvmaf():
        raise ComparisonError(
            "libvmaf is available but produced no score — refusing to report a "
            f"pass without it. FFmpeg said: {result.stderr[-400:]}"
        )
    return None


def compare(candidate: Path, reference: Path) -> dict:
    """Every number the quality gate needs, in one dict."""
    cand, ref = probe(candidate), probe(reference)
    report = {
        "candidate": cand,
        "reference": ref,
        "same_resolution": (cand["width"], cand["height"]) == (ref["width"], ref["height"]),
        "same_frame_count": cand["frames"] == ref["frames"],
        "ssim": None,
        "psnr": None,
        "vmaf": None,
    }
    drift = None
    if cand["video_duration"] is not None and cand["audio_duration"] is not None:
        drift = abs(cand["video_duration"] - cand["audio_duration"])
    report["av_drift"] = drift
    report["av_in_sync"] = drift is None or drift <= 0.1

    # Only worth scoring once the frames line up; SSIM on mismatched counts
    # compares different pictures and reports a meaningless number.
    if report["same_resolution"] and report["same_frame_count"]:
        report["ssim"] = score_ssim(candidate, reference)
        report["psnr"] = score_psnr(candidate, reference)
        report["vmaf"] = score_vmaf(candidate, reference)
    return report


def verdict(report: dict) -> tuple:
    """(passed, reasons) — every failure, not just the first."""
    reasons = []
    if not report["same_resolution"]:
        reasons.append(
            f"resolution differs: {report['candidate']['width']}x{report['candidate']['height']}"
            f" vs {report['reference']['width']}x{report['reference']['height']}"
        )
    if not report["same_frame_count"]:
        reasons.append(
            f"frame count differs: {report['candidate']['frames']} vs "
            f"{report['reference']['frames']}"
        )
    if not report["av_in_sync"]:
        reasons.append(f"audio drifts from video by {report['av_drift']:.3f}s")
    if report["ssim"] is not None and report["ssim"] < SSIM_FLOOR:
        reasons.append(f"SSIM {report['ssim']:.4f} below {SSIM_FLOOR}")
    if report.get("psnr") is not None and report["psnr"] < PSNR_FLOOR:
        reasons.append(f"PSNR {report['psnr']:.1f}dB below {PSNR_FLOOR}")
    if report["vmaf"] is not None and report["vmaf"] < VMAF_FLOOR:
        reasons.append(f"VMAF {report['vmaf']:.2f} below {VMAF_FLOOR}")
    return (not reasons, reasons)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    for path in (args.candidate, args.reference):
        if not path.exists():
            print(f"Not found: {path}", file=sys.stderr)
            return 2

    report = compare(args.candidate, args.reference)
    passed, reasons = verdict(report)
    report["passed"] = passed
    report["reasons"] = reasons

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if passed else 1

    cand, ref = report["candidate"], report["reference"]
    print(f"candidate : {cand['width']}x{cand['height']}  {cand['frames']} frames")
    print(f"reference : {ref['width']}x{ref['height']}  {ref['frames']} frames")
    if report["av_drift"] is not None:
        print(f"a/v drift : {report['av_drift']:.3f}s")
    print(f"SSIM      : {report['ssim'] if report['ssim'] is not None else 'not scored'}")
    print(f"PSNR      : {report.get('psnr') if report.get('psnr') is not None else 'not scored'}")
    print(f"VMAF      : {report['vmaf'] if report['vmaf'] is not None else 'unavailable'}")
    print()
    print("PASS" if passed else "FAIL: " + "; ".join(reasons))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
