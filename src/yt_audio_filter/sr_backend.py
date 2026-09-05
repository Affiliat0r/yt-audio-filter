"""Super-resolution backends: the same weights, run three different ways.

Measured end to end on a real 87,737-frame episode, not extrapolated from a
clip — an RTX 3070 Ti Laptop:

    ncnn binary + PNG round-trip    90.0 min
    TensorRT fp16 + rawvideo pipe   50.3 min     1.79x   (29.1 fps sustained)

Shorter runs flatter this badly and consistently. The same code measured 33-42
fps over 9,000 frames and 65-80 fps over 750, because a laptop card sinks
deeper into thermal throttling the longer it works: 4 minutes of load is not 50
minutes of load. Every projection from a clip overstated the episode by 15-40%.
Quote the 50.3 min figure; the others are burst rates.

Two independent things produce that, and it is worth keeping them apart:

* **Tensor cores.** ``realesrgan-ncnn-vulkan`` runs on Vulkan shader ALUs. The
  model needs 285.3 GFLOP per 640x360 frame, and on shader ALUs the card's
  whole theoretical peak is 42.0 TFLOP/s — so the ncnn path was asking for
  ~99% of peak and got the 27% that is normal for it. TensorRT reaches 54.3%
  of *tensor-core* peak instead, which is 2.5x what PyTorch manages on the
  identical graph.
* **Deleting the file round-trip.** The ncnn binary has no stdin/stdout: input
  and output are paths, always. That forced 110 GB of PNG per episode and cost
  12.7 min on its own — enough that even an infinitely fast upscaler could not
  have reached a 10-minute target through it.

The weights are **not** a different model. They are parsed straight out of the
shipped ``realesr-animevideov3-x2.param``/``.bin``, which is why the output
measures VMAF 97.80 / SSIM 0.99604 / PSNR 50.34 dB against the ncnn binary's
own frames. Nothing was downloaded, and no ``.pth`` is involved.

Selection degrades rather than fails. The worker runs on several machines and
one is a light install with no CUDA: TensorRT, then Torch, then the ncnn
binary, then no sharpening at all — and the last of those still publishes an
episode, plainly scaled.
"""

from __future__ import annotations

import re
import struct
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from .logger import get_logger

logger = get_logger()

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO_ROOT / "tools" / "realesrgan" / "models"
MODEL_NAME = "realesr-animevideov3-x2"

#: One frame at a time.
#:
#: Batching was the obvious hypothesis and the measurements killed it:
#: TensorRT is flat at 118.4 / 118.8 / 118.7 / 116.9 fps for batch 1/2/4/8, and
#: PyTorch actively degrades (46.6 -> 30.7 -> 22.2 at batch 4/8/16) once the
#: ~470 MB of activations fall out of cache. A single 640x360 frame already
#: saturates this GPU, so batch 1 buys the same throughput with no tail-batch
#: handling and a smaller footprint.
DEFAULT_BATCH = 1

#: Backends in the order they are preferred.
BACKENDS = ("tensorrt", "torch", "ncnn")

_NCNN_BINARY = REPO_ROOT / "tools" / "realesrgan" / "realesrgan-ncnn-vulkan.exe"

# ncnn .bin per-layer weight tags.
_TAG_FP32 = 0x0002C056
_TAG_FP16 = 0x01306B47


# ---------------------------------------------------------------------------
# What this machine can do
# ---------------------------------------------------------------------------


def _have_tensorrt() -> bool:
    try:
        import tensorrt  # noqa: F401

        return _have_torch_cuda()
    except Exception:  # noqa: BLE001 - absence is the normal case
        return False


def _have_torch_cuda() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - the light worker has no torch
        return False


def _have_ncnn() -> bool:
    return _NCNN_BINARY.exists()


