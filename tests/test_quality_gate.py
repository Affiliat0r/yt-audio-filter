"""The gate that decides whether a faster render is still the same picture.

A speedup is only a speedup if the output survives it, and "no quality loss"
has to be a number or it is an opinion. This scores a candidate against the
*current pipeline's output* — not against the 640x360 source, which every
candidate trivially differs from.

The verdict logic is what is tested here rather than the metrics themselves:
whether SSIM and VMAF are computed correctly is FFmpeg's problem, but whether
we correctly refuse a candidate that is the wrong size, has lost frames, or has
drifted out of sync is ours. Those three are the failure modes this pipeline
has actually produced.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from compare_quality import (  # noqa: E402
    SSIM_FLOOR,
    VMAF_FLOOR,
    verdict,
)


def _report(**overrides) -> dict:
    """A passing report, so each test changes exactly one thing."""
    report = {
        "candidate": {"width": 1280, "height": 720, "frames": 750},
        "reference": {"width": 1280, "height": 720, "frames": 750},
        "same_resolution": True,
        "same_frame_count": True,
        "av_drift": 0.02,
        "av_in_sync": True,
        "ssim": 0.995,
        "vmaf": 98.4,
    }
    report.update(overrides)
    return report


# ------------------------------------------------------------------ passing


def test_an_identical_render_passes() -> None:
    passed, reasons = verdict(_report())
    assert passed and not reasons


def test_encoder_noise_still_passes() -> None:
    """Two encodes of the same frames land around here; that is not a loss."""
    passed, _ = verdict(_report(ssim=0.985, vmaf=96.0))
    assert passed


# ------------------------------------------------------------------ failing


def test_a_softer_picture_is_refused() -> None:
    passed, reasons = verdict(_report(vmaf=88.0))
    assert not passed
    assert any("VMAF" in r for r in reasons)


def test_a_structural_change_is_refused() -> None:
    passed, reasons = verdict(_report(ssim=0.90))
    assert not passed
    assert any("SSIM" in r for r in reasons)


def test_the_wrong_resolution_is_refused() -> None:
    """A candidate that quietly rendered 960x540 is not a faster 720p."""
    passed, reasons = verdict(
        _report(same_resolution=False, candidate={"width": 960, "height": 540, "frames": 750})
    )
    assert not passed
    assert any("resolution" in r for r in reasons)


def test_a_lost_frame_is_refused() -> None:
    """Frame count must survive split -> upscale -> concat exactly, or the
    audio muxed back on afterwards no longer lines up."""
    passed, reasons = verdict(
        _report(same_frame_count=False, candidate={"width": 1280, "height": 720, "frames": 749})
    )
    assert not passed
    assert any("frame count" in r for r in reasons)


def test_drifting_audio_is_refused() -> None:
    passed, reasons = verdict(_report(av_drift=0.9, av_in_sync=False))
    assert not passed
    assert any("drift" in r for r in reasons)


def test_every_failure_is_reported_not_just_the_first() -> None:
    """A candidate that is wrong in three ways should say so in one run,
    rather than being fixed and re-run three times."""
    passed, reasons = verdict(
        _report(
            same_frame_count=False,
            candidate={"width": 1280, "height": 720, "frames": 700},
            ssim=0.5,
            vmaf=40.0,
        )
    )
    assert not passed
    assert len(reasons) >= 3


# ------------------------------------------------------- absent measurements


def test_a_missing_vmaf_does_not_fail_the_gate() -> None:
    """Not every FFmpeg build has libvmaf. SSIM alone still catches the gross
    failures, so absence must not read as a regression."""
    passed, _ = verdict(_report(vmaf=None))
    assert passed


def test_unscored_metrics_do_not_silently_pass_a_broken_candidate() -> None:
    """When the frames do not line up the metrics are not computed at all —
    the size and count checks still have to fail it."""
    passed, reasons = verdict(
        _report(
            same_frame_count=False,
            candidate={"width": 1280, "height": 720, "frames": 12},
            ssim=None,
            vmaf=None,
        )
    )
    assert not passed
    assert reasons


# -------------------------------------------------------------- the floors


def test_the_floors_are_where_a_viewer_would_notice() -> None:
    """Documented so a future change to them is deliberate rather than drift."""
    assert VMAF_FLOOR == 95.0
    assert SSIM_FLOOR == 0.98


# --------------------------------- PSNR, because VMAF lies in this comparison


def test_psnr_catches_what_vmaf_rewards() -> None:
    """The failure mode this content is prone to.

    An over-sharpened candidate amplifies the source's compression mottling into
    something VMAF scores as detail: measured at VMAF 92.4 while SSIM fell to
    0.888. Sorted by VMAF, SSIM moved the opposite way. PSNR is unforgiving of
    exactly that, so it is the tie-breaker.
    """
    from compare_quality import PSNR_FLOOR

    passed, reasons = verdict(_report(vmaf=92.4, ssim=0.888, psnr=26.7))
    assert not passed
    assert any("PSNR" in r for r in reasons)
    assert PSNR_FLOOR == 45.0


def test_a_clean_but_different_upscale_is_still_refused() -> None:
    """lanczos+unsharp lands near 0.96 SSIM / 35 dB against the ESRGAN
    reference — clean, but a different picture. The gate is "same", not
    "arguably similar"; trading that away is the user's call, not the gate's."""
    passed, reasons = verdict(_report(ssim=0.959, psnr=35.0, vmaf=75.4))
    assert not passed
    assert len(reasons) >= 2


def test_a_missing_psnr_does_not_fail_the_gate() -> None:
    passed, _ = verdict(_report(psnr=None))
    assert passed
