"""Elif-Ba: the curriculum port and the toddler storyboard.

The sound numbering is the part that can silently go wrong: one slipped index and
every tile after it plays the next letter's recording. These pin the helpers to
the numbering Diyanet's site uses.
"""

import json
from pathlib import Path

import pytest

from elifba import curriculum as c
from elifba.storyboard import HEARINGS, MAX_SECONDS, Script, episode

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


NEEDS_AUDIO = pytest.mark.skipif(not (AUDIO / "quran").exists(), reason="run python -m elifba.fetch_audio first")


@NEEDS_AUDIO
def test_no_two_sounds_overlap_and_scenes_tile_the_video():
    ep = episode()
    spans = sorted((a["t"], a["t"] + a["dur"]) for a in ep["audio"])
    for (a0, a1), (b0, _) in zip(spans, spans[1:]):
        assert b0 >= a1 - 1e-3, "two sounds overlap"
    for prev, nxt in zip(ep["scenes"], ep["scenes"][1:]):
        assert abs(prev["end"] - nxt["start"]) < 1e-3
    assert ep["scenes"][-1]["end"] == pytest.approx(ep["duration"], abs=1e-3)


@NEEDS_AUDIO
def test_all_28_letters_in_order_each_named_twice_within_five_minutes():
    ep = episode()
    letters = [s["text"] for s in ep["scenes"] if s["type"] == "letter"]
    assert letters == c.JE
    names = [a["path"] for a in ep["audio"] if "harfler/harfler" in a["path"]]
    assert names == [f"audio/elifba/harfler/harfler/btn_{i}.mp3" for i in range(1, 29) for _ in range(HEARINGS)]
    assert ep["duration"] <= MAX_SECONDS


@NEEDS_AUDIO
def test_youtube_chapters_are_at_least_ten_seconds_apart():
    ep = episode()
    ts = [ch["t"] for ch in ep["chapters"]] + [ep["duration"]]
    assert ts[0] == 0
    assert all(b - a >= 10 for a, b in zip(ts, ts[1:]))


@NEEDS_AUDIO
def test_fetha_lesson_is_thin_letters_then_thick_with_the_books_ya():
    ep = episode("fetha")
    assert len(ep["items"]) == 28
    assert ep["items"][:3] == ["اَ", "بَ", "تَ"]
    assert ep["items"][20:] == ["خَ", "رَ", "غَ", "قَ", "صَ", "ضَ", "طَ", "ظَ"]
    assert "يَ" in ep["items"] and "ىَ" not in ep["items"]   # the printed Elif-Ba's spelling
    sounds = [a["path"].split("/")[-1] for a in ep["audio"] if "fetha/fetha" in a["path"]][::2]
    assert sounds[19:22] == ["btn_20.mp3", "btn_25.mp3", "btn_26.mp3"]   # Diyanet's numbering skips 21-24
    assert ep["duration"] <= MAX_SECONDS


@NEEDS_AUDIO
def test_harekeler_gives_every_letter_fetha_kesra_damme_from_their_own_units():
    ep = episode("harekeler")
    triples = [s for s in ep["scenes"] if s["type"] == "triple"]
    assert len(triples) == 28
    assert [f["text"] for f in triples[1]["forms"]] == ["بَ", "بِ", "بُ"]
    assert [f["text"] for f in triples[27]["forms"]] == ["يَ", "يِ", "يُ"]   # the book's dotted ya
    paths = [a["path"] for a in ep["audio"] if "/elifba/" in a["path"] and "harfler" not in a["path"]]
    assert len(paths) == 84
    assert paths[3:6] == ["audio/elifba/fetha/fetha/btn_2.mp3", "audio/elifba/kesra/kesra/btn_2.mp3",
                          "audio/elifba/damme/damme/btn_2.mp3"]
    # ra is thick with fetha and damme but thin with kesra in Diyanet's units: three different slots
    ra = paths[9 * 3:9 * 3 + 3]
    assert ra == ["audio/elifba/fetha/fetha/btn_26.mp3", "audio/elifba/kesra/kesra/btn_9.mp3",
                  "audio/elifba/damme/damme/btn_22.mp3"]
    assert ep["duration"] <= MAX_SECONDS


