"""Choosing the background for a surah render.

``run_overlay_surahs`` picks ``max(visuals, key=duration)`` — always the single
longest video on the channel. That is a fine default and a dead end the moment
you want the same recitation over a *different* picture: a rerun deterministically
picks the same visual, so the second render is the first one again.

The user asked for exactly that: "ik wil alsnog de tweede exemplaar van die
Qur'an, het gaat om de nieuwe video achtergrond". Hence an override.

It is deliberately not a blind pass-through. Naming a visual that is not on the
channel is the easy mistake — a typo'd id, or one from a different channel —
and silently falling back to the longest would publish a video that looks
identical to the one already on the channel, which is precisely what the
override exists to avoid.
"""

from __future__ import annotations

from unittest import mock

import pytest

from yt_audio_filter import overlay_pipeline
from yt_audio_filter.channel_discovery import Candidate


def _candidate(video_id: str, duration: int, title: str = "Toy Factory Trains") -> Candidate:
    return Candidate(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=title,
        duration=duration,
        view_count=1000,
    )


CANDIDATES = [
    _candidate("YFgywalpeMA", 3900, "the longest - what the default picks"),
    _candidate("nm7Nu5UCnwQ", 3567, "Toy Factory Trains - Choo Choo Train"),
    _candidate("W3ZYcwteexg", 1884, "Toy Factory Cartoon Train"),
]


# ------------------------------------------------------------- the selector


def test_without_an_override_the_longest_wins() -> None:
    """The existing behaviour, kept."""
    assert overlay_pipeline.select_visual(CANDIDATES, None).video_id == "YFgywalpeMA"


def test_an_override_picks_that_video() -> None:
    assert overlay_pipeline.select_visual(CANDIDATES, "W3ZYcwteexg").video_id == "W3ZYcwteexg"


def test_the_override_is_not_required_to_be_the_longest() -> None:
    """The whole point is choosing a *different* one, usually shorter."""
    chosen = overlay_pipeline.select_visual(CANDIDATES, "nm7Nu5UCnwQ")
    assert chosen.duration < max(c.duration for c in CANDIDATES)


def test_an_unknown_id_is_refused_rather_than_silently_ignored() -> None:
    """Falling back to the longest would republish the picture the override
    was asked for to avoid — and nobody would notice until it was public."""
    from yt_audio_filter.exceptions import OverlayError

    with pytest.raises(OverlayError, match="not on the video channel"):
        overlay_pipeline.select_visual(CANDIDATES, "doesNotExist")


def test_the_error_names_something_usable() -> None:
    """A bare refusal leaves the user guessing at eleven-character ids."""
    from yt_audio_filter.exceptions import OverlayError

    with pytest.raises(OverlayError) as excinfo:
        overlay_pipeline.select_visual(CANDIDATES, "typoTypoTyp")
    message = str(excinfo.value) + str(getattr(excinfo.value, "details", ""))
    assert "YFgywalpeMA" in message or "nm7Nu5UCnwQ" in message


def test_an_empty_channel_is_a_clear_failure() -> None:
    from yt_audio_filter.exceptions import OverlayError

    with pytest.raises(OverlayError):
        overlay_pipeline.select_visual([], None)


# ------------------------------------------------------- threaded through


def test_the_surah_render_accepts_a_visual_override() -> None:
    import inspect

    params = inspect.signature(overlay_pipeline.run_overlay_surahs).parameters
    assert "visual_video_id" in params


def test_the_cli_passes_video_id_into_surah_mode(tmp_path) -> None:
    """--video-id already existed for numbers mode and was ignored here, so a
    user naming a background would have been quietly given the default."""
    from types import SimpleNamespace

    from yt_audio_filter import overlay_cli

    metadata = tmp_path / "m.json"
    metadata.write_text(
        '{"title": "t", "description_template": "d", "tags": [], "privacy_status": "private"}',
        encoding="utf-8",
    )
    seen = {}

    def capture(*args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(output_path=tmp_path / "o.mp4", uploaded_video_id=None)

    with mock.patch.object(overlay_cli, "run_overlay_surahs", side_effect=capture):
        overlay_cli.main([
            "--surah", "https://youtu.be/aaaaaaaaaaa",
            "--video-channel", "@toyfactorycartoon",
            "--audio-channel", "@a",
            "--video-id", "W3ZYcwteexg",
            "--metadata", str(metadata),
        ])

    assert seen.get("visual_video_id") == "W3ZYcwteexg"
