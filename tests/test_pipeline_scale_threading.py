"""The scale option must survive every path through the pipeline.

Regression: `scale_height` was added to `process_video`'s signature and
forwarded at both `remux_video` call sites — but one of those calls lives in
`_process_single_chunk`, a different function that never received the
parameter. Long videos take the chunked path, so every music-removal render of
a long video died with `NameError: name 'scale_height' is not defined`.

Nothing caught it: the unit tests exercised `build_remux_command` in isolation,
and no test called into the chunk path at all. These do.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_audio_filter import pipeline


@pytest.fixture
def stub_stages(tmp_path):
    """Neutralise the heavy stages; we only care about argument plumbing."""
    with patch.object(pipeline, "extract_audio"), patch.object(
        pipeline, "isolate_vocals"
    ), patch.object(pipeline, "remux_video") as remux:
        yield remux


def test_single_chunk_forwards_scale_height(stub_stages, tmp_path) -> None:
    """This is the exact call that raised NameError in production."""
    pipeline._process_single_chunk(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        device="cpu",
        model_name="htdemucs",
        audio_bitrate="192k",
        segment=None,
        shifts=1,
        watermark=False,
        fp16=False,
        compile_model=False,
        scale_height=720,
    )
    assert stub_stages.call_args.kwargs["scale_height"] == 720


def test_single_chunk_defaults_to_no_scaling(stub_stages, tmp_path) -> None:
    """Omitting it must keep the lossless copy path, not crash."""
    pipeline._process_single_chunk(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        device="cpu",
        model_name="htdemucs",
        audio_bitrate="192k",
        segment=None,
        shifts=1,
        watermark=False,
        fp16=False,
        compile_model=False,
    )
    assert stub_stages.call_args.kwargs["scale_height"] is None


def test_parallel_worker_tuple_arity_matches_what_it_unpacks() -> None:
    """The parallel path passes a positional tuple, so a mismatch between what
    is packed and what is unpacked is a runtime ValueError that no type checker
    catches. Pin the contract from both ends.
    """
    import ast

    tree = ast.parse(inspect.getsource(pipeline))
    worker = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_process_chunk_worker"
    )
    unpack = next(
        node for node in ast.walk(worker)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Tuple)
        and isinstance(node.value, ast.Name)
        and node.value.id == "args"
    )
    unpacked = [el.id for el in unpack.targets[0].elts]

    annotation = inspect.signature(pipeline._process_chunk_worker).parameters["args"].annotation
    declared = len(getattr(annotation, "__args__", ()))

    assert len(unpacked) == declared, (
        f"worker unpacks {len(unpacked)} values but its Tuple annotation "
        f"declares {declared}"
    )
    assert "scale_height" in unpacked

    # And the producer must pack exactly that many. It lives in
    # _process_video_chunked, which is the whole reason this test exists: the
    # option was threaded through process_video and missed the chunked path.
    packed = next(
        node.args[0] for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and node.args
        and isinstance(node.args[0], ast.Tuple)
    )
    assert len(packed.elts) == len(unpacked), (
        f"process_video packs {len(packed.elts)} values but the worker unpacks "
        f"{len(unpacked)}"
    )


def test_process_video_exposes_the_option() -> None:
    params = inspect.signature(pipeline.process_video).parameters
    assert "scale_height" in params
    assert params["scale_height"].default is None


def test_no_function_reads_scale_height_without_binding_it() -> None:
    """Catch the whole family of bugs, not the two instances found by hand.

    `scale_height` was threaded into `process_video` and forwarded at the call
    sites — but the intermediate functions never gained the parameter, so the
    name was simply unbound at runtime. Python happily compiles that; it only
    explodes when a long video takes the chunked path.

    Reading a name a function never binds is a NameError waiting for the right
    input, so assert it for every function in the module rather than trusting
    that the two known cases were the only ones.
    """
    import ast

    tree = ast.parse(inspect.getsource(pipeline))
    offenders = []

    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        bound = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        bound.add(tgt.id)
                    elif isinstance(tgt, ast.Tuple):
                        bound |= {e.id for e in tgt.elts if isinstance(e, ast.Name)}
        reads = {
            n.id for n in ast.walk(fn)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        if "scale_height" in reads and "scale_height" not in bound:
            offenders.append(f"{fn.name} (line {fn.lineno})")

    assert not offenders, (
        "these read scale_height without binding it, which is a NameError the "
        f"moment they run: {', '.join(offenders)}"
    )