def select_backend(prefer: Optional[str] = None) -> Optional[str]:
    """The fastest backend this machine can actually run, or None.

    ``prefer`` pins a choice — useful to A/B a regression, or to hold a machine
    on the old path. A preference the machine cannot honour falls back rather
    than failing, because the alternative is refusing to render at all; but an
    *unknown* name raises, since a typo would otherwise quietly spend an hour
    on the wrong backend.
    """
    if prefer is not None:
        if prefer not in BACKENDS:
            raise ValueError(
                f"Unknown super-resolution backend {prefer!r}. "
                f"Known: {', '.join(BACKENDS)}"
            )
        checks = {"tensorrt": _have_tensorrt, "torch": _have_torch_cuda, "ncnn": _have_ncnn}
        if checks[prefer]():
            return prefer
        logger.warning(f"Backend {prefer!r} was asked for but is unavailable; falling back.")

    if _have_tensorrt():
        return "tensorrt"
    if _have_torch_cuda():
        return "torch"
    if _have_ncnn():
        return "ncnn"
    return None


def gpu_identity() -> Tuple[str, str]:
    """(gpu name, driver version) — what a built engine is only valid for."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30,
        )
        name, driver = [p.strip() for p in result.stdout.strip().splitlines()[0].split(",")]
        return name, driver
    except Exception:  # noqa: BLE001 - only used to name a cache file
        return "unknown-gpu", "unknown-driver"


def engine_path(cache_dir: Path, gpu: str, driver: str, batch: int = DEFAULT_BATCH) -> Path:
    """Where the built TensorRT plan for this exact machine lives.

    A plan is compiled for one GPU architecture and one driver and is invalid
    on any other — loading the wrong one crashes or, worse, does not. Building
    costs ~35 s, so it is cached, and the filename carries everything that
    makes it valid.
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{gpu}-{driver}").strip("-").lower()
    return Path(cache_dir) / f"{MODEL_NAME}-{slug}-b{batch}.plan"


# ---------------------------------------------------------------------------
# The weights, read out of the files the ncnn binary already uses
# ---------------------------------------------------------------------------


def parse_param(path: Path) -> List[tuple]:
    """(op_type, name, attrs) per layer of an ncnn ``.param``."""
    lines = Path(path).read_text(encoding="utf-8").strip().splitlines()
    layers = []
    for line in lines[2:]:  # skip the magic number and the layer/blob counts
        parts = line.split()
        if not parts:
            continue
        op, name = parts[0], parts[1]
        n_in, n_out = int(parts[2]), int(parts[3])
        attrs = {}
        for token in parts[4 + n_in + n_out:]:
            key, value = token.split("=", 1)
            attrs[key] = value
        layers.append((op, name, attrs))
    return layers


def load_ncnn_weights(param_path: Path, bin_path: Path) -> List[tuple]:
    """Walk the ``.param`` in order, consuming the ``.bin``.

    The final length check is the whole reason to trust this: ncnn's format
    carries no per-layer offsets, so the only proof that every layer was read
    at the right position is that the file comes out exactly empty. For
    ``realesr-animevideov3-x2`` that is 1,247,368 bytes. A short or long read
    raises rather than building a subtly wrong network.
    """
    import numpy as np

    blob = Path(bin_path).read_bytes()
    offset = 0
    weights: List[tuple] = []

    for op, name, attrs in parse_param(param_path):
        if op == "Convolution":
            num_out = int(attrs["0"])
            count = int(attrs["6"])
            has_bias = int(attrs.get("5", "0"))

            (tag,) = struct.unpack_from("<I", blob, offset)
            offset += 4
            if tag == _TAG_FP16:
                array = np.frombuffer(blob, dtype="<f2", count=count, offset=offset)
                array = array.astype(np.float32)
                offset += count * 2
            elif tag in (_TAG_FP32, 0):
                array = np.frombuffer(blob, dtype="<f4", count=count, offset=offset).copy()
                offset += count * 4
            else:
                raise ValueError(f"{name}: unknown weight tag 0x{tag:08X}")

            bias = None
            if has_bias:
                bias = np.frombuffer(blob, dtype="<f4", count=num_out, offset=offset).copy()
                offset += num_out * 4

            num_in = count // (num_out * 9)
            weights.append(("conv", name, array.reshape(num_out, num_in, 3, 3), bias))

        elif op == "PReLU":
            n = int(attrs["0"])
            slope = np.frombuffer(blob, dtype="<f4", count=n, offset=offset).copy()
            offset += n * 4
            weights.append(("prelu", name, slope, None))

    if offset != len(blob):
        raise ValueError(
            f"{Path(bin_path).name}: consumed {offset} of {len(blob)} bytes. "
            "The parse is only trustworthy when the file comes out exactly empty."
        )
    return weights


