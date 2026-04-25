"""Unit tests for yt_audio_filter.render_presets."""

import pytest

from yt_audio_filter.exceptions import YTAudioFilterError
from yt_audio_filter.render_presets import (
    RenderPreset,
    get_preset,
    list_presets,
)


def test_list_presets_returns_render_preset_instances() -> None:
    presets = list_presets()
    assert len(presets) >= 4
    for preset in presets:
        assert isinstance(preset, RenderPreset)


def test_list_presets_returns_fresh_list_each_call() -> None:
    """Mutating the returned list must not leak into future calls."""
    first = list_presets()
    first.clear()
    second = list_presets()
    assert len(second) >= 4


def test_list_presets_includes_whatsapp_vertical() -> None:
    slugs = [p.slug for p in list_presets()]
    assert "whatsapp_vertical" in slugs


def test_list_presets_includes_all_expected_slugs() -> None:
    slugs = {p.slug for p in list_presets()}
    expected = {
        "youtube_landscape",
        "youtube_landscape_720",
        "whatsapp_vertical",
        "instagram_square",
    }
    assert expected.issubset(slugs)


def test_get_preset_known_slug() -> None:
    preset = get_preset("youtube_landscape")
    assert preset.slug == "youtube_landscape"
    assert preset.resolution == (1920, 1080)
    assert preset.scale_mode == "fit"
    assert preset.aspect_ratio == "16:9"


def test_get_preset_unknown_raises() -> None:
    with pytest.raises(YTAudioFilterError) as exc_info:
        get_preset("does_not_exist")
    # The error should mention the bogus slug and list valid options.
    msg = str(exc_info.value)
    assert "does_not_exist" in msg
    assert "youtube_landscape" in msg


def test_whatsapp_vertical_aspect_is_9_16() -> None:
    preset = get_preset("whatsapp_vertical")
    assert preset.aspect_ratio == "9:16"
    width, height = preset.resolution
    # 9:16 means height > width.
    assert height > width
    # Verify the actual ratio rounds to 9:16.
    assert round(width / height, 4) == round(9 / 16, 4)


def test_whatsapp_vertical_uses_fill_scale_mode() -> None:
    """Vertical presets must crop-to-fill, not letterbox.

    A 16:9 cartoon source rendered into a 9:16 frame with ``"fit"`` would
    produce a thin band of cartoon between giant black bars on a phone.
    Phase 2 will read this field to drive its render call.
    """
    preset = get_preset("whatsapp_vertical")
    assert preset.scale_mode == "fill"


def test_youtube_landscape_720_uses_fit() -> None:
    preset = get_preset("youtube_landscape_720")
    assert preset.resolution == (1280, 720)
    assert preset.scale_mode == "fit"


def test_instagram_square_is_1_1_and_fill() -> None:
    preset = get_preset("instagram_square")
    assert preset.resolution == (1080, 1080)
    assert preset.aspect_ratio == "1:1"
    assert preset.scale_mode == "fill"


def test_render_preset_is_immutable() -> None:
    preset = get_preset("youtube_landscape")
    with pytest.raises(Exception):
        # ``frozen=True`` dataclass — assignment must raise.
        preset.slug = "tampered"  # type: ignore[misc]


def test_all_presets_have_known_scale_mode() -> None:
    """Every preset must declare a scale_mode the overlay knows how to render."""
    for preset in list_presets():
        assert preset.scale_mode in {"fit", "fill"}, (
            f"{preset.slug} has unsupported scale_mode {preset.scale_mode!r}"
        )


def test_all_presets_have_positive_resolution() -> None:
    for preset in list_presets():
        width, height = preset.resolution
        assert width > 0 and height > 0, f"{preset.slug} has non-positive resolution"


def test_all_presets_have_display_name_and_description() -> None:
    for preset in list_presets():
        assert preset.display_name, f"{preset.slug} missing display_name"
        assert preset.description, f"{preset.slug} missing description"
