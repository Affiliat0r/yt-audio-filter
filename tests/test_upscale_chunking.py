"""Upscaling a full episode, not just a short clip.

Real-ESRGAN works frame by frame through the filesystem: FFmpeg writes every
frame as PNG, the binary reads that directory and writes another, and FFmpeg
reassembles. For a 25-minute cartoon that is ~37,000 frames — tens of
gigabytes on disk at once. One such run ground for twenty hours, which is why
the pipeline used to refuse anything over 10,000 frames outright.

Refusing was the wrong fix for the right problem. The problem is *peak disk*,
not total work, and peak disk is bounded by splitting the source into chunks
and doing one at a time. Time is then the only real limit, and at roughly 14
fps a full episode is under an hour of GPU.

What these tests pin down is the orchestration — when a source is split, that
every chunk is processed, that they are rejoined in order, and that the rejoin
is a stream copy. The FFmpeg and Real-ESRGAN calls themselves are stubbed;
what matters here is that no frames are silently dropped or reordered, because
that desynchronises the audio that gets muxed back on afterwards.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from yt_audio_filter import upscale


# ----------------------------------------------------------------- budgets


def test_a_single_pass_still_has_a_frame_budget() -> None:
    """Peak disk within one pass is what this bounds."""
    assert upscale.MAX_UPSCALE_FRAMES == 10_000


def test_the_whole_video_budget_is_far_larger() -> None:
    """Chunking means length is a time cost, not a disk cost."""
    assert upscale.MAX_TOTAL_UPSCALE_FRAMES > upscale.MAX_UPSCALE_FRAMES * 10


def test_a_full_episode_fits_the_whole_video_budget() -> None:
    """25 minutes at 25 fps — the case that used to be refused."""
    assert 25 * 60 * 25 < upscale.MAX_TOTAL_UPSCALE_FRAMES


def test_chunks_are_short_enough_to_stay_inside_a_single_pass() -> None:
    """At 30 fps a chunk must not, on its own, blow the single-pass budget."""
    assert upscale.UPSCALE_CHUNK_SECONDS * 30 < upscale.MAX_UPSCALE_FRAMES


# ------------------------------------------------------------ orchestration


@pytest.fixture
def orchestration(tmp_path: Path):
    """``upscale_video`` with its three shell-out steps replaced by records."""

    def run(n_frames: int, n_chunks: int = 3):
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00")
        dst = tmp_path / "dst.mp4"
        calls: dict = {"single": [], "segment": 0, "concat": []}

        def fake_single(source, target, **kwargs):
            calls["single"].append((Path(source).name, Path(target).name))
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_bytes(b"\x00" * 16)
            return Path(target)

        def fake_segment(source, workdir, seconds):
            calls["segment"] += 1
            # The real one creates its own working directory.
            Path(workdir).mkdir(parents=True, exist_ok=True)
            made = []
            for i in range(n_chunks):
                chunk = Path(workdir) / f"chunk_{i:04d}.mp4"
                chunk.write_bytes(b"\x00")
                made.append(chunk)
            return made

        def fake_concat(segments, target):
            calls["concat"] = [Path(s).name for s in segments]
            Path(target).write_bytes(b"\x00" * 32)
            return Path(target)

        with mock.patch.object(upscale, "_probe_framerate", return_value=25.0), mock.patch.object(
            upscale, "_expected_frame_count", return_value=n_frames
        ), mock.patch.object(upscale, "ensure_ffmpeg_available"), mock.patch.object(
            upscale, "ensure_realesrgan_available"
        ), mock.patch.object(
            upscale, "_upscale_single_pass", side_effect=fake_single
        ), mock.patch.object(
            upscale, "_segment_video", side_effect=fake_segment
        ), mock.patch.object(
            upscale, "_concat_segments", side_effect=fake_concat
        ):
            upscale.upscale_video(src, dst)
        return calls

    return run


def test_a_short_video_is_done_in_one_pass(orchestration) -> None:
    """Splitting a clip would add two FFmpeg passes for nothing."""
    calls = orchestration(n_frames=5_000)
    assert calls["segment"] == 0
    assert len(calls["single"]) == 1
    assert not calls["concat"]


def test_a_long_video_is_split_and_every_chunk_is_processed(orchestration) -> None:
    calls = orchestration(n_frames=40_000, n_chunks=5)
    assert calls["segment"] == 1
    assert len(calls["single"]) == 5, "a skipped chunk is a gap in the finished video"


def test_the_chunks_are_rejoined_in_order(orchestration) -> None:
    """Out-of-order rejoining desyncs the audio muxed on afterwards."""
    calls = orchestration(n_frames=40_000, n_chunks=4)
    assert calls["concat"] == sorted(calls["concat"])
    assert len(calls["concat"]) == 4


def test_an_absurdly_long_video_is_still_refused(orchestration) -> None:
    """Chunking removes the disk wall, not the clock."""
    from yt_audio_filter.exceptions import OverlayError

    with pytest.raises(OverlayError, match="too long"):
        orchestration(n_frames=upscale.MAX_TOTAL_UPSCALE_FRAMES + 1)


def test_an_unknown_duration_does_not_block_the_upscale(orchestration) -> None:
    """``_expected_frame_count`` returns 0 when ffprobe will not say."""
    calls = orchestration(n_frames=0)
    assert len(calls["single"]) == 1


# ------------------------------------------------------------- the rejoining


def test_rejoining_copies_rather_than_re_encodes(tmp_path: Path) -> None:
    """Every chunk was just reconstructed frame by frame; a further lossy pass
    would spend the GPU hour and then throw away what it bought."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"\x00" * 32)
        return mock.Mock(returncode=0, stderr="", stdout="")

    segments = []
    for i in range(3):
        seg = tmp_path / f"seg_{i:04d}.mp4"
        seg.write_bytes(b"\x00")
        segments.append(seg)

    with mock.patch("subprocess.run", side_effect=fake_run):
        upscale._concat_segments(segments, tmp_path / "joined.mp4")

    assert "copy" in captured["cmd"]
    assert "concat" in captured["cmd"]