def build_model(model_dir: Path = MODEL_DIR, name: str = MODEL_NAME, out_scale: int = 2):
    """The shipped model, rebuilt in PyTorch from the ncnn weights.

    Architecture read straight off the ``.param`` — this is SRVGGNetCompact::

        conv(3->64) + PReLU
        16 x [ conv(64->64) + PReLU ]
        conv(64->48)
        PixelShuffle(4)
        + nearest-upsample(input, 4x)
        bicubic-downsample(0.5)          <- only for the x2 variant

    So the "x2" model is the x4 network with a bicubic halving stapled on the
    end, exactly as ncnn runs it. Reproducing that tail rather than training a
    real x2 is what keeps the output equivalent.
    """
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class AnimeVideoV3(nn.Module):
        def __init__(self, num_conv: int = 16, num_feat: int = 64, scale: int = 2):
            super().__init__()
            self.scale = scale
            body: list = [nn.Conv2d(3, num_feat, 3, 1, 1), nn.PReLU(num_feat)]
            for _ in range(num_conv):
                body += [nn.Conv2d(num_feat, num_feat, 3, 1, 1), nn.PReLU(num_feat)]
            body += [nn.Conv2d(num_feat, 3 * 16, 3, 1, 1)]
            self.body = nn.Sequential(*body)
            self.shuffle = nn.PixelShuffle(4)

        def forward(self, x):
            out = self.shuffle(self.body(x))
            out = out + F.interpolate(x, scale_factor=4, mode="nearest")
            if self.scale == 2:
                out = F.interpolate(out, scale_factor=0.5, mode="bicubic", align_corners=False)
            return out

    model_dir = Path(model_dir)
    weights = load_ncnn_weights(model_dir / f"{name}.param", model_dir / f"{name}.bin")
    net = AnimeVideoV3(scale=out_scale)

    convs = [m for m in net.body if isinstance(m, nn.Conv2d)]
    prelus = [m for m in net.body if isinstance(m, nn.PReLU)]
    conv_i = prelu_i = 0
    with torch.no_grad():
        for kind, layer_name, array, bias in weights:
            if kind == "conv":
                conv = convs[conv_i]
                if tuple(conv.weight.shape) != array.shape:
                    raise ValueError(
                        f"{layer_name}: shape {array.shape} does not fit {tuple(conv.weight.shape)}"
                    )
                conv.weight.copy_(torch.from_numpy(array))
                conv.bias.copy_(torch.from_numpy(bias))
                conv_i += 1
            else:
                prelus[prelu_i].weight.copy_(torch.from_numpy(array))
                prelu_i += 1
    if (conv_i, prelu_i) != (len(convs), len(prelus)):
        raise ValueError(
            f"weights did not fill the network: {conv_i}/{len(convs)} convs, "
            f"{prelu_i}/{len(prelus)} prelus"
        )
    net.eval()
    return net


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


def _export_onnx_fp16(net, width: int, height: int) -> bytes:
    """The model as a strongly-typed fp16 ONNX graph.

    TensorRT 11 removed the FP16 builder flag: precision now comes from the
    graph itself. So the export must already be fp16 rather than asking the
    builder to downcast, or the engine silently builds in fp32 and loses most
    of the tensor-core advantage.
    """
    import io

    import torch

    net = net.cuda().half().eval()
    dummy = torch.zeros(1, 3, height, width, device="cuda", dtype=torch.float16)
    buffer = io.BytesIO()
    with torch.no_grad():
        torch.onnx.export(
            net, dummy, buffer,
            input_names=["x"], output_names=["y"],
            dynamic_axes={"x": {0: "batch"}, "y": {0: "batch"}},
            opset_version=17,
        )
    return buffer.getvalue()


