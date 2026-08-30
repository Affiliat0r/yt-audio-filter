"""Output quality: the 720p floor, and Real-ESRGAN sharpening.

Two separate promises, and they fail in different ways:

* **The floor.** Nothing this tool publishes is allowed below 720p. YouTube
  picks its encoding ladder from the uploaded resolution, so a 360p upload is
  given a bitrate that makes an already-soft source look worse again.
* **Sharpening.** ``--upscale`` runs Real-ESRGAN over the source so the extra
  pixels are reconstructed rather than interpolated. It needs a Vulkan GPU and
  it refuses long videos, so it is strictly best-effort: a render that cannot
  be sharpened must still be produced, plainly scaled, rather than failing.

Order matters and is the subtle part. Real-ESRGAN works frame by frame and
drops the audio track, so sharpening happens *before* music removal — Demucs
then works on a file that still has its original audio, and the existing remux
carries the result.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from yt_audio_filter import workflow_runner as wr


# --------------------------------------------------------------- the floor


def test_the_floor_is_720p() -> None:
    assert wr.MIN_HEIGHT == 720


@pytest.mark.parametrize("asked", [144, 240, 360, 480, 719])
def test_anything_under_the_floor_is_raised_to_it(asked: int) -> None:
    assert wr.clamp_height(asked) == 720


@pytest.mark.parametrize("asked", [720, 1080, 1440, 2160])
def test_a_height_at_or_above_the_floor_is_left_alone(asked: int) -> None:
    assert wr.clamp_height(asked) == asked


def test_no_height_means_the_default() -> None:
    assert wr.clamp_height(None) == wr.DEFAULT_HEIGHT


def test_the_default_clears_the_floor() -> None:
    """A default below the floor would make the floor unreachable by default."""
    assert wr.DEFAULT_HEIGHT >= wr.MIN_HEIGHT


def test_a_nonsense_height_is_refused() -> None:
    with pytest.raises(ValueError):
        wr.clamp_height(0)
    with pytest.raises(ValueError):
        wr.clamp_height(-1080)


# ---------------------------------------------------- sharpening the source


def _run(tmp_path: Path, **kwargs) -> wr._Run:
    settings = dict(
        dry_run=False,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "output",
        state_path=tmp_path / "state.json",
        metadata_path=tmp_path / "meta.json",
        privacy="private",
        on_event=None,
    )
    settings.update(kwargs)
    return wr._Run(**settings)


@pytest.fixture
def run(tmp_path: Path) -> wr._Run:
    return _run(tmp_path, target_height=720, upscale=True)


def test_sharpening_is_off_unless_asked_for(tmp_path: Path) -> None:
    assert _run(tmp_path).upscale is False


def test_the_sharpened_file_replaces_the_source(run: wr._Run, tmp_path: Path) -> None:
    source = tmp_path / "full_abc.mp4"
    source.write_bytes(b"\x00")
    sharp = tmp_path / "sharp_abc.mp4"
    sharp.write_bytes(b"\x00")

    with mock.patch(
        "yt_audio_filter.upscale.get_or_create_sharpened", return_value=sharp
    ) as sharpen:
        assert run.sharpen(source, "abc") == sharp

    assert sharpen.call_args.kwargs["video_id"] == "abc"


def test_a_video_too_long_to_sharpen_still_renders(run: wr._Run, tmp_path: Path) -> None:
    """The 10,000-frame refusal is the common case for a full episode.

    Falling back to a plain scale keeps the evening's output coming; failing
    the item would mean a 30-minute cartoon can never be produced at all.
    """
    from yt_audio_filter.exceptions import OverlayError

    source = tmp_path / "full_abc.mp4"
    source.write_bytes(b"\x00")

    with mock.patch(
        "yt_audio_filter.upscale.get_or_create_sharpened",
        side_effect=OverlayError("too long to upscale"),
    ):
        assert run.sharpen(source, "abc") is None


def test_a_missing_gpu_does_not_fail_the_item(run: wr._Run, tmp_path: Path) -> None:
    from yt_audio_filter.exceptions import PrerequisiteError

    source = tmp_path / "full_abc.mp4"
    source.write_bytes(b"\x00")

    with mock.patch(
        "yt_audio_filter.upscale.get_or_create_sharpened",
        side_effect=PrerequisiteError("realesrgan-ncnn-vulkan not found"),
    ):
        assert run.sharpen(source, "abc") is None


def test_the_fallback_is_announced(tmp_path: Path) -> None:
    """A silent fallback would let someone believe they got reconstructed
    detail when they got interpolation."""
    from yt_audio_filter.exceptions import OverlayError

    seen: list = []
    run = _run(tmp_path, upscale=True, on_event=lambda kind, msg, data: seen.append(kind))
    source = tmp_path / "full_abc.mp4"
    source.write_bytes(b"\x00")

    with mock.patch(
        "yt_audio_filter.upscale.get_or_create_sharpened",
        side_effect=OverlayError("too long to upscale"),
    ):
        run.sharpen(source, "abc")

    assert "upscale-skipped" in seen


# ------------------------------------------------- sharpening keeps the audio


def _fake_upscale(source, target, **kwargs):
    Path(target).write_bytes(b"\x00")
    return Path(target)


def _capturing_run(captured: dict):
    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"\x00" * 32)
        return mock.Mock(returncode=0, stderr="", stdout="")

    return fake_run


def test_sharpening_restores_the_audio_track(tmp_path: Path) -> None:
    """Real-ESRGAN reassembles from PNGs, so its output is silent.

    Music removal runs *after* this, and Demucs has nothing to separate if the
    audio was dropped on the way in.
    """
    from yt_audio_filter import upscale

    src = tmp_path / "src.mp4"
    src.write_bytes(b"\x00")
    captured: dict = {}

    with mock.patch.object(upscale, "upscale_video", side_effect=_fake_upscale), mock.patch(
        "subprocess.run", side_effect=_capturing_run(captured)
    ):
        upscale.upscale_preserving_audio(src, tmp_path / "dst.mp4")

    cmd = captured["cmd"]
    assert "-map" in cmd, "the mux has to name its streams explicitly"
    # Video from the upscaled file, audio from the original.
    assert "0:v:0" in cmd and any(str(a).startswith("1:a") for a in cmd)
    assert "copy" in cmd, "re-encoding the freshly upscaled video would undo it"


def test_a_silent_source_does_not_break_the_mux(tmp_path: Path) -> None:
    """Some sources genuinely have no audio stream; ffmpeg fails a hard map."""
    from yt_audio_filter import upscale

    src = tmp_path / "src.mp4"
    src.write_bytes(b"\x00")
    captured: dict = {}

    with mock.patch.object(upscale, "upscale_video", side_effect=_fake_upscale), mock.patch(
        "subprocess.run", side_effect=_capturing_run(captured)
    ):
        upscale.upscale_preserving_audio(src, tmp_path / "dst.mp4")

    assert "1:a?" in captured["cmd"], "the audio map must be optional"


def test_the_sharpened_file_is_cached_per_video(tmp_path: Path) -> None:
    """An hour of GPU per episode is not something to repeat on a retry."""
    from yt_audio_filter import upscale

    cache = tmp_path / "cache"
    cache.mkdir()
    cached = cache / "sharp_abc.mp4"
    cached.write_bytes(b"\x00" * 64)

    with mock.patch.object(upscale, "upscale_preserving_audio") as never:
        got = upscale.get_or_create_sharpened(
            tmp_path / "src.mp4", video_id="abc", cache_dir=cache
        )

    assert got == cached
    never.assert_not_called()


def test_the_sharpened_cache_does_not_collide_with_the_overlay_one(tmp_path: Path) -> None:
    """``upscaled_<id>.mp4`` is the *silent* visual the overlay pipeline wants.

    Handing that file to music removal would publish an episode with no sound.
    """
    from yt_audio_filter import upscale

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "upscaled_abc.mp4").write_bytes(b"\x00" * 64)

    with mock.patch.object(
        upscale, "upscale_preserving_audio", return_value=cache / "sharp_abc.mp4"
    ) as built:
        upscale.get_or_create_sharpened(tmp_path / "src.mp4", video_id="abc", cache_dir=cache)

    built.assert_called_once()


# --------------------------------------------------------- carried in the plan


def test_the_plan_remembers_whether_to_sharpen(tmp_path: Path) -> None:
    """Approval is given against what was shown; the render must not quietly
    differ from it."""
    plan = wr.WorkflowPlan(request="niloya", picks=[], target_height=720, upscale=True)
    path = wr.save_plan(plan, tmp_path / "plan.json")
    assert wr.load_plan(path).upscale is True


def test_a_plan_without_the_field_reads_as_off(tmp_path: Path) -> None:
    plan = wr.WorkflowPlan(request="niloya", picks=[])
    assert wr.load_plan(wr.save_plan(plan, tmp_path / "plan.json")).upscale is False


# ------------------------------------------------------------------- the CLI


def _cli_kwargs(argv: list) -> dict:
    """Run ``yt-studio`` far enough to see what the runner was asked for."""
    from yt_audio_filter import workflow_cli

    seen: dict = {}

    def capture(items, **kwargs):
        seen.update(kwargs)
        return wr.WorkflowSummary(dry_run=True)

    with mock.patch("yt_audio_filter.workflow_cli.run_workflow", side_effect=capture):
        workflow_cli.main(["niloya", "--dry-run", *argv])
    return seen


def test_by_default_the_cli_renders_at_1080p_without_sharpening() -> None:
    kwargs = _cli_kwargs([])
    assert kwargs["target_height"] == 1080
    assert kwargs["upscale"] is False


def test_upscale_targets_the_floor_because_the_model_is_2x() -> None:
    """360p doubled is exactly 720p.

    Asking for 1080p on top would scale the reconstructed picture a second
    time, interpolating away part of what the GPU hour just bought.
    """
    kwargs = _cli_kwargs(["--upscale"])
    assert kwargs["target_height"] == 720
    assert kwargs["upscale"] is True


def test_an_explicit_height_still_wins_over_the_upscale_default() -> None:
    assert _cli_kwargs(["--upscale", "--height", "1080"])["target_height"] == 1080


def test_the_cli_applies_the_floor() -> None:
    assert _cli_kwargs(["--height", "360"])["target_height"] == 720


def test_sharp_is_accepted_as_an_alias() -> None:
    assert _cli_kwargs(["--sharp"])["upscale"] is True
