"""The interval maths behind scripts/render_cut_cartoon.py.

Numbers are the real separators of a Gecko compilation (source 0D1tAKi4HKg),
where episode 7 held the Dia de los Muertos episode.
"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "render_cut_cartoon.py"
spec = importlib.util.spec_from_file_location("render_cut_cartoon", SCRIPT)
rcc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rcc)

SEPARATORS = [
    (6.44, 6.68), (131.48, 131.72), (312.72, 312.92), (494.48, 494.72),
    (673.84, 674.08), (863.4, 863.64), (1045.68, 1045.88), (1233.24, 1233.52),
]
TOTAL = 1774.5


def test_a_middle_episode_is_removed_black_frames_and_all():
    # E7 sits between separator 6 and separator 7: both blacks go with it.
    assert rcc.keep_intervals(SEPARATORS, [7], TOTAL) == [(0.0, 863.4), (1045.88, TOTAL)]


def test_the_first_episode_is_removed_from_the_very_start():
    assert rcc.keep_intervals(SEPARATORS, [1], TOTAL) == [(6.68, TOTAL)]


def test_the_last_episode_is_removed_to_the_very_end():
    assert rcc.keep_intervals(SEPARATORS, [9], TOTAL) == [(0.0, 1233.24)]


def test_adjacent_episodes_merge_into_one_cut():
    assert rcc.keep_intervals(SEPARATORS, [7, 8], TOTAL) == [(0.0, 863.4), (1233.52, TOTAL)]


def test_no_cuts_keeps_everything():
    assert rcc.keep_intervals(SEPARATORS, [], TOTAL) == [(0.0, TOTAL)]


def test_an_episode_that_does_not_exist_is_refused():
    import pytest

    with pytest.raises(SystemExit):
        rcc.keep_intervals(SEPARATORS, [10], TOTAL)
