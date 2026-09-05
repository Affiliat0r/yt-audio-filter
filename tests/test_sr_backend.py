"""Choosing how to upscale, and never failing an episode over it.

Measured end to end on a real 87,737-frame episode, RTX 3070 Ti Laptop:

    ncnn + PNG round-trip   90.0 min
    TensorRT fp16 + pipe    50.3 min      (1.79x, VMAF 97.80 / SSIM 0.99604)

Frame count survives exactly: 87,737 in, 87,737 out at 1280x720.

The win is not a faster model — it is the same weights, parsed out of the very
same ncnn ``.param``/``.bin`` — it is (a) tensor cores instead of Vulkan shader
ALUs and (b) deleting a 110 GB round-trip through PNG files that cost 12.7 min
on its own.

That makes backend selection load-bearing, and it has to degrade rather than
fail: the worker runs on several machines, one of them a light install with no
CUDA at all. The order is TensorRT, then Torch, then the ncnn binary, then no
sharpening — and a machine that has none of them must still publish an episode.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from yt_audio_filter import sr_backend


# ------------------------------------------------------------- availability


def test_selection_prefers_tensorrt() -> None:
    """2.5x faster than Torch on the same weights (118.8 vs 46.6 fps)."""
    with mock.patch.object(sr_backend, "_have_tensorrt", return_value=True), \
         mock.patch.object(sr_backend, "_have_torch_cuda", return_value=True):
        assert sr_backend.select_backend() == "tensorrt"


def test_torch_is_used_when_tensorrt_is_missing() -> None:
    with mock.patch.object(sr_backend, "_have_tensorrt", return_value=False), \
         mock.patch.object(sr_backend, "_have_torch_cuda", return_value=True):
        assert sr_backend.select_backend() == "torch"


def test_it_falls_back_to_the_ncnn_binary() -> None:
    with mock.patch.object(sr_backend, "_have_tensorrt", return_value=False), \
         mock.patch.object(sr_backend, "_have_torch_cuda", return_value=False), \
         mock.patch.object(sr_backend, "_have_ncnn", return_value=True):
        assert sr_backend.select_backend() == "ncnn"


def test_a_machine_with_nothing_reports_none_rather_than_raising() -> None:
    """The light worker has no CUDA. It must still produce an episode, plainly
    scaled, rather than failing the item."""
    with mock.patch.object(sr_backend, "_have_tensorrt", return_value=False), \
         mock.patch.object(sr_backend, "_have_torch_cuda", return_value=False), \
         mock.patch.object(sr_backend, "_have_ncnn", return_value=False):
        assert sr_backend.select_backend() is None


def test_an_explicit_choice_overrides_detection() -> None:
    """So a slow machine can be pinned, and so a regression can be A/B'd."""
    with mock.patch.object(sr_backend, "_have_tensorrt", return_value=True):
        assert sr_backend.select_backend(prefer="ncnn") == "ncnn"


def test_an_unknown_preference_is_refused_loudly() -> None:
    """Silently ignoring a typo would render 90 minutes on the wrong backend."""
    with pytest.raises(ValueError, match="Unknown"):
        sr_backend.select_backend(prefer="tensorrtx")


def test_a_preference_the_machine_cannot_honour_falls_back(caplog) -> None:
    """Pinning TensorRT on a laptop without it should still render."""
    with mock.patch.object(sr_backend, "_have_tensorrt", return_value=False), \
         mock.patch.object(sr_backend, "_have_torch_cuda", return_value=False), \
         mock.patch.object(sr_backend, "_have_ncnn", return_value=True):
        assert sr_backend.select_backend(prefer="tensorrt") == "ncnn"


# ------------------------------------------------------------ engine cache


def test_the_engine_is_cached_per_gpu_and_driver(tmp_path: Path) -> None:
    """A TensorRT plan is built for one GPU and driver and is invalid on
    another, so the filename has to say which. Rebuilding costs 35 s; loading
    the wrong one costs a wrong render or a crash."""
    a = sr_backend.engine_path(tmp_path, gpu="NVIDIA GeForce RTX 3070 Ti Laptop GPU",
                               driver="591.59", batch=4)
    b = sr_backend.engine_path(tmp_path, gpu="NVIDIA GeForce RTX 4090",
                               driver="591.59", batch=4)
    c = sr_backend.engine_path(tmp_path, gpu="NVIDIA GeForce RTX 3070 Ti Laptop GPU",
                               driver="600.00", batch=4)
    assert a != b and a != c and b != c
    assert a.suffix == ".plan"