def test_the_concat_list_names_every_segment(tmp_path: Path) -> None:
    """A list written but never populated yields a silently truncated video."""
    written: dict = {}

    def fake_run(cmd, **kwargs):
        # The list file is the -i argument; read it before ffmpeg would.
        listfile = Path(cmd[cmd.index("-i") + 1])
        written["body"] = listfile.read_text(encoding="utf-8")
        Path(cmd[-1]).write_bytes(b"\x00" * 32)
        return mock.Mock(returncode=0, stderr="", stdout="")

    segments = []
    for i in range(3):
        seg = tmp_path / f"seg_{i:04d}.mp4"
        seg.write_bytes(b"\x00")
        segments.append(seg)

    with mock.patch("subprocess.run", side_effect=fake_run):
        upscale._concat_segments(segments, tmp_path / "joined.mp4")

    for seg in segments:
        assert seg.name in written["body"]


def test_segmenting_keeps_the_video_stream_untouched(tmp_path: Path) -> None:
    """Re-encoding on the way in would degrade the source before Real-ESRGAN
    ever sees it."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # The segment muxer would write these; stand in for it.
        for i in range(2):
            (tmp_path / "work" / f"chunk_{i:04d}.mp4").write_bytes(b"\x00")
        return mock.Mock(returncode=0, stderr="", stdout="")

    src = tmp_path / "src.mp4"
    src.write_bytes(b"\x00")
    workdir = tmp_path / "work"
    workdir.mkdir()

    with mock.patch("subprocess.run", side_effect=fake_run):
        made = upscale._segment_video(src, workdir, 60)

    assert "copy" in captured["cmd"]
    assert "segment" in captured["cmd"]
    assert len(made) == 2
