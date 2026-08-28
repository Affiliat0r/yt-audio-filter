"""Parsing a one-line production request into work items.

The whole point of the command is that you type what you want the way you
would say it:

    niloya, Quran (AtTakathur - AnNaas, Ghamdi, background: toy factory with
    train, not scary), riko, abc alfabet

Two things make this harder than a `split(",")`: the Quran item contains its
own commas inside parentheses, and the surah and reciter names arrive in
whatever transliteration the user happens to use. `AnNaas` and `An-Nas` are the
same surah; `Ghamdi` is `Saad Al-Ghamdi`.
"""

from __future__ import annotations

import pytest

from yt_audio_filter.workflow import (
    CartoonItem,
    QuranItem,
    WorkflowParseError,
    normalise_name,
    parse_request,
    parse_surah_ref,
)

EXAMPLE = (
    "niloya, Quran (AtTakathur - AnNaas, Ghamdi, "
    "background: toy factory with train, not scary), riko, abc alfabet"
)


# ------------------------------------------------------------ splitting


def test_the_example_splits_into_four_items() -> None:
    """Commas inside the parentheses must not split the Quran item."""
    items = parse_request(EXAMPLE)
    assert len(items) == 4
    assert [type(i).__name__ for i in items] == [
        "CartoonItem",
        "QuranItem",
        "CartoonItem",
        "CartoonItem",
    ]


def test_cartoon_items_keep_their_search_text() -> None:
    items = parse_request(EXAMPLE)
    assert items[0].query == "niloya"
    assert items[2].query == "riko"
    assert items[3].query == "abc alfabet"


def test_blank_items_are_ignored() -> None:
    assert len(parse_request("niloya, , riko,")) == 2


def test_an_empty_request_is_an_error() -> None:
    with pytest.raises(WorkflowParseError):
        parse_request("   ")


# ------------------------------------------------------------- counts


def test_a_bare_item_means_one_video() -> None:
    assert parse_request("niloya")[0].count == 1


@pytest.mark.parametrize("text", ["niloya x3", "niloya X3", "niloya  x 3"])
def test_xn_sets_the_count(text: str) -> None:
    item = parse_request(text)[0]
    assert item.count == 3
    assert item.query == "niloya", "the xN suffix must not leak into the search"


def test_a_trailing_x_without_a_number_stays_part_of_the_name() -> None:
    """`abc x` is a search for "abc x", not a malformed count."""
    item = parse_request("abc x")[0]
    assert item.count == 1
    assert item.query == "abc x"


def test_a_silly_count_is_refused() -> None:
    with pytest.raises(WorkflowParseError, match="between 1 and"):
        parse_request("niloya x999")


# -------------------------------------------------------- surah names


@pytest.mark.parametrize(
    "written,number",
    [
        ("AtTakathur", 102),
        ("at-takathur", 102),
        ("At Takathur", 102),
        ("AnNaas", 114),
        ("An-Nas", 114),
        ("annas", 114),
        ("Al-Ikhlas", 112),
        ("alikhlaas", 112),
        ("114", 114),
    ],
)
def test_surah_names_survive_transliteration(written: str, number: int) -> None:
    assert parse_surah_ref(written) == number


def test_an_unknown_surah_says_so() -> None:
    with pytest.raises(WorkflowParseError, match="Unknown surah"):
        parse_surah_ref("Al-Bogus")


def test_normalise_collapses_the_usual_variation() -> None:
    """Doubled vowels and separators are the two things that differ most."""
    assert normalise_name("An-Naas") == normalise_name("AnNas") == "annas"
    assert normalise_name("At Takathur") == "attakathur"


# --------------------------------------------------------- quran item


def test_the_range_expands_to_every_surah_in_it() -> None:
    item = parse_request(EXAMPLE)[1]
    assert isinstance(item, QuranItem)
    assert item.surah_numbers == list(range(102, 115))


def test_a_descending_range_is_read_as_written() -> None:
    """Reciting An-Nas back to Al-Ikhlas is a legitimate order."""
    item = parse_request("Quran (AnNaas - AlIkhlas, Ghamdi)")[0]
    assert item.surah_numbers == [114, 113, 112]


def test_a_single_surah_needs_no_range() -> None:
    item = parse_request("Quran (Al-Fatiha, Ghamdi)")[0]
    assert item.surah_numbers == [1]


def test_a_comma_list_of_surahs_works_too() -> None:
    item = parse_request("Quran (Al-Fatiha + An-Nas, Ghamdi)")[0]
    assert item.surah_numbers == [1, 114]