def build_engine(path: Path, width: int, height: int, batch: int = DEFAULT_BATCH) -> Path:
    """Compile a TensorRT plan for this machine and cache it at ``path``.

    Takes ~35 s and is worth doing once: the plan is tied to this GPU and
    driver, which is why :func:`engine_path` puts both in the filename.
    """
    import tensorrt as trt

    # Named apart from this module's own ``logger``: shadowing it sent
    # logger.info() to TensorRT's logger, which has no such method, and the
    # engine build died after doing all its work.
    trt_logger = trt.Logger(trt.Logger.ERROR)
    builder = trt.Builder(trt_logger)
    # Explicit batch is the default in TRT 10+; STRONGLY_TYPED makes the graph's
    # own fp16 authoritative.
    network = builder.create_network(
        1 << int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED)
    )
    parser = trt.OnnxParser(network, trt_logger)
    onnx_bytes = _export_onnx_fp16(build_model(), width, height)
    if not parser.parse(onnx_bytes):
        errors = "; ".join(str(parser.get_error(i)) for i in range(parser.num_errors))
        raise RuntimeError(f"TensorRT could not parse the model: {errors}")

    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 3 << 30)
    profile = builder.create_optimization_profile()
    profile.set_shape("x", (1, 3, height, width), (batch, 3, height, width),
                      (batch, 3, height, width))
    config.add_optimization_profile(profile)

    plan = builder.build_serialized_network(network, config)
    if plan is None:
        raise RuntimeError("TensorRT returned no engine")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plan)
    logger.info(f"Built TensorRT engine -> {path.name}")
    return path


class Upscaler:
    """Runs the model on batches of RGB frames already on the GPU.

    Constructed through :func:`make_upscaler`, which picks the backend and
    degrades on its own if the fast one will not start.
    """

    def __init__(self, backend: str, width: int, height: int, cache_dir: Path,
                 batch: int = DEFAULT_BATCH, scale: int = 2) -> None:
        import torch

        self.backend = backend
        self.batch = batch
        self.scale = scale
        self._torch = torch

        if backend == "tensorrt":
            import tensorrt as trt

            gpu, driver = gpu_identity()
            path = engine_path(cache_dir, gpu, driver, batch)
            # The plan is also tied to the input resolution baked into the
            # profile, so a different source size needs its own file.
            path = path.with_name(path.stem + f"-{width}x{height}.plan")
            if not path.exists():
                build_engine(path, width, height, batch)
            runtime = trt.Runtime(trt.Logger(trt.Logger.ERROR))
            self.engine = runtime.deserialize_cuda_engine(path.read_bytes())
            self.context = self.engine.create_execution_context()
            self.context.set_input_shape("x", (batch, 3, height, width))
            self.d_in = torch.empty(batch, 3, height, width, device="cuda",
                                    dtype=torch.float16)
            self.d_out = torch.empty(tuple(self.context.get_tensor_shape("y")),
                                     device="cuda", dtype=torch.float16)
            self.context.set_tensor_address("x", self.d_in.data_ptr())
            self.context.set_tensor_address("y", self.d_out.data_ptr())
        else:
            torch.backends.cudnn.benchmark = True
            self.net = (
                build_model(out_scale=scale)
                .cuda().half().eval()
                .to(memory_format=torch.channels_last)
            )

    def __call__(self, frames):
        """frames: (N, H, W, 3) uint8 on cuda -> (N, 2H, 2W, 3) uint8 on cuda."""
        torch = self._torch
        n = frames.shape[0]
        x = frames.permute(0, 3, 1, 2).to(torch.float16).div_(255.0)
        if self.backend == "tensorrt":
            self.d_in[:n].copy_(x)
            self.context.execute_async_v3(torch.cuda.current_stream().cuda_stream)
            y = self.d_out[:n]
        else:
            x = x.contiguous(memory_format=torch.channels_last)
            with torch.no_grad():
                y = self.net(x)
        y = y.clamp(0, 1).mul_(255).round_().to(torch.uint8)
        return y.permute(0, 2, 3, 1).contiguous()


