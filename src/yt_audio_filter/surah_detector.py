"""Detect Quran surah / well-known passage names from free-form text.

Matches canonical transliterations and common spelling variants, returning the
first match. Case-insensitive, punctuation-tolerant.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class SurahMatch:
    name: str          # Canonical English transliteration (e.g., "At-Tin")
    tag: str           # PascalCase hashtag-safe form (e.g., "AtTin")
    number: Optional[int]  # 1-114 for surahs; None for named passages (Ayatul Kursi)


@dataclass(frozen=True)
class SurahInfo:
    """Canonical data for a numbered surah (1..114)."""

    name: str   # Canonical English transliteration, e.g. "Al-Fatiha"
    tag: str    # PascalCase hashtag-safe form, e.g. "AlFatiha"
    number: int  # 1..114


# (canonical_name, surah_number_or_None, alias_patterns_without_anchors)
# Patterns use \s*-?\s* between "Al"/"Ar"/"An"/"At" prefixes and the name stem.
_SURAHS: List[Tuple[str, Optional[int], List[str]]] = [
    ("Al-Fatiha",      1,  [r"al\W*faat?ih?a", r"fatih?ah?"]),
    ("Al-Baqarah",     2,  [r"al\W*baqara(h|t)?"]),
    ("Al-Imran",       3,  [r"al?\W*[i']?mraan?", r"aal\W*[i']?mraan?"]),
    ("An-Nisa",        4,  [r"an?\W*nisaa?[’']?"]),
    ("Al-Maidah",      5,  [r"al\W*maa?[’']?ida(h)?"]),
    ("Al-Anam",        6,  [r"al\W*an[’']?aa?m"]),
    ("Al-Araf",        7,  [r"al\W*a[’']?raa?f"]),
    ("Al-Anfal",       8,  [r"al\W*anfaa?l"]),
    ("At-Tawbah",      9,  [r"at\W*taw(a|u)?bah?", r"bara[’']?ah"]),
    ("Yunus",          10, [r"(?<![a-z])yu[ou]nus(?![a-z])"]),
    ("Hud",            11, [r"(?<![a-z])huu?d(?![a-z])"]),
    ("Yusuf",          12, [r"yu[ou]suf"]),
    ("Ar-Rad",         13, [r"ar?\W*ra[’']?d"]),
    ("Ibrahim",        14, [r"ibraa?heem?", r"ibrahim"]),
    ("Al-Hijr",        15, [r"al\W*hijr"]),
    ("An-Nahl",        16, [r"an\W*nahl"]),
    ("Al-Isra",        17, [r"al\W*israa?[’']?", r"bani\W*israa?[’']?il"]),
    ("Al-Kahf",        18, [r"al\W*kahf"]),
    ("Maryam",         19, [r"mary?am"]),
    ("Ta-Ha",          20, [r"taa?[\W]*haa?"]),
    ("Al-Anbiya",      21, [r"al\W*anbiyaa?[’']?"]),
    ("Al-Hajj",        22, [r"al\W*hajj"]),
    ("Al-Muminun",     23, [r"al\W*mu[’']?minuu?n"]),
    ("An-Nur",         24, [r"an\W*nuu?r"]),
    ("Al-Furqan",      25, [r"al\W*furqaa?n"]),
    ("Ash-Shuara",     26, [r"ash?\W*shu[’']?araa?[’']?"]),
    ("An-Naml",        27, [r"an\W*naml"]),
    ("Al-Qasas",       28, [r"al\W*qas[ae]s"]),
    ("Al-Ankabut",     29, [r"al\W*[’']?ankabuu?t"]),
    ("Ar-Rum",         30, [r"ar?\W*ruu?m"]),
    ("Luqman",         31, [r"luqmaa?n"]),
    ("As-Sajdah",      32, [r"as\W*sajda(h)?"]),
    ("Al-Ahzab",       33, [r"al\W*ahzaa?b"]),
    ("Saba",           34, [r"(?<![a-z])saba[’']?(?![a-z])"]),
    ("Fatir",          35, [r"(?<![a-z])faa?tir(?![a-z])"]),
    ("Ya-Sin",         36, [r"y(a|ā)a?\W*seen?", r"yaseen", r"yasin"]),
    ("As-Saffat",      37, [r"as\W*saa?ffaa?t"]),
    ("Sad",            38, [r"(?<![a-z])sa{1,2}d(?![a-z])"]),
    ("Az-Zumar",       39, [r"az\W*zumar"]),
    ("Ghafir",         40, [r"\bghaa?fir\b", r"al\W*mu[’']?min"]),
    ("Fussilat",       41, [r"fussilaa?t"]),
    ("Ash-Shura",      42, [r"ash?\W*shuu?raa?"]),
    ("Az-Zukhruf",     43, [r"az\W*zukhruf"]),
    ("Ad-Dukhan",      44, [r"ad\W*dukhaa?n"]),
    ("Al-Jathiyah",    45, [r"al\W*jaathiya(h)?"]),
    ("Al-Ahqaf",       46, [r"al\W*ahqaa?f"]),
    ("Muhammad",       47, [r"\bmu[hḥ]?ammad\b"]),
    ("Al-Fath",        48, [r"al\W*fath"]),
    ("Al-Hujurat",     49, [r"al\W*hujuraa?t"]),
    ("Qaf",            50, [r"(?<![a-z])qaa?f(?![a-z])"]),
    ("Adh-Dhariyat",   51, [r"adh?\W*dhaariyaa?t"]),
    ("At-Tur",         52, [r"at\W*tuu?r"]),
    ("An-Najm",        53, [r"an\W*najm"]),
    ("Al-Qamar",       54, [r"al\W*qamar"]),
    ("Ar-Rahman",      55, [r"ar?\W*ra[h]?maa?n"]),
    ("Al-Waqiah",      56, [r"al\W*waa?qi[’']?a(h)?"]),
    ("Al-Hadid",       57, [r"al\W*hadi[i]?d"]),
    ("Al-Mujadilah",   58, [r"al\W*mujaadi?la(h)?"]),
    ("Al-Hashr",       59, [r"al\W*hashr"]),
    ("Al-Mumtahanah",  60, [r"al\W*mumtah[ai]na(h)?"]),
    ("As-Saff",        61, [r"as\W*saff"]),
    ("Al-Jumuah",      62, [r"al\W*jumu[’']?a(h)?"]),
    ("Al-Munafiqun",   63, [r"al\W*munaa?fiquu?n"]),
    ("At-Taghabun",    64, [r"at\W*taghaa?bun"]),
    ("At-Talaq",       65, [r"at\W*tala[aā]?q"]),
    ("At-Tahrim",      66, [r"at\W*tahri[i]?m"]),
    ("Al-Mulk",        67, [r"al\W*mulk"]),
    ("Al-Qalam",       68, [r"al\W*qalam"]),
    ("Al-Haqqah",      69, [r"al\W*haa?qqa(h)?"]),
    ("Al-Maarij",      70, [r"al\W*ma[’']?aarij"]),
    ("Nuh",            71, [r"(?<![a-z])nu[u]?h(?![a-z])"]),
    ("Al-Jinn",        72, [r"al\W*jinn"]),
    ("Al-Muzzammil",   73, [r"al\W*muzzamm?il"]),
    ("Al-Muddaththir", 74, [r"al\W*muddath?thir"]),
    ("Al-Qiyamah",     75, [r"al\W*qiya[a]?ma(h)?"]),
    ("Al-Insan",       76, [r"al\W*insaa?n", r"ad?\W*dahr"]),
    ("Al-Mursalat",    77, [r"al\W*mursalaa?t"]),
    ("An-Naba",        78, [r"an\W*naba[’']?"]),
    ("An-Naziat",      79, [r"an\W*naazi[’']?aa?t"]),
    ("Abasa",          80, [r"(?<![a-z])[’']?abasa(?![a-z])"]),
    ("At-Takwir",      81, [r"at\W*takwi[i]?r"]),
    ("Al-Infitar",     82, [r"al\W*infitaa?r"]),
    ("Al-Mutaffifin",  83, [r"al\W*mutaffif[ei]en?"]),
    ("Al-Inshiqaq",    84, [r"al\W*inshiqaa?q"]),
    ("Al-Buruj",       85, [r"al\W*buruu?j"]),
    ("At-Tariq",       86, [r"at\W*taariq"]),
    ("Al-Ala",         87, [r"al\W*a[’']?laa?"]),
    ("Al-Ghashiyah",   88, [r"al\W*ghaashiya(h)?"]),
    ("Al-Fajr",        89, [r"al\W*fajr"]),
    ("Al-Balad",       90, [r"al\W*balad"]),
    ("Ash-Shams",      91, [r"ash?\W*shams"]),
    ("Al-Layl",        92, [r"al\W*layl"]),
    ("Ad-Duha",        93, [r"ad?\W*duhaa?"]),
    ("Ash-Sharh",      94, [r"ash?\W*sharh", r"al\W*inshirah"]),
    ("At-Tin",         95, [r"at\W*ti[i]?n", r"\btin\b"]),
    ("Al-Alaq",        96, [r"al\W*[’']?alaq"]),
    ("Al-Qadr",        97, [r"al\W*qadr"]),
    ("Al-Bayyinah",    98, [r"al\W*bayyina(h)?"]),
    ("Az-Zalzalah",    99, [r"az\W*zalzala(h)?"]),
    ("Al-Adiyat",      100,[r"al\W*[’']?aadiyaa?t"]),
    ("Al-Qariah",      101,[r"al\W*qaari[’']?a(h)?"]),
    ("At-Takathur",    102,[r"at\W*takaathur"]),
    ("Al-Asr",         103,[r"al\W*[’']?asr"]),
    ("Al-Humazah",     104,[r"al\W*humaza(h)?"]),
    ("Al-Fil",         105,[r"al\W*fi[i]?l"]),
    ("Quraysh",        106,[r"quraa?ysh"]),
    ("Al-Maun",        107,[r"al\W*maa?[’']?uu?n"]),
    ("Al-Kawthar",     108,[r"al\W*kawthar", r"al\W*kauthar"]),
    ("Al-Kafirun",     109,[r"al\W*kaafiruu?n"]),
    ("An-Nasr",        110,[r"an\W*nasr"]),
    ("Al-Masad",       111,[r"al\W*masad", r"al\W*lahab"]),
    ("Al-Ikhlas",      112,[r"al\W*ikhlaa?s"]),
    ("Al-Falaq",       113,[r"al\W*falaq"]),
    ("An-Nas",         114,[r"an\W*naa?s"]),
    # Well-known named passages (not whole surahs)
    ("Ayatul Kursi",   None, [r"aya[ht]u?l\W*kurs[ei]", r"aya[ht]\W*al\W*kurs[ei]"]),
    ("Ayat al-Kursi",  None, []),  # alias resolved to Ayatul Kursi by regex above
]


def _slug_tag(name: str) -> str:
    """PascalCase tag: strip punctuation, Title-case parts, join."""
    parts = re.split(r"[\s\-]+", name.strip())
    return "".join(p[:1].upper() + p[1:].lower() for p in parts if p)


_COMPILED: List[Tuple[str, Optional[int], List[re.Pattern]]] = [
    (name, number, [re.compile(p, re.IGNORECASE) for p in patterns])
    for name, number, patterns in _SURAHS
    if patterns
]


def detect_surah(text: str) -> Optional[SurahMatch]:
    """Return the first surah / named passage matched in `text`, or None."""
    if not text:
        return None
    for name, number, patterns in _COMPILED:
        for p in patterns:
            if p.search(text):
                return SurahMatch(name=name, tag=_slug_tag(name), number=number)
    return None


def detect_all_surahs(text: str) -> List[SurahMatch]:
    """Return all surah / named-passage matches in `text`, deduplicated by canonical name.

    Used to score the "cleanliness" of a candidate title: a compilation titled
    "Surah X - Surah Y" returns two matches; a clean single-surah title returns one.
    """
    if not text:
        return []
    seen: set = set()
    results: List[SurahMatch] = []
    for name, number, patterns in _COMPILED:
        if name in seen:
            continue
        for p in patterns:
            if p.search(text):
                seen.add(name)
                results.append(SurahMatch(name=name, tag=_slug_tag(name), number=number))
                break
    return results


# Well-known Quran reciters. Patterns are case-insensitive substring matches.
# Canonical names use the most widely recognized English transliteration.
_RECITERS: List[Tuple[str, List[str]]] = [
    ("Mishary Rashid Alafasy",     [r"mishary(\W*rashid)?(\W*al[- ]?[’']?afasy)?", r"alafasy"]),
    ("Abdur-Rahman As-Sudais",     [r"as[- ]?sudais", r"abdu[rl][- ]?rahman\W*(as\W*)?sudais", r"\bsudais\b"]),
    ("Saud Al-Shuraim",            [r"al[- ]?shuraim", r"sa[’']?ud.*shuraim"]),
    ("Maher Al-Muaiqly",           [r"al[- ]?mu[’']?aiqly", r"maa?her.*mu[’']?aiqly"]),
    ("Ahmed Al-Ajmi",              [r"al[- ]?[’']?ajmi", r"ahm?ed.*[’']?ajmi"]),
    ("Abdul Basit Abdul Samad",    [r"abdul?\W*basit", r"abd\W*al\W*basit"]),
    ("Muhammad Siddiq Al-Minshawi",[r"al[- ]?minshawi", r"minshaa?wi"]),
    ("Salim Bahanan",              [r"sali?m\W*bah?ana?n"]),
    ("Muhammad Thaha Al-Junayd",   [r"(muhammad\W*)?thah?a\W*al[- ]?junayd", r"muhammad\W*thah?a"]),
    ("Hani Ar-Rifai",              [r"haa?ni\W*ar[- ]?rifa[a']?i", r"rifa[a']?i"]),
    ("Saad Al-Ghamdi",             [r"al[- ]?ghaa?mdi", r"saa?[’']?d.*ghaa?mdi"]),
    ("Nasser Al-Qatami",           [r"al[- ]?qatami", r"nasser.*qatami"]),
    ("Yasser Ad-Dosari",           [r"ad?[- ]?dosari", r"yasser.*dosari"]),
    ("Idris Abkar",                [r"idris?\W*abkar"]),
    ("Khalid Al-Jalil",            [r"al[- ]?jalil", r"khali?d.*jalil"]),
    ("Fares Abbad",                [r"fares?\W*abbaa?d"]),
    ("Muhammad Al-Luhaidan",       [r"al[- ]?luhaa?ydan"]),
    ("Ali Jaber",                  [r"ali\W*jaber", r"ali\W*jabir"]),
]


_COMPILED_RECITERS: List[Tuple[str, List[re.Pattern]]] = [
    (name, [re.compile(p, re.IGNORECASE) for p in patterns])
    for name, patterns in _RECITERS
]


@dataclass
class ReciterMatch:
    name: str   # Canonical English transliteration (e.g., "Salim Bahanan")
    tag: str    # PascalCase hashtag-safe form (e.g., "SalimBahanan")


def detect_reciter(text: str) -> Optional[ReciterMatch]:
    """Return the first known reciter matched in `text`, or None."""
    if not text:
        return None
    for name, patterns in _COMPILED_RECITERS:
        for p in patterns:
            if p.search(text):
                return ReciterMatch(name=name, tag=_slug_tag(name))
    return None


def get_surah_info(number: int) -> SurahInfo:
    """Look up canonical surah name + tag by number (1..114).

    Raises ``ValueError`` if the number is outside 1..114 or doesn't match a
    numbered surah entry (named passages like Ayatul Kursi have number=None
    and are therefore unreachable through this helper — use ``detect_surah``
    for free-form text instead).
    """
    if not isinstance(number, int) or isinstance(number, bool):
        raise ValueError(f"surah number must be an int, got {type(number).__name__}")
    if number < 1 or number > 114:
        raise ValueError(f"surah number {number} is out of range; must be 1..114")
    for name, num, _patterns in _SURAHS:
        if num == number:
            return SurahInfo(name=name, tag=_slug_tag(name), number=number)
    raise ValueError(f"No canonical entry for surah number {number}")
    return None
