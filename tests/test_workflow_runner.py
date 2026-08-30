"""Producing a parsed request: source picking, duplicates, uploads, playlists.

Every external call is mocked — no search, no download, no render, no upload,
no YouTube API. What is exercised here is the runner's judgement:

* it never publishes the same source twice,
* it keeps going when one item fails,
* a playlist problem does not undo a published video,
* ``--dry-run`` writes nothing at all while still saying what would happen,
* and nothing is downloaded, rendered or published before the picks have been
  shown as URLs and approved.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional
from unittest import mock

import pytest

from yt_audio_filter.cartoon_catalog import CatalogVideo
from yt_audio_filter.workflow import parse_request
from yt_audio_filter.workflow_runner import (
    ItemResult,
    PlanError,
    WorkflowPlan,
    create_planner,
    excluded_by,
    load_plan,
    load_state,
    prefer_episode_length,
    run_plan,
    run_workflow,
    save_plan,
)

LINK = "https://youtu.be/U_EhMEOolI0"
LINK_ID = "U_EhMEOolI0"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _video(video_id: str, title: str = "Episode", duration: int = 25 * 60) -> CatalogVideo:
    return CatalogVideo(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=title,
        duration=duration,
        view_count=1000,
        upload_date="20260101",
        thumbnail_url="",
        channel_slug="__search__",
    )


@dataclass
class _SourceMeta:
    """Stands in for ``youtube.VideoMetadata`` (only these fields are read)."""

    video_id: str
    title: str
    channel: str
    file_path: Path
    description: str = ""
    tags: Optional[List[str]] = None
    duration: int = 0
    view_count: int = 0


class _Env:
    """Paths and a ``run_workflow`` call bound to a throwaway directory."""

    def __init__(self, tmp_path: Path) -> None:
        self.cache_dir = tmp_path / "cache"
        self.output_dir = tmp_path / "output"
        self.state_path = tmp_path / "state" / "workflow_sources.json"
        self.plan_path = tmp_path / "state" / "last_plan.json"
        self.metadata_path = tmp_path / "metadata.json"
        self.metadata_path.write_text(
            json.dumps(
                {
                    "title": "$detected_surah - $reciter",
                    "description_template": "$detected_surah by $reciter",
                    "tags": ["quran"],
                    "privacy_status": "public",
                }
            ),
            encoding="utf-8",
        )

    def run(self, request: str, **kwargs):
        return run_workflow(parse_request(request), **self.paths(), **kwargs)

    def planner(self, request: str, **kwargs):
        return create_planner(parse_request(request), **self.paths(), **kwargs)

    def paths(self) -> dict:
        return {
            "cache_dir": self.cache_dir,
            "output_dir": self.output_dir,
            "state_path": self.state_path,
            "metadata_path": self.metadata_path,
        }


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    return _Env(tmp_path)


@pytest.fixture
def mocks(env: _Env):
    """Every outside call, stubbed with a harmless default."""

    def fake_download(url: str, output_dir: Path, use_cache: bool = True) -> _SourceMeta:
        video_id = url.rsplit("=", 1)[-1].rsplit("/", 1)[-1]
        return _SourceMeta(
            video_id=video_id,
            title=f"Source {video_id}",
            channel="Some Channel",
            file_path=Path(output_dir) / f"{video_id}.mp4",
        )

    calls: dict = {}

    def fake_process(
        input_path: Path, output_path: Path, progress_callback=None, scale_height=None
    ) -> Path:
        # Recorded so tests can assert the enlargement decision, not just that
        # a render happened.
        calls.setdefault("process_scale_height", []).append(scale_height)
        if progress_callback is not None:
            progress_callback("Isolating vocals", 50)
        return Path(output_path)

    def fake_overlay(**kwargs):
        return SimpleNamespace(
            output_path=Path(kwargs["output_path"]),
            uploaded_video_id=None,
            audio_url="",
            video_url="",
        )

    with ExitStack() as stack:

        def patch(target: str, **kwargs):
            return stack.enter_context(mock.patch(target, **kwargs))

        namespace = SimpleNamespace(
            search=patch("yt_audio_filter.cartoon_search.search_videos", return_value=[]),
            add_pick=patch("yt_audio_filter.cartoon_search.add_pick_to_catalog"),
            catalog=patch("yt_audio_filter.cartoon_catalog.list_videos", return_value=[]),
            download=patch(
                "yt_audio_filter.youtube.download_video_with_metadata",
                side_effect=fake_download,
            ),
            calls=calls,
            process=patch("yt_audio_filter.pipeline.process_video", side_effect=fake_process),
            overlay=patch(
                "yt_audio_filter.overlay_pipeline.run_overlay_from_surah_numbers",
                side_effect=fake_overlay,
            ),
            upload_rendered=patch(
                "yt_audio_filter.overlay_pipeline.upload_rendered", return_value="quran-upload"
            ),
            upload=patch(
                "yt_audio_filter.uploader.upload_to_youtube", return_value="cartoon-upload"
            ),
            uploaded_ids=patch(
                "yt_audio_filter.uploader.get_uploaded_source_ids", return_value={}
            ),
            playlists=patch("yt_audio_filter.uploader.list_playlists", return_value=[]),
            create_playlist=patch(
                "yt_audio_filter.uploader.create_playlist", return_value="PL-new"
            ),
            add_to_playlist=patch("yt_audio_filter.uploader.add_to_playlist"),
            authenticate=patch(
                "yt_audio_filter.uploader.authenticate_youtube", return_value=mock.MagicMock()
            ),
            # Defaults to "could not read it" so a test that does not care
            # never gets a MagicMock threaded into a title or a duration.
            peek=patch("yt_audio_filter.yt_metadata.fetch_yt_metadata", return_value=None),
        )
        yield namespace


def _only(summary) -> ItemResult:
    assert len(summary.results) == 1
    return summary.results[0]


# ---------------------------------------------------------------------------
# Candidate ranking and exclusion (pure helpers)
# ---------------------------------------------------------------------------


def test_full_length_episodes_outrank_clips_and_compilations() -> None:
    clip = _video("clip0000001", duration=90)
    marathon = _video("long0000001", duration=6 * 3600)
    episode = _video("ep000000001", duration=30 * 60)
    ranked = prefer_episode_length([clip, marathon, episode])
    assert ranked[0] is episode


def test_ranking_keeps_relevance_order_inside_the_preferred_band() -> None:
    first = _video("aaaaaaaaaa1", duration=21 * 60)
    second = _video("bbbbbbbbbb1", duration=44 * 60)
    assert prefer_episode_length([first, second]) == [first, second]


def test_exclusion_matches_whole_words_only() -> None:
    assert excluded_by("Toy Factory Scary Night", ["scary"]) == "scary"
    # `_` is a word character, so a \b-based matcher would miss this one.
    assert excluded_by("Toys_Scary_Fun", ["scary"]) == "scary"
    assert excluded_by("Scarywood Cartoons", ["scary"]) is None
    assert excluded_by("Not Too Scary Train", ["too scary"]) == "too scary"


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


def test_a_published_source_is_skipped_and_the_next_candidate_used(env, mocks) -> None:
    mocks.search.return_value = [_video("dup00000001", "Old"), _video("new00000001", "New")]
    mocks.uploaded_ids.return_value = {
        "dup00000001": {"uploaded_id": "yt1", "url": "https://youtube.com/watch?v=yt1"}
    }

    result = _only(env.run("niloya"))

    assert result.source_id == "new00000001"
    assert [s.video_id for s in result.skipped] == ["dup00000001"]
    assert "already published" in result.skipped[0].reason
    assert result.uploaded_video_id == "cartoon-upload"


def test_a_source_from_an_earlier_run_is_skipped(env, mocks) -> None:
    """The state file catches what the channel scan cannot: rendered, not yet
    uploaded."""
    mocks.search.return_value = [_video("old00000001"), _video("new00000001")]
    env.state_path.parent.mkdir(parents=True, exist_ok=True)
    env.state_path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "old00000001",
                        "kind": "cartoon",
                        "request": "niloya",
                        "rendered_at": "2026-08-01T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = _only(env.run("niloya"))

    assert result.source_id == "new00000001"
    assert "already rendered" in result.skipped[0].reason


def test_a_finished_item_is_recorded_so_a_rerun_skips_it(env, mocks) -> None:
    mocks.search.return_value = [_video("first000001"), _video("second00001")]

    env.run("niloya")
    state = load_state(env.state_path)

    assert [s.source_id for s in state.sources] == ["first000001"]
    assert state.sources[0].uploaded_video_id == "cartoon-upload"

    # A second run walks past it rather than republishing.
    assert _only(env.run("niloya")).source_id == "second00001"


def test_count_produces_that_many_distinct_sources(env, mocks) -> None:
    mocks.search.return_value = [
        _video("aaaaaaaaaa1"),
        _video("bbbbbbbbbb1"),
        _video("cccccccccc1"),
    ]

    summary = env.run("niloya x2")

    assert len(summary.results) == 2
    assert [r.source_id for r in summary.results] == ["aaaaaaaaaa1", "bbbbbbbbbb1"]
    assert all(r.uploaded_video_id for r in summary.results)
    assert summary.exit_code == 0


# ---------------------------------------------------------------------------
# Quran items
# ---------------------------------------------------------------------------


def test_exclude_terms_filter_the_background_search(env, mocks) -> None:
    mocks.search.return_value = [
        _video("scary000001", "Toy Factory Scary Night"),
        _video("train000001", "Toy Factory Train Ride"),
    ]

    result = _only(env.run("Quran (An-Nas, Ghamdi, background: toy factory, not scary)"))

    assert mocks.search.call_args.args[0] == "toy factory"
    assert result.source_id == "train000001"
    assert "excluded term 'scary'" in result.skipped[0].reason


def test_a_quran_item_without_a_background_uses_the_curated_catalog(env, mocks) -> None:
    mocks.catalog.return_value = [_video("curated0001", "Toy Factory")]

    result = _only(env.run("Quran (An-Nas, Ghamdi)"))

    mocks.search.assert_not_called()
    assert result.source_id == "curated0001"


def test_an_overlay_pick_reaches_the_catalog_before_the_render(env, mocks) -> None:
    """``_resolve_visual_video`` only sees ids in ``list_videos``, so a search
    hit has to be persisted first or the render cannot resolve it."""
    order: List[str] = []
    mocks.search.return_value = [_video("visual00001", "Toy Factory Train")]
    mocks.add_pick.side_effect = lambda video, cache_dir=None: order.append("catalog")

    def render(**kwargs):
        order.append("render")
        return SimpleNamespace(output_path=Path(kwargs["output_path"]))

    mocks.overlay.side_effect = render

    result = _only(env.run("Quran (An-Nas, Ghamdi, background: toy factory)"))

    assert order == ["catalog", "render"]
    assert mocks.overlay.call_args.kwargs["upload"] is False
    assert mocks.overlay.call_args.kwargs["visual_video_id"] == "visual00001"
    assert result.uploaded_video_id == "quran-upload"
    assert result.playlist_name == "Quran"


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------


def test_an_existing_playlist_is_matched_case_insensitively(env, mocks) -> None:
    mocks.search.return_value = [_video("aaaaaaaaaa1")]
    mocks.playlists.return_value = [{"id": "PL-niloya", "title": "niloya"}]

    result = _only(env.run("niloya"))

    mocks.create_playlist.assert_not_called()
    assert result.playlist_id == "PL-niloya"
    assert result.playlist_created is False
    assert mocks.add_to_playlist.call_args.args[1:] == ("cartoon-upload", "PL-niloya")


def test_a_missing_playlist_is_created_once_for_the_whole_item(env, mocks) -> None:
    mocks.search.return_value = [_video("aaaaaaaaaa1"), _video("bbbbbbbbbb1")]

    summary = env.run("niloya x2")

    mocks.create_playlist.assert_called_once()
    assert mocks.create_playlist.call_args.kwargs["title"] == "Niloya"
    assert [r.playlist_id for r in summary.results] == ["PL-new", "PL-new"]
    assert [r.playlist_created for r in summary.results] == [True, False]


def test_a_playlist_failure_leaves_the_item_successful(env, mocks) -> None:
    """The video is already public by then; a playlist error is a note, not a
    rollback."""
    mocks.search.return_value = [_video("aaaaaaaaaa1")]
    mocks.add_to_playlist.side_effect = RuntimeError("quota exceeded")

    summary = env.run("niloya")
    result = _only(summary)

    assert result.ok
    assert result.uploaded_video_id == "cartoon-upload"
    assert "quota exceeded" in (result.playlist_error or "")
    assert summary.exit_code == 0


def test_a_playlist_that_cannot_be_created_still_leaves_the_item_successful(env, mocks) -> None:
    mocks.search.return_value = [_video("aaaaaaaaaa1")]
    mocks.create_playlist.return_value = None

    result = _only(env.run("niloya"))

    assert result.ok
    assert result.uploaded_video_id == "cartoon-upload"
    assert result.playlist_error is not None


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


def test_one_failing_item_does_not_stop_the_others(env, mocks) -> None:
    mocks.search.side_effect = lambda query, **kwargs: [_video(f"{query[:4]}0000001", query)]
    mocks.process.side_effect = [
        RuntimeError("demucs exploded"),
        env.output_dir / "riko_filtered.mp4",
    ]

    summary = env.run("niloya, riko")

    assert summary.results[0].error is not None
    assert "demucs exploded" in summary.results[0].error
    assert summary.results[1].uploaded_video_id == "cartoon-upload"
    assert summary.exit_code == 1
    assert [r.label for r in summary.failures] == ["niloya"]


def test_a_dead_search_fails_only_its_own_item(env, mocks) -> None:
    def search(query: str, **kwargs):
        if query == "niloya":
            raise RuntimeError("yt-dlp is down")
        return [_video("riko0000001")]

    mocks.search.side_effect = search

    summary = env.run("niloya, riko")

    assert summary.results[0].error is not None
    assert summary.results[1].uploaded_video_id == "cartoon-upload"


def test_running_out_of_candidates_fails_that_item_only(env, mocks) -> None:
    mocks.search.return_value = []

    result = _only(env.run("niloya"))

    assert result.error is not None
    assert "No unused source" in result.error


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def test_dry_run_reports_a_plan_and_writes_nothing(env, mocks) -> None:
    mocks.search.return_value = [_video("aaaaaaaaaa1", "Niloya Full Episode")]

    summary = env.run("niloya", dry_run=True)
    result = _only(summary)

    assert result.source_id == "aaaaaaaaaa1"
    assert result.planned_title  # the title that would actually be published
    assert result.rendered_path is not None  # where it would land
    assert result.playlist_name == "Niloya"
    assert result.playlist_created is True  # i.e. *would* be created
    assert summary.exit_code == 0

    mocks.download.assert_not_called()
    mocks.process.assert_not_called()
    mocks.upload.assert_not_called()
    mocks.create_playlist.assert_not_called()
    mocks.add_to_playlist.assert_not_called()
    assert not env.state_path.exists()


def test_dry_run_still_runs_the_duplicate_check(env, mocks) -> None:
    mocks.search.return_value = [_video("dup00000001"), _video("new00000001")]
    mocks.uploaded_ids.return_value = {"dup00000001": {"uploaded_id": "yt1", "url": "u"}}

    result = _only(env.run("niloya", dry_run=True))

    assert result.source_id == "new00000001"
    assert "already published" in result.skipped[0].reason


def test_dry_run_of_a_quran_item_renders_the_real_title(env, mocks) -> None:
    mocks.search.return_value = [_video("visual00001", "Toy Factory Train")]

    result = _only(env.run("Quran (An-Nas, Ghamdi, background: toy factory)", dry_run=True))

    mocks.add_pick.assert_not_called()
    mocks.overlay.assert_not_called()
    assert result.planned_title is not None
    assert "An-Nas" in result.planned_title
    assert "Ghamdi" in result.planned_title


# ---------------------------------------------------------------------------
# Pasted links
# ---------------------------------------------------------------------------


def test_an_unlabelled_link_is_filed_under_its_own_channel(env, mocks) -> None:
    mocks.download.side_effect = lambda url, output_dir, use_cache=True: _SourceMeta(
        video_id=LINK_ID,
        title="Niloya - Bölüm 1",
        channel="niloya tv",
        file_path=Path(output_dir) / f"{LINK_ID}.mp4",
    )

    result = _only(env.run(LINK))

    mocks.search.assert_not_called()  # a link names its own video
    assert result.source_id == LINK_ID
    assert result.source_title == "Niloya - Bölüm 1"
    assert result.playlist_name == "Niloya Tv"
    assert mocks.create_playlist.call_args.kwargs["title"] == "Niloya Tv"
    assert result.playlist_id == "PL-new"
    assert result.uploaded_video_id == "cartoon-upload"


def test_a_labelled_link_uses_the_label_for_the_playlist(env, mocks) -> None:
    result = _only(env.run(f"niloya: {LINK}"))

    mocks.search.assert_not_called()
    assert result.playlist_name == "Niloya"
    assert mocks.create_playlist.call_args.kwargs["title"] == "Niloya"
    assert mocks.download.call_args.args[0].endswith(LINK_ID)


def test_an_already_published_link_is_skipped_not_rerendered(env, mocks) -> None:
    mocks.uploaded_ids.return_value = {
        LINK_ID: {"uploaded_id": "yt9", "url": "https://youtube.com/watch?v=yt9"}
    }

    summary = env.run(LINK)
    result = _only(summary)

    assert result.skipped_reason is not None
    assert "already published" in result.skipped_reason
    assert "yt9" in result.skipped_reason
    mocks.download.assert_not_called()
    mocks.process.assert_not_called()
    mocks.upload.assert_not_called()
    # Refusing to republish is the right answer, so the run is still a success.
    assert result.ok
    assert summary.skipped == [result]
    assert summary.exit_code == 0


def test_a_dry_run_link_names_its_playlist_without_downloading(env, mocks) -> None:
    mocks.peek.return_value = SimpleNamespace(
        video_id=LINK_ID, title="Niloya - Bölüm 1", channel="Niloya TV", duration=900
    )

    result = _only(env.run(LINK, dry_run=True))

    mocks.download.assert_not_called()
    assert result.source_title == "Niloya - Bölüm 1"
    assert result.playlist_name == "Niloya TV"


def test_a_link_whose_metadata_cannot_be_read_still_plans(env, mocks) -> None:
    mocks.peek.side_effect = RuntimeError("yt-dlp is down")

    result = _only(env.run(LINK, dry_run=True))

    assert result.ok
    assert result.source_id == LINK_ID
    assert result.playlist_name is None  # derived from the channel at render time


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------


def test_events_are_reported_in_order(env, mocks) -> None:
    mocks.search.return_value = [_video("aaaaaaaaaa1")]
    events: List[str] = []

    env.run("niloya", on_event=lambda kind, message, data: events.append(kind))

    assert events.index("pick") < events.index("render") < events.index("uploaded")
    assert "playlist" in events


# ---------------------------------------------------------------------------
# Planning and approval, at the runner level
# ---------------------------------------------------------------------------


def test_resolving_picks_touches_nothing(env, mocks) -> None:
    """The gate is worthless if resolving already downloaded something."""
    mocks.search.return_value = [_video("aaaaaaaaaa1", "Niloya Full Episode")]

    picks = env.planner("niloya").resolve()

    assert len(picks) == 1
    assert picks[0].producible
    assert picks[0].url == "https://www.youtube.com/watch?v=aaaaaaaaaa1"
    mocks.download.assert_not_called()
    mocks.process.assert_not_called()
    mocks.upload.assert_not_called()
    assert not env.state_path.exists()


def test_rejecting_a_pick_advances_to_the_next_candidate(env, mocks) -> None:
    mocks.search.return_value = [_video("first000001"), _video("second00001")]
    planner = env.planner("niloya")
    picks = planner.resolve()
    assert picks[0].video is not None and picks[0].video.video_id == "first000001"

    picks = planner.reject(picks, [1])

    assert picks[0].video is not None
    assert picks[0].video.video_id == "second00001"
    # One search, not two: the candidate pool is shared, so pushing back moves
    # down the list instead of asking YouTube again.
    mocks.search.assert_called_once()


def test_a_rejection_is_not_written_to_the_produced_record(env, mocks) -> None:
    """``workflow_sources.json`` means "already made". A video the user merely
    did not want must stay available to every future run."""
    mocks.search.return_value = [_video("first000001"), _video("second00001")]
    planner = env.planner("niloya")
    picks = planner.produce(planner.reject(planner.resolve(), [1]))

    assert picks.results[0].source_id == "second00001"
    assert [s.source_id for s in load_state(env.state_path).sources] == ["second00001"]


def test_rejecting_one_repetition_leaves_the_others_alone(env, mocks) -> None:
    mocks.search.return_value = [
        _video("first000001"),
        _video("second00001"),
        _video("third000001"),
    ]
    planner = env.planner("niloya x2")

    picks = planner.reject(planner.resolve(), [1])

    assert [p.video.video_id for p in picks if p.video] == ["third000001", "second00001"]


def test_rejecting_a_pasted_link_does_not_re_offer_the_same_video(env, mocks) -> None:
    """A link has no next candidate, so pushing back on one has to say so
    rather than hand back the very video that was just refused."""
    planner = env.planner(LINK)

    picks = planner.reject(planner.resolve(), [1])

    assert not picks[0].producible
    assert picks[0].skipped_reason == "rejected during approval"


def test_rejecting_a_pick_number_that_does_not_exist_is_refused(env, mocks) -> None:
    mocks.search.return_value = [_video("aaaaaaaaaa1")]
    planner = env.planner("niloya")

    with pytest.raises(PlanError):
        planner.reject(planner.resolve(), [7])


def test_a_plan_survives_a_round_trip_through_json(env, mocks) -> None:
    """Approving must replay the exact video that was shown, so the whole
    ``CatalogVideo`` has to come back — not just its id."""
    mocks.search.return_value = [_video("aaaaaaaaaa1", "Niloya Full Episode", duration=1500)]
    planner = env.planner("niloya")
    save_plan(planner.plan(planner.resolve(), "niloya"), env.plan_path)

    plan = load_plan(env.plan_path)

    assert plan.request == "niloya"
    assert plan.created_at  # a timestamp, so a stale approval is visible
    assert len(plan.picks) == 1
    restored = plan.picks[0]
    assert restored.video == _video("aaaaaaaaaa1", "Niloya Full Episode", duration=1500)
    assert restored.item == parse_request("niloya")[0]
    assert restored.playlist_name == "Niloya"


def test_a_quran_plan_round_trips_with_its_surahs_and_reciter(env, mocks) -> None:
    mocks.search.return_value = [_video("visual00001", "Toy Factory Train")]
    request = "Quran (An-Nas, Ghamdi, background: toy factory, not scary)"
    planner = env.planner(request)
    save_plan(planner.plan(planner.resolve(), request), env.plan_path)

    item = load_plan(env.plan_path).picks[0].item

    assert item == parse_request(request)[0]


def test_running_a_plan_produces_it_without_searching_again(env, mocks) -> None:
    mocks.search.return_value = [_video("aaaaaaaaaa1")]
    planner = env.planner("niloya")
    save_plan(planner.plan(planner.resolve(), "niloya"), env.plan_path)
    mocks.search.reset_mock()

    summary = run_plan(load_plan(env.plan_path), **env.paths())

    mocks.search.assert_not_called()
    assert _only(summary).source_id == "aaaaaaaaaa1"
    assert _only(summary).uploaded_video_id == "cartoon-upload"


def test_a_plan_only_matches_the_request_it_was_resolved_for() -> None:
    plan = WorkflowPlan(request="niloya")

    assert plan.matches("niloya")
    assert plan.matches("  niloya  ")  # the shell is allowed to be untidy
    assert not plan.matches("riko")
    assert plan.matches(None)  # a bare --approve contradicts nothing


def test_a_missing_plan_file_says_so(tmp_path) -> None:
    with pytest.raises(PlanError) as excinfo:
        load_plan(tmp_path / "nope.json")

    assert "No plan is waiting" in str(excinfo.value)


def test_a_plan_from_another_version_is_refused_rather_than_half_read(env) -> None:
    env.plan_path.parent.mkdir(parents=True, exist_ok=True)
    env.plan_path.write_text(json.dumps({"version": 999, "picks": []}), encoding="utf-8")

    with pytest.raises(PlanError):
        load_plan(env.plan_path)


# ---------------------------------------------------------------------------
# CLI approval gate — this command publishes publicly and unattended
# ---------------------------------------------------------------------------


@pytest.fixture
def cli(env, mocks):
    """``workflow_cli`` driven for real, against the stubbed externals.

    Only the directories are redirected: the planner, the plan file and the
    approval logic all run, because they are what these tests are about.
    stdin claims to be a terminal by default; the non-interactive tests turn
    that off.
    """
    from yt_audio_filter import workflow_cli, workflow_runner

    def with_paths(kwargs: dict) -> dict:
        kwargs.update(output_dir=env.output_dir, state_path=env.state_path)
        return kwargs

    with ExitStack() as stack:

        def patch(target: str, **kwargs):
            return stack.enter_context(mock.patch(target, **kwargs))

        run = patch(
            "yt_audio_filter.workflow_cli.run_workflow",
            side_effect=lambda items, **kw: workflow_runner.run_workflow(items, **with_paths(kw)),
        )
        planner = patch(
            "yt_audio_filter.workflow_cli.create_planner",
            side_effect=lambda items, **kw: workflow_runner.create_planner(
                items, **with_paths(kw)
            ),
        )
        approve = patch(
            "yt_audio_filter.workflow_cli.run_plan",
            side_effect=lambda plan, **kw: workflow_runner.run_plan(plan, **with_paths(kw)),
        )
        isatty = patch("sys.stdin.isatty", return_value=True)
        prompt = patch("builtins.input", return_value="")

        def main(argv):
            return workflow_cli.main(
                list(argv)
                + [
                    "--metadata",
                    str(env.metadata_path),
                    "--cache-dir",
                    str(env.cache_dir),
                    "--plan-file",
                    str(env.plan_path),
                ]
            )

        yield SimpleNamespace(
            main=main,
            run=run,
            planner=planner,
            approve=approve,
            prompt=prompt,
            isatty=isatty,
            env=env,
            mocks=mocks,
        )


def _assert_nothing_was_produced(mocks) -> None:
    mocks.download.assert_not_called()
    mocks.process.assert_not_called()
    mocks.overlay.assert_not_called()
    mocks.upload.assert_not_called()
    mocks.upload_rendered.assert_not_called()


# ------------------------------------------------------- what the user sees


def test_every_pick_is_printed_as_an_openable_url(cli, capsys) -> None:
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1", "Niloya Full Episode")]
    cli.prompt.return_value = "n"

    cli.main(["niloya"])

    out = capsys.readouterr().out
    assert "https://www.youtube.com/watch?v=aaaaaaaaaa1" in out
    assert "Niloya Full Episode" in out


def test_a_quran_pick_shows_its_surahs_reciter_and_background_url(cli, capsys) -> None:
    cli.mocks.search.return_value = [_video("visual00001", "Toy Factory Train")]
    cli.prompt.return_value = "n"

    cli.main(["Quran (An-Nas, Ghamdi, background: toy factory)"])

    out = capsys.readouterr().out
    assert "An-Nas" in out
    assert "Saad Al-Ghamdi" in out
    assert "https://www.youtube.com/watch?v=visual00001" in out


# ------------------------------------------------------- interactive gate


def test_approving_produces_every_pick(cli) -> None:
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]
    cli.prompt.return_value = "y"

    assert cli.main(["niloya"]) == 0

    cli.mocks.upload.assert_called_once()


def test_rejecting_everything_produces_nothing(cli) -> None:
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]
    cli.prompt.return_value = "n"

    assert cli.main(["niloya"]) == 1

    _assert_nothing_was_produced(cli.mocks)
    assert not cli.env.state_path.exists()


def test_pushing_back_on_one_pick_offers_the_next_candidate(cli, capsys) -> None:
    """The push-back the whole gate exists for: the URL turned out to be the
    wrong episode, and the run should carry on with a different one."""
    cli.mocks.search.return_value = [_video("wrong000001"), _video("right000001")]
    cli.prompt.side_effect = ["1", "y"]

    assert cli.main(["niloya"]) == 0

    out = capsys.readouterr().out
    assert "https://www.youtube.com/watch?v=right000001" in out
    assert cli.mocks.download.call_args.args[0].endswith("right000001")
    assert [s.source_id for s in load_state(cli.env.state_path).sources] == ["right000001"]


def test_an_answer_that_makes_no_sense_never_counts_as_approval(cli) -> None:
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]
    cli.prompt.return_value = "maybe"

    assert cli.main(["niloya"]) == 1

    _assert_nothing_was_produced(cli.mocks)


def test_a_terminal_that_cannot_be_read_saves_the_plan_instead(cli) -> None:
    """Windows calls a console handle a terminal even when nothing will ever be
    typed into it, so the prompt itself is the only honest test. Falling back
    to the saved plan beats throwing the resolved picks away."""
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]
    cli.prompt.side_effect = EOFError

    assert cli.main(["niloya"]) == 10

    _assert_nothing_was_produced(cli.mocks)
    assert load_plan(cli.env.plan_path).picks[0].video.video_id == "aaaaaaaaaa1"


# ------------------------------------------------------- non-interactive


def test_without_a_terminal_the_plan_is_saved_and_nothing_runs(cli, capsys) -> None:
    cli.isatty.return_value = False
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1", "Niloya Full Episode")]

    assert cli.main(["niloya"]) == 10

    _assert_nothing_was_produced(cli.mocks)
    cli.prompt.assert_not_called()
    assert cli.env.plan_path.exists()
    out = capsys.readouterr().out
    assert "https://www.youtube.com/watch?v=aaaaaaaaaa1" in out
    assert "--approve" in out

    plan = load_plan(cli.env.plan_path)
    assert plan.request == "niloya"
    assert plan.created_at
    assert plan.picks[0].video is not None
    assert plan.picks[0].video.video_id == "aaaaaaaaaa1"


def test_approve_runs_the_saved_picks_without_searching_again(cli) -> None:
    cli.isatty.return_value = False
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]
    assert cli.main(["niloya"]) == 10
    cli.mocks.search.reset_mock()

    assert cli.main(["--approve", "niloya"]) == 0

    cli.mocks.search.assert_not_called()
    cli.mocks.upload.assert_called_once()
    assert cli.mocks.download.call_args.args[0].endswith("aaaaaaaaaa1")


def test_approve_is_refused_when_the_request_has_changed(cli) -> None:
    cli.isatty.return_value = False
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]
    assert cli.main(["niloya"]) == 10

    assert cli.main(["--approve", "riko"]) == 1

    _assert_nothing_was_produced(cli.mocks)
    assert cli.env.plan_path.exists()  # still there, still approvable


def test_force_applies_a_saved_plan_to_a_different_request(cli) -> None:
    cli.isatty.return_value = False
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]
    assert cli.main(["niloya"]) == 10

    assert cli.main(["--approve", "--force", "riko"]) == 0

    cli.mocks.upload.assert_called_once()


def test_a_bare_approve_needs_no_request(cli) -> None:
    cli.isatty.return_value = False
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]
    assert cli.main(["niloya"]) == 10

    assert cli.main(["--approve"]) == 0

    cli.mocks.upload.assert_called_once()


def test_an_approval_is_good_exactly_once(cli) -> None:
    """The duplicate check ran when the plan was resolved, so replaying the
    same file would render and publish the same source again."""
    cli.isatty.return_value = False
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]
    assert cli.main(["niloya"]) == 10
    assert cli.main(["--approve", "niloya"]) == 0

    assert not cli.env.plan_path.exists()
    assert cli.main(["--approve", "niloya"]) == 1
    cli.mocks.upload.assert_called_once()


def test_a_missing_plan_file_is_reported_clearly(cli, capsys) -> None:
    assert cli.main(["--approve"]) == 1

    _assert_nothing_was_produced(cli.mocks)
    assert "No plan is waiting" in capsys.readouterr().err


# ------------------------------------------------------- the other switches


def test_yes_skips_the_gate_entirely(cli) -> None:
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]

    assert cli.main(["--yes", "niloya"]) == 0

    cli.prompt.assert_not_called()
    cli.run.assert_called_once()
    cli.planner.assert_not_called()
    cli.mocks.upload.assert_called_once()
    assert not cli.env.plan_path.exists()


def test_yes_still_works_without_a_terminal(cli) -> None:
    """The cron case: no TTY, but the decision was made when the job was set up."""
    cli.isatty.return_value = False
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]

    assert cli.main(["--yes", "niloya"]) == 0

    cli.mocks.upload.assert_called_once()
    assert not cli.env.plan_path.exists()


def test_a_dry_run_never_prompts_and_writes_no_plan_file(cli) -> None:
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]

    assert cli.main(["--dry-run", "niloya"]) == 0

    cli.prompt.assert_not_called()
    assert cli.run.call_args.kwargs["dry_run"] is True
    _assert_nothing_was_produced(cli.mocks)
    assert not cli.env.plan_path.exists()
    assert not cli.env.state_path.exists()


def test_a_dry_run_writes_no_plan_file_without_a_terminal_either(cli) -> None:
    cli.isatty.return_value = False
    cli.mocks.search.return_value = [_video("aaaaaaaaaa1")]

    assert cli.main(["--dry-run", "niloya"]) == 0

    assert not cli.env.plan_path.exists()


def test_an_already_published_pick_never_reaches_the_gate(cli, capsys) -> None:
    """Duplicate checking happens before the plan is shown, so nobody is asked
    to approve something the channel already has."""
    cli.isatty.return_value = False
    cli.mocks.uploaded_ids.return_value = {
        LINK_ID: {"uploaded_id": "yt9", "url": "https://youtube.com/watch?v=yt9"}
    }

    assert cli.main([LINK]) == 0

    cli.prompt.assert_not_called()
    _assert_nothing_was_produced(cli.mocks)
    assert not cli.env.plan_path.exists()
    assert "already published" in capsys.readouterr().out


def test_an_unreadable_request_exits_without_running_anything(cli) -> None:
    assert cli.main(["Quran (nonsense-surah, Ghamdi)"]) == 2

    cli.run.assert_not_called()
    cli.planner.assert_not_called()


def test_force_without_approve_is_rejected(cli) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--force", "niloya"])


def test_approve_cannot_be_a_dry_run(cli) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--approve", "--dry-run"])


def test_a_request_is_required_when_not_approving(cli) -> None:
    with pytest.raises(SystemExit):
        cli.main([])


# ------------------------------------------------------- 720p by default


def test_renders_target_1080p_without_being_asked() -> None:
    """1080p is not about recovering detail — the sources are 360p and those
    pixels are interpolated. It is about YouTube's encoding ladder, which is
    chosen by uploaded resolution and gives 1080p a markedly higher bitrate."""
    from yt_audio_filter import workflow_runner as wr

    assert wr.DEFAULT_HEIGHT == 1080
    assert wr.resolution_for(1080) == (1920, 1080)
    assert wr.resolution_for(720) == (1280, 720)


def test_a_small_source_is_scaled_up_to_the_target() -> None:
    """A 360p upload gets a much worse bitrate ladder from YouTube than a 1080p
    one, so enlarging is worth the re-encode even though no detail is added."""
    from yt_audio_filter.workflow_runner import scale_height_for

    assert scale_height_for(source_height=360, target=1080) == 1080
    assert scale_height_for(source_height=720, target=1080) == 1080


def test_a_source_already_at_or_above_the_target_is_left_alone() -> None:
    """Never re-encode for nothing, and never downscale someone's good source."""
    from yt_audio_filter.workflow_runner import scale_height_for

    assert scale_height_for(source_height=1080, target=1080) is None
    assert scale_height_for(source_height=2160, target=1080) is None


def test_an_unknown_source_height_is_left_alone() -> None:
    """If ffprobe could not tell us, copying beats a guessed re-encode."""
    from yt_audio_filter.workflow_runner import scale_height_for

    assert scale_height_for(source_height=None, target=1080) is None


def test_the_target_height_is_overridable() -> None:
    from yt_audio_filter.workflow_runner import resolution_for, scale_height_for

    assert resolution_for(1080) == (1920, 1080)
    assert scale_height_for(source_height=720, target=1080) == 1080
