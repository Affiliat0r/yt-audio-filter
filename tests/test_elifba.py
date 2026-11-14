"""Elif-Ba: the curriculum port and the toddler storyboard.

The sound numbering is the part that can silently go wrong: one slipped index and
every tile after it plays the next letter's recording. These pin the helpers to
the numbering Diyanet's site uses.
"""

import json
from pathlib import Path

import pytest

from elifba import curriculum as c
from elifba.storyboard import FAMILIES, SERIES, Script, episode

AUDIO = Path(c.__file__).resolve().parent / "audio"


def sounds(rows):
    return [[t["sound"] for t in r["tiles"]] for r in rows]


def test_fetha_thick_rows_skip_a_number_between_rows():
    # The fetha lesson's thick rows have kalinRowGap=1: btn_25-28, then btn_30-33.
    thin, thick = c.ye([c.fetha(x) for x in c.HE], [f"{x}َ" for x in c.BE], 1, 25, kalin_row_gap=1)
    assert sounds(thick["rows"]) == [["btn_25", "btn_26", "btn_27", "btn_28"], ["btn_30", "btn_31", "btn_32", "btn_33"]]
    assert sounds(thin["rows"])[0] == ["btn_1", "btn_2", "btn_3", "btn_4", "btn_5"]


def test_the_last_thin_row_takes_the_remainder():
    thin, _ = c.ye(c.EE, c.KE, 1, 22)
    assert [len(r["tiles"]) for r in thin["rows"]] == [5, 5, 5, 6]
    assert thin["rows"][-1]["tiles"][-1]["sound"] == "btn_21"


def test_rhythm_rows_are_numbered_across_both_groups():
    thin, thick = c.ye(c.HE, c.BE, 1, 21)
    assert [r["ritmikSound"] for r in thin["rows"] + thick["rows"]] == [f"satir_{i}" for i in range(1, 7)]


def test_letter_and_its_name_share_one_recording():
    rows = c.be_pairs(6, [["ح", "حا"], ["خ", "خا"]])
    assert sounds(rows) == [["btn_6", "btn_7"], ["btn_6", "btn_7"]]


def test_comparison_sets_give_one_recording_per_letter_across_forms():
    rows = c.sets([["بَ", "تَ"], ["بَا", "تَا"], ["بًا", "تًا"]], 1, 5)
    assert sounds(rows) == [["btn_1", "btn_2"]] * 3


def test_names_of_all_28_letters_are_in_alphabet_order():
    unit = c.build()[0]
    harfler = unit["lessons"][0]
    tiles = [t for r in harfler["groups"][0]["rows"] for t in r["tiles"]]
    assert [t["arabic"] for t in tiles] == c.JE
    assert [t["sound"] for t in tiles] == [f"btn_{i}" for i in range(1, 29)]


@pytest.mark.skipif(not AUDIO.exists(), reason="run python -m elifba.fetch_audio first")
def test_every_referenced_recording_is_on_disk_or_known_missing():
    missing = set(json.loads((AUDIO / "missing.json").read_text()))
    absent = []
    for unit in c.build():
        for les in unit["lessons"]:
            for name in c.iter_sounds(les):
                rel = c.sound_path(les, name)
                if rel not in missing and not (AUDIO / rel).exists():
                    absent.append(rel)
    assert absent == []


@pytest.mark.skipif(not (AUDIO / "quran").exists(), reason="run python -m elifba.fetch_audio first")
def test_no_two_sounds_overlap_and_scenes_tile_the_episode():
    ep = episode(1, SERIES[0])
    spans = sorted((a["t"], a["t"] + a["dur"]) for a in ep["audio"])
    for (a0, a1), (b0, _) in zip(spans, spans[1:]):
        assert b0 >= a1 - 1e-3, "two sounds overlap"
    for prev, nxt in zip(ep["scenes"], ep["scenes"][1:]):
        assert abs(prev["end"] - nxt["start"]) < 1e-3
    assert ep["scenes"][-1]["end"] == pytest.approx(ep["duration"], abs=1e-3)


@pytest.mark.skipif(not (AUDIO / "quran").exists(), reason="run python -m elifba.fetch_audio first")
def test_the_game_pairs_look_alike_letters_when_it_can():
    ep = episode(1, SERIES[0])
    rounds = [s for s in ep["scenes"] if s["type"] == "game"]
    for r in rounds:
        a, b = r["options"]
        if r["target"] in "بتث":
            assert any(a in f and b in f for f in FAMILIES)


def test_game_warms_up_then_pairs_each_letter_with_its_nearest_twin():
    from elifba.storyboard import game_rounds

    # ت against ث (two dots on top vs three) is the contrast that matters most.
    assert game_rounds(["ا", "ب", "ت", "ث"]) == [("ا", "ب"), ("ب", "ت"), ("ت", "ث"), ("ث", "ت")]


def test_a_repeated_pair_swaps_its_cards_and_no_side_runs_three_times():
    from elifba.storyboard import answer_sides, game_rounds

    for group in SERIES:
        rounds = game_rounds(group)
        sides = answer_sides(rounds)
        layouts = [(t, o) if s == 0 else (o, t) for (t, o), s in zip(rounds, sides)]
        for i in range(1, len(rounds)):
            if set(rounds[i]) == set(rounds[i - 1]):
                assert layouts[i] != layouts[i - 1], f"{group}: same cards in the same places twice"
        assert all(not (sides[i] == sides[i - 1] == sides[i - 2]) for i in range(2, len(sides))), group


def test_every_series_letter_has_a_dots_description():
    from elifba.storyboard import DOTS

    assert {l for group in SERIES for l in group} == set(c.JE)
    assert set(DOTS) == set(c.JE)


def test_script_cursor_advances_past_waits():
    s = Script()
    s.wait(1.5)
    assert s.t == 1.5