def test_the_engine_name_survives_an_awkward_gpu_string(tmp_path: Path) -> None:
    """GPU names carry spaces and slashes; the path must stay a single file."""
    p = sr_backend.engine_path(tmp_path, gpu="NVIDIA A100-SXM4/40GB", driver="1.2",
                               batch=4)
    assert p.parent == tmp_path
    assert "/" not in p.name and " " not in p.name


def test_the_batch_size_is_part_of_the_engine_identity(tmp_path: Path) -> None:
    """The engine is built for a fixed input shape."""
    assert sr_backend.engine_path(tmp_path, gpu="g", driver="d", batch=1) != \
           sr_backend.engine_path(tmp_path, gpu="g", driver="d", batch=4)


# -------------------------------------------------------------- the weights


def test_the_model_is_the_one_already_shipping() -> None:
    """Not a lookalike downloaded from elsewhere: the exact ncnn weights.

    Measured equivalence to the ncnn binary's own output was VMAF 97.80,
    SSIM 0.99604, PSNR 50.34 dB — that only holds because these are the same
    numbers, so the loader must keep pointing at the shipped files.
    """
    assert sr_backend.MODEL_NAME == "realesr-animevideov3-x2"
    assert sr_backend.MODEL_DIR.name == "models"


def test_a_truncated_weight_file_is_refused(tmp_path: Path) -> None:
    """The parse is only trustworthy because the .bin is consumed to the byte;
    a short read must raise rather than silently build a wrong net."""
    param = tmp_path / "m.param"
    param.write_text(
        "7767517\n2 2\nConvolution conv0 1 1 in out 0=64 5=1 6=1728\n",
        encoding="utf-8",
    )
    (tmp_path / "m.bin").write_bytes(b"\x00" * 8)
    with pytest.raises(Exception):
        sr_backend.load_ncnn_weights(param, tmp_path / "m.bin")


# --------------------------------------------------------------- batch size


def test_the_batch_size_is_one_because_batching_does_not_help() -> None:
    """Batching was the obvious hypothesis and the numbers killed it twice.

    Inference alone is flat on TensorRT: 118.4 / 118.8 / 118.7 / 116.9 fps at
    batch 1/2/4/8 — one 640x360 frame already saturates this GPU. End to end,
    through the real pipe, larger batches are actively worse: 79.9 / 82.8 /
    72.0 / 54.6 fps at 1/2/4/8. Batch 2's 3.6% is inside the run-to-run spread
    and would buy a tail-batch to handle, so 1 it is."""
    assert sr_backend.DEFAULT_BATCH == 1


# ------------------------------------------------------- degrading, not dying


def test_a_tensorrt_that_will_not_build_falls_through_to_torch(tmp_path) -> None:
    """A driver mismatch or a failed engine build must not fail the episode."""
    attempted = []

    class Boom(Exception):
        pass

    def fake_upscaler(backend, *a, **kw):
        attempted.append(backend)
        if backend == "tensorrt":
            raise Boom("engine build failed")
        return f"{backend}-upscaler"

    with mock.patch.object(sr_backend, "select_backend", return_value="tensorrt"), \
         mock.patch.object(sr_backend, "Upscaler", side_effect=fake_upscaler):
        got = sr_backend.make_upscaler(640, 360, tmp_path)

    assert attempted == ["tensorrt", "torch"]
    assert got == "torch-upscaler"


def test_the_ncnn_machine_gets_no_streaming_upscaler(tmp_path) -> None:
    """ncnn keeps the existing file-based path; None says so."""
    with mock.patch.object(sr_backend, "select_backend", return_value="ncnn"):
        assert sr_backend.make_upscaler(640, 360, tmp_path) is None


def test_a_machine_with_nothing_gets_none(tmp_path) -> None:
    with mock.patch.object(sr_backend, "select_backend", return_value=None):
        assert sr_backend.make_upscaler(640, 360, tmp_path) is None


def test_every_backend_failing_is_not_an_exception(tmp_path) -> None:
    """The caller then plainly scales, which still publishes an episode."""
    with mock.patch.object(sr_backend, "select_backend", return_value="tensorrt"), \
         mock.patch.object(sr_backend, "Upscaler", side_effect=RuntimeError("no cuda")):
        assert sr_backend.make_upscaler(640, 360, tmp_path) is None
