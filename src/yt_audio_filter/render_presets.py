"""Named render presets pairing a target resolution with a scale mode.

A preset answers two questions a caller must agree on before invoking the
overlay render:

1. **Resolution** — the output (width, height) in pixels.
2. **Scale mode** — how the source visual fits that frame:
   * ``"fit"`` — letterbox: keep the whole source visible, pad with black
     where aspect ratios disagree (FFmpeg ``scale=W:H:force_original_aspect_ratio=decrease``
     followed by ``pad``). Safe for landscape source on landscape target.
   * ``"fill"`` — crop-to-fill: the source covers the whole frame; content
     outside the target aspect is cropped. Required for vertical (9:16)
     output where landscape cartoon source would otherwise show black bars
     above and below the visual on a phone screen.

The Streamlit app (Phase 2) will surface the preset list as a dropdown and
pass the resolution into ``ffmpeg_overlay.render_overlay`` directly. The
``scale_mode`` field is informational at the moment — Phase 2 should read it
to decide whether to invoke a scale-to-fill helper, or to pass the new
``scale_mode`` parameter on ``build_filter_graph`` once that branch is wired
end-to-end.
"""

from dataclasses import dataclass
from typing import List, Tuple

from .exceptions import YTAudioFilterError


@dataclass(frozen=True)
class RenderPreset:
    """A named bundle of render parameters for a target distribution channel."""

    slug: str
    display_name: str
    resolution: Tuple[int, int]
    scale_mode: str  # "fit" or "fill"
    aspect_ratio: str  # "16:9", "9:16", "1:1" (informational)
    description: str


_PRESETS: Tuple[RenderPreset, ...] = (
    RenderPreset(
        slug="youtube_landscape",
        display_name="YouTube (1080p landscape)",
        resolution=(1920, 1080),
        scale_mode="fit",
        aspect_ratio="16:9",
        description=(
            "Standard YouTube landscape upload at 1920x1080. Letterboxes "
            "non-16:9 sources to keep the full visual visible."
        ),
    ),
    RenderPreset(
        slug="youtube_landscape_720",
        display_name="YouTube (720p landscape)",
        resolution=(1280, 720),
        scale_mode="fit",
        aspect_ratio="16:9",
        description=(
            "Smaller YouTube landscape render at 1280x720. Pair with "
            "--upscale to recover detail when the cartoon source is "
            "lower-resolution."
        ),
    ),
    RenderPreset(
        slug="whatsapp_vertical",
        display_name="WhatsApp (9:16 vertical)",
        resolution=(1080, 1920),
        scale_mode="fill",
        aspect_ratio="9:16",
        description=(
            "Phone-first vertical export at 1080x1920 for WhatsApp status / "
            "share. Crops landscape source to fill the frame so the cartoon "
            "doesn't appear in a thin band between black bars."
        ),
    ),
    RenderPreset(
        slug="instagram_square",
        display_name="Instagram (1:1 square)",
        resolution=(1080, 1080),
        scale_mode="fill",
        aspect_ratio="1:1",
        description=(
            "Square 1080x1080 render for Instagram feed posts. Crops to fill "
            "since most cartoon sources are 16:9 and a letterboxed square "
            "looks like a postage stamp."
        ),
    ),
)


def list_presets() -> List[RenderPreset]:
    """Return the available render presets in display order.

    The list is fresh on every call so callers can mutate the result without
    affecting future callers.
    """
    return list(_PRESETS)


def get_preset(slug: str) -> RenderPreset:
    """Look up a preset by slug.

    Raises:
        YTAudioFilterError: when no preset matches the slug. Includes the list
            of valid slugs in the hint.
    """
    for preset in _PRESETS:
        if preset.slug == slug:
            return preset
    valid = ", ".join(p.slug for p in _PRESETS)
    raise YTAudioFilterError(
        f"Unknown render preset: {slug!r}",
        f"Valid presets: {valid}",
    )
