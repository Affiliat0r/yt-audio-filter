"""Unit tests for yt_audio_filter.pair_state."""

import json
from pathlib import Path

from yt_audio_filter.pair_state import PairState, load_state, save_state


def test_missing_file_returns_empty_state(tmp_path: Path) -> None:
    state = load_state(tmp_path / "nope.json")
    assert state.pairs == []


def test_roundtrip_save_load(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    s1 = PairState()
    s1.add("aud1", "vid1", uploaded_video_id="yt-abc", output_path="out/1.mp4")
    s1.add("aud2", "vid2")
    save_state(s1, path)
    s2 = load_state(path)
    assert len(s2.pairs) == 2
    assert s2.pairs[0].audio_id == "aud1"
    assert s2.pairs[0].uploaded_video_id == "yt-abc"
    assert s2.pairs[1].uploaded_video_id is None


def test_contains(tmp_path: Path) -> None:
    s = PairState()
    s.add("a", "b")
    assert s.contains("a", "b") is True
    assert s.contains("a", "c") is False
    assert s.contains("c", "b") is False


def test_corrupt_json_returns_empty_state(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("not valid json", encoding="utf-8")
    state = load_state(path)
    assert state.pairs == []


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "sub" / "dir" / "state.json"
    s = PairState()
    s.add("x", "y")
    save_state(s, nested)
    assert nested.exists()
    loaded = json.loads(nested.read_text(encoding="utf-8"))
    assert loaded["pairs"][0]["audio_id"] == "x"
