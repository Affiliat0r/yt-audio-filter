"""Not running the network on a frame that has not changed.

Animation is drawn "on twos" or "on threes": the same picture is held for two
or three video frames. Measured on a real episode, 62% of consecutive frame
pairs differ by less than 1.0/255 on average — buried in codec noise rather
than exactly equal, because h264 re-quantises each frame slightly.

Inference is 86% of the render, so skipping it on a held frame and re-emitting
the previous upscaled result is the largest remaining lever. It is also the
only one that does not touch the picture: reusing the output for an input that
did not change is not an approximation, it is the same answer.

The threshold is the whole risk. Too tight and nothing is skipped; too loose
and a real cut is held over, which shows as a dropped frame — far more visible
than any softness. So the decision is tested here directly, and the difference
metric has to behave like a metric: symmetric, zero on identical input, and
growing with the size of the change.
"""

from __future__ import annotations

import numpy as np
import pytest

from yt_audio_filter import sr_backend


# ------------------------------------------------------------ the threshold


def test_the_threshold_is_below_the_codec_noise_floor() -> None:
    """Measured: 62% of pairs sit under 1.0/255, which is compression noise on
    a held frame rather than motion. The threshold has to sit inside that
    band, not above it."""
    assert 0 < sr_backend.REUSE_THRESHOLD <= 1.0


def test_reuse_is_off_by_default_until_it_is_asked_for() -> None:
    """It changes output timing in a way the quality gate must approve first,
    so it is opt-in rather than silently on."""
    assert sr_backend.REUSE_DEFAULT is False


# --------------------------------------------------------- the decision itself


@pytest.mark.parametrize("value", [0, 7, 128, 255])
def test_an_identical_frame_is_always_reused(value: int) -> None:
    frame = np.full((8, 8, 3), value, dtype=np.uint8)
    assert sr_backend.frames_match(frame, frame.copy())


def test_a_frame_that_changed_everywhere_is_not_reused() -> None:
    a = np.zeros((8, 8, 3), dtype=np.uint8)
    b = np.full((8, 8, 3), 255, dtype=np.uint8)
    assert not sr_backend.frames_match(a, b)


def test_codec_noise_on_a_held_frame_is_reused() -> None:
    """A held cel comes back from h264 with sub-1/255 jitter. Refusing to reuse
    it would leave the whole lever on the table."""
    rng = np.random.default_rng(0)
    a = np.full((64, 64, 3), 100, dtype=np.uint8)
    noise = rng.integers(0, 2, size=a.shape, endpoint=False, dtype=np.uint8)
    assert sr_backend.frames_match(a, (a + noise).astype(np.uint8))


def test_a_cut_is_never_reused() -> None:
    """Holding a frame across a scene change drops a frame visibly — the one
    failure this must not have."""
    rng = np.random.default_rng(1)
    a = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
    b = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
    assert not sr_backend.frames_match(a, b)


def test_a_small_moving_object_is_not_reused() -> None:
    """The hard case: a mouth or an eye moves while 99% of the frame is held.
    Mean difference is small, so a mean-only test would wrongly reuse it."""
    a = np.full((100, 100, 3), 50, dtype=np.uint8)
    b = a.copy()
    b[45:55, 45:55] = 255  # 1% of the frame, but a real change
    assert not sr_backend.frames_match(a, b)


def test_the_comparison_is_symmetric() -> None:
    rng = np.random.default_rng(2)
    a = rng.integers(0, 256, size=(32, 32, 3), dtype=np.uint8)
    b = a.copy()
    b[0, 0] = 255 - b[0, 0]
    assert sr_backend.frames_match(a, b) == sr_backend.frames_match(b, a)


def test_no_previous_frame_means_no_reuse() -> None:
    """The first frame of a render has nothing to reuse."""
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    assert not sr_backend.frames_match(None, frame)


# ------------------------------------------------------------- the arithmetic


def test_the_saving_is_bounded_by_how_much_is_inference() -> None:
    """Profiled: inference is 86% of the render. So even skipping *every*
    repeated frame cannot beat that share, and the honest ceiling has to be
    computed from it rather than from the skip rate alone.

    At the measured 62% reuse rate the network runs on 38% of frames:
        0.86 * 0.38 + 0.14 = 0.467 of the current time.
    """
    inference_share, reuse_rate = 0.86, 0.62
    remaining = inference_share * (1 - reuse_rate) + (1 - inference_share)
    assert 0.45 < remaining < 0.48
    # 50.3 min today -> ~23 min. Real, and still not the 10 min asked for.
    assert 20 < 50.3 * remaining < 25


def test_a_small_fast_object_is_not_reused() -> None:
    """The failure a mean-only test cannot see.

    A 5x5 highlight moving across a 640x360 frame changes 0.01% of the pixels,
    so the mean difference is 0.028/255 — far under any sane threshold — while
    being exactly the motion the viewer is watching. Blinking eyes and moving
    mouths are this shape. Reuse must look at where the change is, not only at
    how much of it there is.
    """
    a = np.full((360, 640, 3), 90, dtype=np.uint8)
    b = a.copy()
    b[180:185, 320:325] = 255

    mean_only = np.abs(a.astype(int) - b.astype(int)).mean()
    assert mean_only < 0.05, "the premise: the mean really is tiny here"
    assert not sr_backend.frames_match(a, b)


def test_a_single_stray_pixel_still_reuses() -> None:
    """One pixel of ringing is codec noise, not motion; refusing on it would
    switch reuse off for most of an episode."""
    a = np.full((360, 640, 3), 90, dtype=np.uint8)
    b = a.copy()
    b[7, 11] = 255
    assert sr_backend.frames_match(a, b)
