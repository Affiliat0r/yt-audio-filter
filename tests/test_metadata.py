"""Unit tests for yt_audio_filter.metadata."""

import json
from pathlib import Path

import pytest

from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.metadata import (
    DEFAULT_LOGO_POSITION,
    DEFAULT_PRIVACY,
    apply_cli_overrides,
    load_metadata,
)


def _write_json(tmp_path: Path, data: dict, name: str = "meta.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_load_minimal_metadata(tmp_path: Path) -> None:
    meta_path = _write_json(tmp_path, {"title": "Hello"})
    meta = load_metadata(meta_path)
    assert meta.title == "Hello"
    assert meta.description == ""
    assert meta.tags == []
    assert meta.category_id == "22"
    assert meta.privacy_status == DEFAULT_PRIVACY
    assert meta.logo_path is None
    assert meta.logo_position == DEFAULT_LOGO_POSITION


def test_load_renders_description_template(tmp_path: Path) -> None:
    meta_path = _write_json(
        tmp_path,
        {
            "title": "T",
            "description_template": "Hi $name, from $place!",
            "description_vars": {"name": "World", "place": "Earth"},
        },
    )
    meta = load_metadata(meta_path)
    assert meta.description == "Hi World, from Earth!"


def test_load_template_missing_var_raises(tmp_path: Path) -> None:
    meta_path = _write_json(
        tmp_path,
        {
            "title": "T",
            "description_template": "Missing $who here",
            "description_vars": {},
        },
    )
    with pytest.raises(OverlayError, match="who"):
        load_metadata(meta_path)


def test_load_literal_braces_allowed_in_template(tmp_path: Path) -> None:
    meta_path = _write_json(
        tmp_path,
        {
            "title": "T",
            "description_template": "JSON example: {\"key\": \"$v\"}",
            "description_vars": {"v": "value"},
        },
    )
    meta = load_metadata(meta_path)
    assert meta.description == 'JSON example: {"key": "value"}'


def test_load_rejects_invalid_privacy(tmp_path: Path) -> None:
    meta_path = _write_json(tmp_path, {"title": "T", "privacy_status": "bogus"})
    with pytest.raises(OverlayError, match="privacy_status"):
        load_metadata(meta_path)


def test_load_rejects_invalid_logo_position(tmp_path: Path) -> None:
    meta_path = _write_json(tmp_path, {"title": "T", "logo_position": "middle"})
    with pytest.raises(OverlayError, match="logo_position"):
        load_metadata(meta_path)


def test_load_rejects_non_string_tags(tmp_path: Path) -> None:
    meta_path = _write_json(tmp_path, {"title": "T", "tags": ["ok", 1, "also ok"]})
    with pytest.raises(OverlayError, match="tags"):
        load_metadata(meta_path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(OverlayError, match="not found"):
        load_metadata(tmp_path / "does-not-exist.json")


def test_invalid_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not valid json {", encoding="utf-8")
    with pytest.raises(OverlayError, match="Invalid JSON"):
        load_metadata(p)


def test_relative_logo_path_resolved_against_metadata_dir(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    logo = sub / "logo.png"
    logo.write_bytes(b"fake-png")
    meta_path = _write_json(sub, {"title": "T", "logo_path": "logo.png"})
    meta = load_metadata(meta_path)
    assert meta.logo_path is not None
    assert meta.logo_path.resolve() == logo.resolve()


def test_absolute_logo_path_preserved(tmp_path: Path) -> None:
    logo = tmp_path / "brand.png"
    logo.write_bytes(b"fake")
    meta_path = _write_json(tmp_path, {"title": "T", "logo_path": str(logo.resolve())})
    meta = load_metadata(meta_path)
    assert meta.logo_path == logo.resolve()


def test_cli_overrides_logo(tmp_path: Path) -> None:
    meta_path = _write_json(tmp_path, {"title": "T", "logo_position": "top-left"})
    meta = load_metadata(meta_path)
    override_logo = tmp_path / "override.png"
    override_logo.write_bytes(b"x")
    meta = apply_cli_overrides(meta, logo=override_logo, logo_position="bottom-right")
    assert meta.logo_path == override_logo
    assert meta.logo_position == "bottom-right"


def test_cli_override_rejects_invalid_position(tmp_path: Path) -> None:
    meta_path = _write_json(tmp_path, {"title": "T"})
    meta = load_metadata(meta_path)
    with pytest.raises(OverlayError):
        apply_cli_overrides(meta, logo=None, logo_position="middle")


def test_cli_no_overrides_preserves_loaded(tmp_path: Path) -> None:
    meta_path = _write_json(tmp_path, {"title": "T", "logo_position": "top-right"})
    meta = load_metadata(meta_path)
    meta2 = apply_cli_overrides(meta, logo=None, logo_position=None)
    assert meta2.logo_position == "top-right"
    assert meta2.logo_path is None