@NEEDS_AUDIO
def test_letters_in_words_is_one_video_with_all_28_letters():
    ep = episode("kelime")
    scenes = [s for s in ep["scenes"] if s["type"] == "words"]
    assert [s["letter"] for s in scenes] == c.JE
    assert all(len(s["words"]) == 3 for s in scenes)
    assert ep["duration"] <= ep["max_seconds"]
    ba = next(s for s in scenes if s["letter"] == "ب")
    assert ba["words"] == ["{ب}عث", "ع{ب}د", "وه{ب}"]          # start, middle, end
    words = [a["path"] for a in ep["audio"] if "sekilleri" in a["path"]]
    assert words[3:6] == [f"audio/elifba/harfler/sekilleri/btn_{n}.mp3" for n in (12, 13, 14)]
    # the letter on its own is named with lesson 1's recording, so alif stays "Elif"
    assert ep["audio"][1]["path"] == "audio/elifba/harfler/harfler/btn_1.mp3"


def test_only_the_words_lesson_may_run_past_five_minutes():
    from elifba.storyboard import LESSON_MAX_SECONDS

    assert LESSON_MAX_SECONDS == {"kelime": 720}


def test_every_spelled_word_has_one_timing_per_letter_in_order():
    from elifba.spelling import letters_of
    from elifba.storyboard import SPELLING, kelime_items

    for item in kelime_items(0):
        for w in item["words"]:
            segs = SPELLING[w["sound"].name]
            assert len(segs) == len(letters_of(w["text"])), w["text"]
            assert all(a < b for a, b in segs)
            assert all(b1 <= a2 for (_, b1), (a2, _) in zip(segs, segs[1:]))


@NEEDS_AUDIO
def test_the_words_scene_carries_letter_timings_inside_each_word():
    ep = episode("kelime")
    ba = next(s for s in ep["scenes"] if s.get("letter") == "ب")
    word0 = next(a for a in ep["audio"] if a["path"].endswith("sekilleri/btn_12.mp3"))
    first, last = ba["spell"][0][0], ba["spell"][0][-1]
    assert word0["t"] <= first[0] and last[1] <= word0["t"] + word0["dur"] + 0.05


@NEEDS_AUDIO
def test_reading_lesson_builds_three_fetha_syllables_into_suitable_words():
    from elifba.storyboard import UNSUITABLE_WORDS

    ep = episode("okuma")
    scenes = [s for s in ep["scenes"] if s["type"] == "build"]
    assert len(scenes) >= 12
    for sc in scenes:
        assert len(sc["syllables"]) == 3 and all(x.endswith("َ") for x in sc["syllables"])
        assert sc["word"] not in UNSUITABLE_WORDS
    assert scenes[3]["word"] == "بَعَثَ" and scenes[3]["syllables"] == ["بَ", "عَ", "ثَ"]
    syl = [a["path"] for a in ep["audio"] if "/fetha/fetha/" in a["path"]]
    assert syl[9:12] == ["audio/elifba/fetha/fetha/btn_2.mp3", "audio/elifba/fetha/fetha/btn_12.mp3",
                         "audio/elifba/fetha/fetha/btn_4.mp3"]   # bâ, ayn, se: each letter's own fetha recording
    assert ep["duration"] <= MAX_SECONDS


def test_markless_font_lays_out_exactly_like_scheherazade():
    """The clay sprites paint the letters over coloured marks with this font.
    It must differ from Scheherazade only in blank mark outlines: any change to
    an advance or a positioning rule shifts the letters off their marks again
    (as stripping the marks from the string did to سَاَلَ)."""
    ttlib = pytest.importorskip("fontTools.ttLib")
    from elifba.markless_font import SOURCE, TARGET, mark_glyphs

    src, out = ttlib.TTFont(SOURCE), ttlib.TTFont(TARGET)
    blank = mark_glyphs(src)
    assert {src.getBestCmap()[cp] for cp in (0x064E, 0x0650, 0x064F, 0x0652, 0x0651)} <= blank
    assert not any(g.startswith(("uni0653", "uni0654")) for g in blank)      # maddah, hamza stay
    assert src["hmtx"].metrics == out["hmtx"].metrics
    assert src["GPOS"].compile(src) == out["GPOS"].compile(out)
    assert src["GSUB"].compile(src) == out["GSUB"].compile(out)
    for name in src.getGlyphOrder():
        if name in blank:
            assert out["glyf"][name].numberOfContours == 0, name
        else:
            assert out["glyf"][name].numberOfContours == src["glyf"][name].numberOfContours, name
    assert "Scheherazade" not in out["name"].getDebugName(1)             # OFL reserved name


def test_script_cursor_advances_past_waits():
    s = Script()
    s.wait(1.5)
    assert s.t == 1.5