def test_the_reciter_is_resolved_to_a_slug() -> None:
    item = parse_request(EXAMPLE)[1]
    assert item.reciter_slug == "ghamdi"


def test_an_unknown_reciter_says_so() -> None:
    with pytest.raises(WorkflowParseError, match="Unknown reciter"):
        parse_request("Quran (Al-Fatiha, Nobody McNobody)")


def test_the_background_becomes_a_search_query() -> None:
    item = parse_request(EXAMPLE)[1]
    assert item.background_query == "toy factory with train"


def test_negative_terms_are_collected() -> None:
    """'not scary' is a filter on the background search, not a search term."""
    item = parse_request(EXAMPLE)[1]
    assert "scary" in item.exclude_terms
    assert "scary" not in item.background_query


def test_several_negatives() -> None:
    item = parse_request(
        "Quran (Al-Fatiha, Ghamdi, background: trains, not scary, not dark)"
    )[0]
    assert set(item.exclude_terms) >= {"scary", "dark"}


def test_a_quran_item_without_a_background_is_allowed() -> None:
    """Then the runner picks from the curated channels instead."""
    item = parse_request("Quran (Al-Fatiha, Ghamdi)")[0]
    assert item.background_query is None


def test_quran_is_case_insensitive_and_tolerates_spacing() -> None:
    for text in ("quran (Al-Fatiha, Ghamdi)", "QURAN(Al-Fatiha, Ghamdi)"):
        assert isinstance(parse_request(text)[0], QuranItem)


def test_an_unclosed_bracket_is_a_clear_error() -> None:
    with pytest.raises(WorkflowParseError, match="closing"):
        parse_request("Quran (Al-Fatiha, Ghamdi")


# ------------------------------------------------------------ playlists


def test_the_playlist_name_comes_from_the_item() -> None:
    items = parse_request(EXAMPLE)
    assert items[0].playlist_name == "Niloya"
    assert items[1].playlist_name == "Quran"
    assert items[3].playlist_name == "Abc Alfabet"


def test_quran_items_share_one_playlist() -> None:
    items = parse_request(
        "Quran (Al-Fatiha, Ghamdi), Quran (An-Nas, Ghamdi)"
    )
    assert items[0].playlist_name == items[1].playlist_name == "Quran"


# ----------------------------------------------------------------- urls


URL = "https://www.youtube.com/watch?v=U_EhMEOolI0"
SHORT_URL = "https://youtu.be/U_EhMEOolI0"


def test_a_pasted_url_becomes_an_item() -> None:
    """Pasting a link beats hoping a search finds the right episode."""
    item = parse_request(URL)[0]
    assert isinstance(item, CartoonItem)
    assert item.url == URL
    assert item.video_id == "U_EhMEOolI0"


def test_a_short_url_works_too() -> None:
    assert parse_request(SHORT_URL)[0].video_id == "U_EhMEOolI0"


def test_urls_mix_with_searches_in_one_request() -> None:
    items = parse_request(f"niloya, {URL}, riko")
    assert [bool(i.url) for i in items] == [False, True, False]
    assert items[1].video_id == "U_EhMEOolI0"


def test_a_url_item_has_no_playlist_of_its_own() -> None:
    """A link carries no name, so the runner names the playlist from the
    video's own channel once it has fetched it."""
    assert parse_request(URL)[0].playlist_name is None


def test_a_label_names_the_playlist() -> None:
    for text in (f"niloya: {URL}", f"niloya -> {URL}", f"niloya - {URL}"):
        item = parse_request(text)[0]
        assert item.playlist_name == "Niloya", text
        assert item.url == URL


def test_the_https_scheme_is_not_mistaken_for_a_label() -> None:
    """`https:` looks exactly like `label:` to a naive split."""
    item = parse_request(URL)[0]
    assert item.playlist_name is None
    assert item.url == URL


def test_a_url_can_be_repeated_but_that_is_pointless_so_count_stays_one() -> None:
    """xN on a fixed link would render the same video N times."""
    with pytest.raises(WorkflowParseError, match="already names one video"):
        parse_request(f"{URL} x3")


def test_a_non_youtube_url_is_refused() -> None:
    with pytest.raises(WorkflowParseError, match="YouTube"):
        parse_request("https://vimeo.com/12345")


def test_a_url_with_extra_query_params_still_parses() -> None:
    item = parse_request(f"{URL}&list=PLabc&index=2")[0]
    assert item.video_id == "U_EhMEOolI0"


def test_the_count_suffix_does_not_need_a_space() -> None:
    """`niloyax2` is how people actually type it."""
    item = parse_request("niloyax2")[0]
    assert item.count == 2
    assert item.query == "niloya"
