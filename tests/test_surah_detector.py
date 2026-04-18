"""Unit tests for yt_audio_filter.surah_detector."""

from yt_audio_filter.surah_detector import detect_reciter, detect_surah


def test_detect_ayatul_kursi_uppercase() -> None:
    m = detect_surah("SALIM BAHANAN || AYATUL KURSI || BEAUTIFUL VOICE")
    assert m is not None
    assert m.name == "Ayatul Kursi"
    assert m.tag == "AyatulKursi"
    assert m.number is None


def test_detect_ar_rahman_with_hyphen() -> None:
    m = detect_surah("Surah Ar-Rahman - Salim Bahanan")
    assert m is not None
    assert m.name == "Ar-Rahman"
    assert m.number == 55


def test_detect_at_tin() -> None:
    m = detect_surah("Surah At-Tin recitation")
    assert m is not None
    assert m.name == "At-Tin"
    assert m.number == 95


def test_detect_fatiha() -> None:
    m = detect_surah("Surah Al-Fatiha")
    assert m is not None
    assert m.name == "Al-Fatiha"
    assert m.number == 1


def test_detect_yaseen_alt_spelling() -> None:
    m = detect_surah("Yaseen - Mishary Rashid")
    assert m is not None
    assert m.name == "Ya-Sin"
    assert m.number == 36


def test_detect_kahf_without_hyphen() -> None:
    m = detect_surah("Al Kahf full")
    assert m is not None
    assert m.name == "Al-Kahf"
    assert m.number == 18


def test_no_match_returns_none() -> None:
    assert detect_surah("Just some random video title") is None


def test_empty_string_returns_none() -> None:
    assert detect_surah("") is None


def test_matches_first_occurrence() -> None:
    m = detect_surah("Playlist: Al-Fatiha then Al-Baqarah")
    assert m is not None
    assert m.name == "Al-Fatiha"


def test_tag_is_pascal_case_no_punctuation() -> None:
    m = detect_surah("Ayatul Kursi")
    assert m is not None
    assert m.tag == "AyatulKursi"
    m2 = detect_surah("Al-Fatiha")
    assert m2 is not None
    assert m2.tag == "AlFatiha"


def test_detect_reciter_salim_bahanan() -> None:
    r = detect_reciter("SALIM BAHANAN || AYATUL KURSI")
    assert r is not None
    assert r.name == "Salim Bahanan"
    assert r.tag == "SalimBahanan"


def test_detect_reciter_sudais() -> None:
    r = detect_reciter("Surah Al-Kahf by Sudais")
    assert r is not None
    assert r.name == "Abdur-Rahman As-Sudais"


def test_detect_reciter_alafasy() -> None:
    r = detect_reciter("Mishary Rashid Alafasy - Yaseen")
    assert r is not None
    assert r.name == "Mishary Rashid Alafasy"


def test_detect_reciter_no_match() -> None:
    assert detect_reciter("Random uploader channel") is None


def test_detect_reciter_empty() -> None:
    assert detect_reciter("") is None