def make_upscaler(width: int, height: int, cache_dir: Path,
                  prefer: Optional[str] = None, batch: int = DEFAULT_BATCH):
    """An :class:`Upscaler`, or None when this machine has no GPU path.

    Degrades on a real failure, not just on absence: an engine that will not
    build or a driver that will not initialise falls through to the next
    backend rather than failing the episode.
    """
    chosen = select_backend(prefer)
    if chosen not in ("tensorrt", "torch"):
        # "ncnn" or None: the caller keeps its existing file-based path.
        return None

    # Try the chosen backend, then everything slower than it.
    order = ["tensorrt", "torch"][["tensorrt", "torch"].index(chosen):]
    for backend in order:
        try:
            return Upscaler(backend, width, height, Path(cache_dir), batch=batch)
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning(f"{backend} upscaler unavailable ({exc}); trying the next backend.")
    return None


# ---------------------------------------------------------------------------
# Temporal reuse: not running the network on a frame that did not change
# ---------------------------------------------------------------------------

#: Mean absolute difference, 0-255, under which two frames are the same cel.
#:
#: Animation is drawn on twos or threes, so the same picture is held for two or
#: three video frames. Measured on a real episode, 62% of consecutive pairs sit
#: under 1.0 — they are not bit-identical only because h264 re-quantises each
#: frame, so an equality test would find almost nothing.
REUSE_THRESHOLD = 1.0

#: Mean absolute difference within any 8x8 block, over which the frames differ
#: however small the whole-frame mean is.
#:
#: This is the guard that makes the mean safe to use. A 5x5 highlight moving
#: across a 640x360 frame shifts the frame mean by 0.028/255 — invisible to a
#: mean test — while being exactly the motion the viewer is watching. Blinking
#: eyes and moving mouths are that shape. Pooling first localises the change so
#: a small fast object cannot hide inside a large still frame.
REUSE_BLOCK_THRESHOLD = 8.0

#: Off, and measured rather than merely cautious — this lead was killed by its
#: own numbers.
#:
#: It looked like the one remaining route to a 10-minute render: an earlier
#: measurement put 62% of consecutive frame pairs under 1.0/255. That figure
#: does not survive a change detector that is actually safe. Measured over
#: 6,000 frames of a real episode, reuse against block threshold:
#:
#:      4      5.8%      47.8 min
#:      8      9.3%      46.3 min      <- REUSE_BLOCK_THRESHOLD
#:     16     12.6%      44.9 min
#:     32     16.9%      43.0 min
#:     64     25.7%      39.2 min
#:   mean only 32.2%     36.4 min      <- unsafe; drops small fast motion
#:
#: Against 50.3 min today, and 10 min asked for. Worse, the comparison itself
#: costs 2.6 ms/frame on CPU — about 3.8 min over 87,737 frames, which at the
#: safe setting cancels the ~4 min it saves. Net zero.
#:
#: Kept because the tests encode *why* the cheap version is unsafe, and because
#: on a source with longer holds the arithmetic could differ. Do not switch it
#: on expecting a win without re-measuring the reuse rate for that source.
REUSE_DEFAULT = False

_BLOCK = 8


def frames_match(previous, current) -> bool:
    """Whether ``current`` is the same drawing as ``previous``.

    Two tests, because either alone has a blind spot. The whole-frame mean
    catches broad change and is cheap; the block maximum catches a small region
    changing a lot, which the mean averages away to nothing.

    ``None`` for ``previous`` — the first frame of a render — is never a match.
    """
    import numpy as np

    if previous is None or current is None:
        return False
    if previous.shape != current.shape:
        return False

    diff = np.abs(previous.astype(np.int16) - current.astype(np.int16))
    if diff.mean() >= REUSE_THRESHOLD:
        return False

    # Mean-pool into 8x8 blocks and take the worst one. Trim the ragged edge
    # rather than padding it, since a partial block would dilute its own mean
    # and could hide a change sitting against the frame border.
    height, width = diff.shape[0] // _BLOCK * _BLOCK, diff.shape[1] // _BLOCK * _BLOCK
    if height == 0 or width == 0:
        return True
    trimmed = diff[:height, :width].astype(np.float32)
    blocks = trimmed.reshape(height // _BLOCK, _BLOCK, width // _BLOCK, _BLOCK, -1)
    return float(blocks.mean(axis=(1, 3, 4)).max()) < REUSE_BLOCK_THRESHOLD
