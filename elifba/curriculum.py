"""The Elif-Ba curriculum, and which recording belongs to which tile.

Ported from Diyanet's "Kur'an Öğreniyorum" web course on kuran.diyanet.gov.tr/elifba,
whose every tile has a human recording at

    /elifba/data/sound/<part>/<chapter>/<lesson>/<sound>.mp3

The site builds its lessons with a handful of small helpers that number the
sounds (``btn_1`` .. ``btn_N``) by position; the helpers below are those, ported
one for one, so a tile here plays exactly the recording the site plays for it.
The Arabic is copied verbatim from the site, including its Turkish-mushaf marks
(U+06EA for the long kesra, U+0670 for the dagger alif).

Units 1-13 are the Elif-Ba part; they cover the same ground as Diyanet's printed
Elif-Ba (2024) — letters, their forms, fetha / kesra / damme, their lengthening and
tenvin, cezm, şedde — taught one hareke at a time.

    python -m elifba.curriculum > elifba/data/curriculum.json
"""

from __future__ import annotations

import json
import sys
from typing import Dict, List, Optional, Sequence

SOUND_ROOT = "https://kuran.diyanet.gov.tr/elifba/data/sound"
PART = "elifba"

THIN = "İnce Sesli Harfler"
THICK = "Kalın Sesli Harfler"

# Letter sets, as the site orders them.
HE = ["ا", "ب", "ت", "ث", "ج", "ح", "د", "ذ", "ز", "س", "ش", "ع", "ف", "ك", "ل", "م", "ن", "هـ", "و", "ى"]
BE = ["خ", "ر", "غ", "ق", "ص", "ض", "ط", "ظ"]
EE = ["ا", "ب", "ت", "ث", "ج", "ح", "د", "ذ", "ر", "ز", "س", "ش", "ع", "ف", "ك", "ل", "م", "ن", "هـ", "و", "ى"]
MN = ["خ", "ص", "ض", "ط", "ظ", "غ", "ق"]
KE = [c for c in BE if c != "ر"]
JE = ["ا", "ب", "ت", "ث", "ج", "ح", "خ", "د", "ذ", "ر", "ز", "س", "ش", "ص", "ض", "ط", "ظ", "ع", "غ", "ف", "ق", "ك", "ل", "م", "ن", "هـ", "و", "ى"]

#: Letters read "thick" (kalın). Used to colour them, as the printed Elif-Ba does.
HEAVY = set("خصضطظغق")

Tile = Dict
Row = Dict
Group = Dict


def tile(sound: str, arabic: str, **extra) -> Tile:
    return {"sound": sound, "arabic": arabic, **extra}


# --- the site's helpers ------------------------------------------------------


def ye(thin: Sequence[str], thick: Sequence[str], thin_start: int, thick_start: int,
       ince_title="baslik2", kalin_title="baslik3", kalin_row_gap=0,
       ince_ritmik=True, kalin_ritmik=True) -> List[Group]:
    """Thin letters in rows of five (the last row takes the rest), thick in fours.

    Each row also has a rhythmic reading, ``satir_<n>``, numbered across both groups.
    """
    rows: List[Row] = []
    n_rows = len(thin) // 5 or (1 if thin else 0)
    y = 0
    for f in range(n_rows):
        end = len(thin) if f == n_rows - 1 else y + 5
        rows.append({"ritmikSound": f"satir_{f + 1}",
                     "tiles": [tile(f"btn_{thin_start + y + h}", a) for h, a in enumerate(thin[y:end])]})
        y = end
    thick_rows: List[Row] = []
    gap = 0
    for f in range(0, len(thick), 4):
        u = f // 4
        if u > 0:
            gap += kalin_row_gap
        thick_rows.append({"ritmikSound": f"satir_{n_rows + u + 1}",
                           "tiles": [tile(f"btn_{thick_start + f + m + gap}", a)
                                     for m, a in enumerate(thick[f:f + 4])]})
    if not ince_ritmik:
        for r in rows:
            r.pop("ritmikSound")
    if not kalin_ritmik:
        for r in thick_rows:
            r.pop("ritmikSound")
    return [{"title": THIN, "titleSound": ince_title, "rows": rows},
            {"title": THICK, "titleSound": kalin_title, "rows": thick_rows}]


def be_pairs(start: int, pairs: Sequence[Sequence[str]]) -> List[Row]:
    """A row of letters over a row of their names; letter and name share a sound."""
    return [{"tiles": [tile(f"btn_{start + d}", p[0]) for d, p in enumerate(pairs)]},
            {"tiles": [tile(f"btn_{start + d}", p[1]) for d, p in enumerate(pairs)], "gray": True, "names": True}]


def sets(columns: Sequence[Sequence[str]], start: int, width: int) -> List[Row]:
    """Two or three forms of each letter stacked (short / long / tenvin), one sound each.

    The site's ``kt`` (pairs) and ``pt`` (triples) helpers; one recording reads all
    the forms of a letter in turn.
    """
    rows: List[Row] = []
    n = len(columns[0])
    for y in range(0, n, width):
        for col in columns:
            rows.append({"set": True, "tiles": [tile(f"btn_{start + y + i}", a) for i, a in enumerate(col[y:y + width])]})
    return rows


def windowed_sets(columns: Sequence[Sequence[str]], start: int, windows: Sequence[tuple]) -> List[Row]:
    """The site's ``Le`` / ``Re``: like :func:`sets` but over explicit (offset, count) windows."""
    rows: List[Row] = []
    for off, count in windows:
        for col in columns:
            rows.append({"set": True, "tiles": [tile(f"btn_{start + off + i}", col[off + i]) for i in range(count)
                                                 if off + i < len(col)]})
    return rows


def j(items: Sequence[str], per_row: int, start: int) -> List[Row]:
    return [{"tiles": [tile(f"btn_{start + t + g}", a) for g, a in enumerate(items[t:t + per_row])]}
            for t in range(0, len(items), per_row)]


def lesson(chapter: str, key: str, title: str, groups: List[Group], description: Optional[str] = None,
           description_sound: Optional[str] = "aciklama") -> Dict:
    return {"chapter": chapter, "lesson": key, "title": title, "description": description,
            "descriptionSound": description_sound if description else None, "groups": groups}


def fetha(c: str) -> str:
    return "هَـ" if c == "هـ" else f"{c}َ"


def fetha_long(c: str) -> str:
    return {"هـ": "هَا", "ا": "اٰ", "ى": "يَا"}.get(c, f"{c}َا")


def kesra(c: str) -> str:
    return "هِـ" if c == "هـ" else f"{c}ِ"


def kesra_long(c: str) -> str:
    return {"ا": "ا۪", "هـ": "ه۪ى", "ى": "ي۪ى"}.get(c, f"{c}۪ى")


def damme(c: str) -> str:
    return "هُـ" if c == "هـ" else f"{c}ُ"


# --- Unit 1: Harfler ---------------------------------------------------------

POSITION_HEADERS = [
    tile("harf", "Harf", header=True), tile("basinda", "Başta", header=True),
    tile("ortasinda", "Ortada", header=True), tile("sonunda", "Sonda", header=True),
]

THIN_POSITIONS = [
    ["{ت}", "{ت}رك", "اع{ت}دوا", "سك{ت} جن{ة}"], ["{ث}", "{ث}بت", "م{ث}ل", "رف{ث}"],
    ["{ج}", "{ج}عل", "تع{ج}ب", "الح{ج}"], ["{ح}", "{ح}ذر", "يج{ح}د", "فت{ح}"],
    ["{د}", "{د}خل", "ع{د}د", "وع{د}"], ["{ذ}", "{ذ}را", "ك{ذ}لك", "نب{ذ}"],
    ["{ر}", "{ر}جع", "ف{ر}ض", "ام{ر}"], ["{ز}", "{ز}عم", "ن{ز}ل", "بر{ز}"],
    ["{س}", "{س}ال", "ف{س}جد", "عب{س}"], ["{ش}", "{ش}كر", "ي{ش}عر", "بط{ش}"],
    ["{ع}", "{ع}بد", "ل{ع}ن", "من{ع}"], ["{ف}", "{ف}وقع", "ك{ف}ر", "خل{ف}"],
    ["{ك}", "{ك}سب", "م{ك}ر", "من{ك}"], ["{ل}", "{ل}برز", "خ{ل}ع", "جع{ل}"],
    ["{م}", "{م}ثل", "يط{م}ع", "لم{م}"], ["{ن}", "{ن}ظر", "م{ن}ع", "بط{ن}"],
    ["{هـ}", "{ه}لك", "ف{ه}م", "وج{ه}"], ["{و}", "{و}عد", "ع{و}د", "دل{و}"],
    ["{ى}", "{ي}تيم", "خ{ي}ر", "لق{ى}"],
]
THICK_POSITIONS = [
    ["{خ}", "{خ}لق", "في{خ}رج", "ونف{خ}"], ["{ص}", "{ص}دق", "ن{ص}ف", "نك{ص}"],
    ["{ض}", "{ض}ل", "ت{ض}ع", "ببع{ض}"], ["{ط}", "{ط}بع", "ف{ط}ر", "خل{ط}"],
    ["{ظ}", "{ظ}هر", "ن{ظ}ر", "غل{ظ}"], ["{غ}", "{غ}ضب", "ف{غ}فر", "بل{غ}"],
    ["{ق}", "{ق}دم", "س{ق}ر", "فس{ق}"],
]
LISP_POSITIONS = [
    ["{ث}", "{ث}بت", "م{ث}ل", "رف{ث}"], ["{ذ}", "{ذ}را", "ك{ذ}لك", "نب{ذ}"], ["{ظ}", "{ظ}هر", "ن{ظ}ر", "غل{ظ}"],
]


def position_rows(words: Sequence[Sequence[str]], start: int) -> List[Row]:
    return [{"tiles": [tile(f"btn_{start + n * 4 + s}", w, position=s) for s, w in enumerate(row)]}
            for n, row in enumerate(words)]


def unit_harfler() -> List[Dict]:
    c = "harfler"
    harfler = lesson(c, "harfler", "Harfler", [{
        "ritmikSound": "harfler_ritmik",
        "rows": [{"tiles": [tile(f"btn_{i + 1}", JE[i]) for i in range(a, b)]}
                 for a, b in ((0, 5), (5, 10), (10, 15), (15, 20), (20, 25), (25, 28))],
        "note": "Bazı elif-ba kitaplarında bir harf olarak gösterilen ( لا ) müstakil bir harf olmayıp, "
                "( ل ) ve ( ا ) harflerinden oluşan bir yazım şeklidir.",
        "noteSound": "aciklama",
    }])
    names = [["ا", "الف"], ["ب", "با"], ["ت", "تا"], ["ث", "ثا"], ["ج", "جيم"],
             ["ح", "حا"], ["خ", "خا"], ["د", "دال"], ["ذ", "ذال"], ["ر", "را"],
             ["ز", "زاى"], ["س", "سين"], ["ش", "شين"], ["ص", "صاد"], ["ض", "ضاد"],
             ["ط", "طا"], ["ظ", "ظا"], ["ع", "عين"], ["غ", "غين"], ["ف", "فا"],
             ["ق", "قاف"], ["ك", "كاف"], ["ل", "لام"], ["م", "ميم"], ["ن", "نون"],
             ["هـ", "ها"], ["و", "واو"], ["ى", "يا"]]
    isimleri = lesson(c, "isimleri", "Harfler ve İsimleri", [{
        "type": "names",
        "rows": [r for k in range(0, 28, 5) for r in be_pairs(k + 1, names[k:k + 5])],
    }], description="Aşağıdaki tabloda sarı satırda harflerin şekilleri, beyaz satırda ise isimleri yer "
                    "almaktadır. Harflerin isimlerini uzman rehberliğinde öğreniniz.")
    thin_sound = ["ا", "ب", "ت", "ث", "ج", "ح", "د", "ذ", "ر", "ز", "س", "ش", "ع", "ف", "ك", "ل", "م", "ن", "هـ", "و", "ى"]
    sesleri = lesson(c, "sesleri", "Harfler ve Sesleri", [
        {"title": "İnce Sesli Harfler", "titleSound": "aciklama", "ritmikSound": "ince_ritmik",
         "rows": [{"tiles": [tile(f"btn_{i + 1}", thin_sound[i]) for i in range(a, b)]}
                  for a, b in ((0, 5), (5, 10), (10, 15), (15, 21))]},
        {"title": "Kalın Sesli Harfler", "titleSound": "aciklama2", "ritmikSound": "kalin_ritmik",
         "rows": [{"tiles": [tile(f"btn_{22 + i}", MN[i]) for i in range(a, min(a + 4, len(MN)))]}
                  for a in range(0, len(MN), 4)]},
    ])
    first_rows = [
        {"tiles": [tile("btn_1", "{ا}", position=0), tile("btn_2", "{ا}مر", position=1),
                   tile("btn_3", "س{ا}ل", position=2), tile("btn_4", "نب{ا}", position=3)]},
        {"tiles": [tile("btn_1", "{ء}", position=0), tile("btn_6", "{ء}انذر", position=1),
                   tile("btn_7", "ي{ئ}س", position=2), tile("btn_8", "سو{ء}", position=3)]},
        {"tiles": [tile("btn_1", "{ء}", position=0, repeat=True), tile("btn_9", "{أ}بعث", position=1),
                   tile("btn_10", "ت{ؤ}من", position=2), tile("btn_11", "سي{ئ}", position=3)]},
        {"tiles": [tile("btn_1000", "{ب}", position=0), tile("btn_12", "{ب}عث", position=1),
                   tile("btn_13", "ع{ب}د", position=2), tile("btn_14", "وه{ب}", position=3)]},
    ]
    sekilleri = lesson(c, "sekilleri", "Harfler ve Şekilleri", [
        {"title": "İnce Sesli Harfler", "titleSound": "aciklama2", "type": "positions",
         "headers": POSITION_HEADERS, "rows": first_rows + position_rows(THIN_POSITIONS, 15)},
        {"title": "Kalın Sesli Harfler", "titleSound": "aciklama3", "type": "positions",
         "headers": POSITION_HEADERS, "rows": position_rows(THICK_POSITIONS, 91)},
        {"title": "Peltek Harfler", "titleSound": "peltek", "type": "positions", "headers": POSITION_HEADERS,
         "preNote": "Arapça'da harfler kalın ve ince olmak üzere ikiye ayrılır. Bu harflerin üç tanesi peltek "
                    "olarak telaffuz edilir. Peltek harflerin iki tanesi ince, bir tanesi ise kalın seslidir.",
         "preNoteSound": "peltekaciklama", "rows": position_rows(LISP_POSITIONS, 119)},
    ], description="Arapça'da harfler, kelimede bulunduğu yere göre (başta – ortada – sonda) şekil olarak "
                   "bazı küçük farklılıklar taşır. Bu farklılıkları kavrama çalışması, Kur’an kelimeleri "
                   "üzerinde yapılmalı ve şekil itibariyle harfler tam olarak öğrenilmeden diğer adımlara "
                   "geçilmemelidir.")
    pekistirme = lesson(c, "pekistirme", "Pekiştirme", [{"rows": j(
        ["{هؤلاء}", "يؤمن", "نسئ", "{فقد}", "نبذ", "فغوى", "{برز}", "ثمر", "شجر", "{سحر}", "حجر", "صبر",
         "{جثيا}", "يشكر", "ينفق", "{ضحى}", "طلق", "لظى", "{يتلو}", "فخار", "فلق", "{هلك}", "ملك", "اوفوا",
         "{قتل}", "تنفذ", "ينبغى", "{غضب}", "لاتينا", "يبصرون", "{اليم}", "تاويلا", "دون", "{قدحا}", "ذلك",
         "يذكر", "{رضى}", "يرسل", "زنيم", "{يزكى}", "ودعك", "موعد"], 3, 1)}],
        description="Şekil olarak benzeyen harfler, nokta sayısına ve noktanın harfin neresinde yer aldığına "
                    "dikkat edildiğinde kolaylıkla ayırt edilebilir. Bu durumu da göz önünde bulundurarak, "
                    "aşağıdaki kelimelerde yer alan harflerin isimlerini söyleyiniz.")
    return [harfler, isimleri, sesleri, sekilleri, pekistirme]


# --- Units 2-4: fetha --------------------------------------------------------


def unit_fetha() -> List[Dict]:
    c = "fetha"
    return [
        lesson(c, "fetha", "Fetha", ye([fetha(x) for x in HE], [f"{x}َ" for x in BE], 1, 25,
                                       ince_title="aciklama2", kalin_title="aciklama3", kalin_row_gap=1,
                                       kalin_ritmik=False),
               description='Fetha hareke, harfin üstünde yer alır ve harflere "e" sesi verir. Aşağıdaki '
                           "harfleri önce tek tek, daha sonra ise beşli guruplar halinde ritmik usulle, "
                           "uzatmadan okuyunuz."),
        lesson(c, "uygulama", "Fetha Uygulama", [{"rows": j(
            ["اَمَرَ", "سَاَلَ", "ثَمَرَ", "بَعَثَ", "نَبَذَ", "وَهَبَ", "سَكَتَ", "بَدَاَ", "فَتَحَ", "تَرَكَ",
             "خَتَمَ", "عَدَدَ", "جَعَلَ", "مَثَلَ", "رَفَثَ", "حَذَرَ", "شَجَرَ", "عَبَدَ", "خَلَقَ", "اَحَدَ",
             "وَعَدَ", "ذَرَاَ", "نَزَلَ", "كَسَبَ", "دَخَلَ", "رَجَعَ", "شَرَحَ", "وَجَهَ", "فَرَضَ", "عَبَسَ",
             "بَلَغَ", "زَعَمَ", "صَدَقَ", "هَلَكَ", "لَعَنَ", "غَضَبَ", "بَطَشَ", "ضَرَبَ", "حَضَرَ", "عَرَضَ",
             "طَبَعَ", "نَكَصَ", "خَلَطَ", "ظَهَرَ", "مَكَرَ", "غَلَظَ", "عَشَرَ", "كَفَرَ", "فَطَرَ", "قَدَمَ",
             "سَقَرَ", "جَرَمَ", "بَطَنَ", "مَنَعَ", "مَثَلَ", "نَظَرَ", "فَصَلَ", "وَاَخَذَ", "وَجَمَعَ", "فَفَسَقَ",
             "فَصَرَفَ", "وَنَفَخَ", "فَحَشَرَ", "ظَلَمَكَ", "فَسَجَدَ", "لَذَهَبَ", "فَوَقَعَ", "فَخَلَفَ", "لَجَعَلَ",
             "فَغَفَرَ", "فَنَظَرَ", "سَاَلَكَ", "لَبَرَزَ", "فَخَرَجَ", "فَهَزَمَ", "وَكَفَرَ", "عَقَبَةَ", "فَعَدَلَكَ",
             "وَوَجَدَكَ", "جَعَلَ لَكَ"], 4, 1)}],
            description="Aşağıdaki kelimeleri ritmik usulle okuyunuz."),
    ]


def _fetha_long_both(i: int, c: str) -> str:
    if i == 0:
        return "ءَا-اٰ"
    return {"هـ": "هَا-هٰـ", "ى": "يَا-يٰ"}.get(c, f"{c}َا-{c}ٰ")


def unit_fethanin_uzatilmasi() -> List[Dict]:
    c = "fethanin-uzatilmasi"
    return [
        lesson(c, "fethanin-uzatilmasi", "Fetha'nın Uzatılması",
               ye([_fetha_long_both(i, x) for i, x in enumerate(HE)], [f"{x}َا-{x}ٰ" for x in BE], 1, 21),
               description="Fetha harekeli harfin uzatılarak okunması gerektiği, sol yanında bulunan "
                           "**harekesiz elif harfi**nden veya harfin üzerindeki **dikey fethadan** anlaşılır. "
                           'Fetha uzatıldığında **"e"** sesi **"â"** sesine dönüştürülür.'),
        lesson(c, "karsilastirma", "Karşılaştırma", [
            {"title": THIN, "titleSound": "baslik2", "type": "sets",
             "rows": sets([[fetha(x) for x in HE], [fetha_long(x) for x in HE]], 1, 5)},
            {"title": THICK, "titleSound": "baslik3", "type": "sets",
             "rows": sets([[f"{x}َ" for x in BE], [f"{x}َا" for x in BE]], 21, 4)},
        ], description="Aşağıdaki fetha harekeli harfleri önce tek tek, daha sonra beşli gruplar halinde "
                       "**ritmik** usulle, **uzatarak** okuyunuz. Bir harfin iki konumunu okuduktan sonra "
                       "diğer harfe geçiniz."),
        lesson(c, "uygulama", "Uygulama", [{"rows": j(
            ["كَمَا", "جَوَابَ", "بَلَغَا", "كَلَامَ", "لَكَانَ", "اَحَاطَ", "اَخَانَا", "وَاثَابَ", "رَمَضَانَ",
             "وَقَاتَلَ", "فَسَاهَمَ", "فَوَجَدَا", "لَفَسَدَتَا", "مَا كَتَبَ", "فَلَا رَفَثَ", "فَمَا جَعَلَ",
             "قَالَ مَعَاذَ", "وَمَا مَنَعَنَا", "خَافَ عَذَابَ", "وَضَرَبَ لَنَا", "فَمَا كَانَ", "جَعَلَ لَكَ",
             "جَاوَزَا قَالَ", "قَالَ لَا تَخَافَا", "بَلٰى", "عَسٰى", "عَلٰى", "فَهَدٰى", "فَاٰوٰى", "دَحٰيهَا",
             "قَضٰيهَا", "فَنَادٰيهَا", "مَا سَعٰى", "وَمَا بَنٰيهَا", "وَمَا قَلٰى", "وَمَا طَحٰيهَا", "يَتَوٰرٰى",
             "مَتٰى هٰذَا", "حَيٰوةَ", "صَلٰوةَ", "زَكٰوةَ", "وَمَنٰوةَ", "تَظٰهَرَا", "مَا عٰهَدَ", "وَهٰمٰنَ",
             "جٰهَدَاكَ", "تَبٰرَكَ", "لَنَا خَطٰيٰنَا"], 4, 1)}],
            description="Aşağıdaki kelimeleri fetha harekenin uzatılmayan ve uzatılan konumlarına dikkat "
                        "ederek ve sesinizi güzel kullanarak okuyunuz."),
    ]


FETHA_TENVIN = ["اً", "بًا", "تًا", "ثًا", "جًا", "حًا", "دًا", "ذًا", "زًا", "سًا", "شًا", "عًا", "فًا", "كًا",
                "لاً", "مًا", "نًا", "هًا", "وًا", "يًا"]
FETHA_TENVIN_THICK = ["خًا", "رًا", "غًا", "قًا", "صًا", "ضًا", "طًا", "ظًا"]


def unit_fetha_tenvin() -> List[Dict]:
    c = "fetha-tenvin"
    return [
        lesson(c, "fetha-tenvin", "Fetha'nın Tenvini (İki Fetha)", ye(FETHA_TENVIN, FETHA_TENVIN_THICK, 1, 21),
               description='Tenvin, bir harfin sesine **"n"** sesi ilave etmektir. Fetha\'daki **"e"** sesi '
                           'tenvinle **"en"** sesine dönüşür.'),
        lesson(c, "karsilastirma", "Karşılaştırma", [
            {"title": THIN, "titleSound": "baslik2", "type": "sets",
             "rows": sets([[fetha(x) for x in HE], [fetha_long(x) for x in HE], FETHA_TENVIN], 1, 5)},
            {"title": THICK, "titleSound": "baslik3", "type": "sets",
             "rows": sets([[f"{x}َ" for x in BE], [f"{x}َا" for x in BE], FETHA_TENVIN_THICK], 21, 4)},
        ], description="Fetha'nın uzatılmayan, uzatılan ve tenvinli konumlarını, aralarındaki farklılıkları "
                       "gözlemleyerek, **ritmik** usulle okuyunuz. Bir harfin üç konumunu okuduktan sonra "
                       "diğer harfe geçiniz."),
        lesson(c, "uygulama", "Uygulama", [{"rows": j(
            ["مَرَحًا", "سَوَاءً", "خَطَاً", "مَطَرًا", "زَ بَدًا", "حَرَسًا", "قَصَصًا", "مَالًا", "حَرَجًا",
             "مَفَازًا", "جَنَفًا", "غَدَقًا", "دَرَكًا", "سَكَنًا", "سَفَهًا", "زَكٰوةً", "وَقَارًا", "ضَلٰلًا",
             "مَعَاشًا", "شَطَطًا", "طَبَقًا", "وَحَنَانًا", "كَانَ عَرَضًا", "هٰذَا لَبَلَاغًا"], 3, 1)}],
            description="Aşağıdaki kelimeleri fethanın uzatılmayan, uzatılan ve tenvinli konumlarına dikkat "
                        "ederek uzman rehberliğinde okuyunuz."),
    ]


# --- Units 5-7: kesra --------------------------------------------------------

KESRA_WINDOWS_THIN = [(0, 5), (5, 5), (10, 5), (15, 6)]
KESRA_WINDOWS_THICK = [(0, 3), (3, 4)]


def unit_kesra() -> List[Dict]:
    c = "kesra"
    groups = ye([kesra(x) for x in EE], [f"{x}ِ" for x in KE], 1, 22, kalin_title="aciklama2",
                ince_ritmik=False, kalin_ritmik=False)
    groups[1]["preNote"] = 'Kalın harfleri "i" sesi ile okurken, harfin kalınlık özelliğini bozmamaya özen gösteriniz.'
    return [
        lesson(c, "kesra", "Kesra", groups,
               description='Kesra hareke harfin altında yer alır ve bütün harflere "i" sesi verir. Tablodaki '
                           "kesra harekeli harfleri uzatmadan, önce tek tek, daha sonra beşli gruplar halinde "
                           "ritmik usulle, uzatmadan okuyunuz."),
        lesson(c, "uygulama", "Uygulama", [{"rows": j(
            ["بِهَا", "اِلٰى", "اَمِنَ", "ضِرَارًا", "ءَاِذَا", "فَفَزِعَ", "بَاطِلًا", "سَلٰسِلَ", "عِبَادِنَا",
             "ذِرَاعًا", "وَمَغَارِبَهَا", "مَنَازِلَ", "فَطَفِقَ", "خِيَانَتَكَ", "لِعِبَادِىَ", "خَائِفًا",
             "وَلَا جِدَالَ", "سَفَرِنَا هٰذَا", "خَلَقَ ظِلَالًا", "بِهٰذَا مَثَلاً", "اِلٰى صِرَاطِ"], 3, 1)}],
            description="Aşağıdaki kelimeleri uzman rehberliğinde okuyarak bilgilerinizi pekiştiriniz."),
    ]


def unit_kesranin_uzatilmasi() -> List[Dict]:
    c = "kesranin-uzatilmasi"
    groups = [{"preNote": "Aşağıdaki kesra harekeli harfleri önce tek tek, daha sonra beşli guruplar halinde "
                          "ritmik usulle, uzatarak okuyunuz.", "preNoteSound": "aciklama_2", "rows": []}]
    groups += ye([{"هـ": "ه۪ى", "ى": "ي۪ى"}.get(x, f"{x}۪ى") for x in EE], [f"{x}۪ى" for x in KE], 1, 22,
                 ince_ritmik=False, kalin_ritmik=False)
    thin_short, thin_long = [kesra(x) for x in EE], [kesra_long(x) for x in EE]
    thick_short, thick_long = [f"{x}ِ" for x in KE], [f"{x}۪ى" for x in KE]
    return [
        lesson(c, "kesranin-uzatilmasi", "Kesra'nın Uzatılması", groups,
               description="Kesra harekeden sonra gelen **harekesiz ya harfi ve dikey kesra** harfin uzatılarak "
                           "okunması gerektiğini ifade eder."),
        lesson(c, "karsilastirma", "Karşılaştırma", [
            {"title": THIN, "titleSound": "baslik2", "type": "sets",
             "rows": windowed_sets([thin_short, thin_long], 1, KESRA_WINDOWS_THIN)},
            {"title": THICK, "titleSound": "baslik3", "type": "sets",
             "rows": windowed_sets([thick_short, thick_long], 22, KESRA_WINDOWS_THICK)},
        ], description="Aşağıdaki harfleri, kesranın uzatılmayan ve uzatılan konumları arasındaki farkı "
                       "mukâyese ederek **ritmik** usulle okuyunuz. Bir harfin iki konumunu okuduktan sonra "
                       "diğer harfe geçiniz."),
        lesson(c, "uygulama", "Uygulama", [{"rows": j(
            ["ف۪يهِ", "بِه۪", "ق۪يلَ", "ح۪ينَ", "هٰذِه۪", "ذٰلِكَ", "شَه۪يدًا", "فَرِح۪ينَ", "بِاَخ۪يكَ", "خَل۪يفَةً",
             "فَفَر۪يقًا", "عِض۪ينَ", "بِبَن۪يهِ", "سَب۪يل۪ى", "كَافِر۪ينَ", "لَكَب۪يرَةً", "بِخَارِج۪ينَ", "اَفَعَي۪ينَا",
             "ثَلٰث۪ينَ", "فَخَش۪ينَا", "س۪يرَتَهَا", "بِقَم۪يص۪ى", "شَيٰط۪ينَ", "لَحَافِظ۪ينَ", "نَف۪يرًا", "شَاكِر۪ينَ",
             "لَط۪يفًا خَب۪يرًا", "قَالَ سَاٰو۪ى", "وَاِذَا ق۪يلَ", "سِن۪ينَ دَاَبًا", "خٰلِد۪ينَ ف۪يهَا", "كَانَ ضَع۪يفًا",
             "ذ۪ى ثَلٰثِ", "ف۪ى عِبَاد۪ى", "م۪يثَاقًا غَل۪يظًا", "عَل۪يمًا حَك۪يمًا"], 3, 1)}],
            description="Aşağıdaki kelimeleri kesranın uzatılan ve uzatılmayan konumlarını gözlemleyerek okuyunuz."),
    ]


def unit_kesranin_tenvini() -> List[Dict]:
    c = "kesranin-tenvini"
    thin = [kesra(x) for x in EE], [kesra_long(x) for x in EE], ["هٍـ" if x == "هـ" else f"{x}ٍ" for x in EE]
    thick = [f"{x}ِ" for x in KE], [f"{x}۪ى" for x in KE], [f"{x}ٍ" for x in KE]
    return [
        lesson(c, "kesranin-tenvini", "Kesra'nın Tenvini (İki Kesra)",
               ye(thin[2], thick[2], 1, 22, ince_ritmik=False, kalin_ritmik=False),
               description='**Tenvin** bir harfin sesine **"n"** sesi ilave etmektir. Kesra harekedeki **"i"** '
                           'sesi, tenvinle **"in"** sesine dönüşür.'),
        lesson(c, "karsilastirma", "Karşılaştırma", [
            {"title": THIN, "titleSound": "baslik2", "type": "sets",
             "rows": windowed_sets(thin, 1, KESRA_WINDOWS_THIN)},
            {"title": THICK, "titleSound": "baslik3", "type": "sets",
             "rows": windowed_sets(thick, 22, KESRA_WINDOWS_THICK)},
        ], description="Kesranın uzatılmayan, uzatılan ve tenvinli konumunu mukâyese ederek **ritmik** usulle "
                       "okuyunuz. Bir harfin üç konumunu okuduktan sonra diğer harfe geçiniz."),
        lesson(c, "uygulama", "Uygulama", [{"rows": j(
            ["فَلَكٍ", "د۪ينٍ", "وَاقِعٍ", "بِاَخٍ", "حَمَاٍ", "تَرَاضٍ", "بِخَارِ جٍ", "يٰبِسَاتٍ", "بِحَف۪يظٍ",
             "غَوَاشٍ", "بِغَضَبٍ", "ح۪ينَئِذٍ", "لَف۪ى نَع۪يمٍ", "بِبَاسِطٍ", "ح۪ينَ مَنَاصٍ", "ف۪ى فَلَكٍ",
             "ف۪ى حَد۪يثٍ", "عَذَابٍ غَل۪يظٍ"], 3, 1)}],
            description="Aşagıdaki kelimeleri harekelerine dikkat ederek okuyunuz."),
    ]


# --- Units 8-10: damme -------------------------------------------------------

DAMME_LONG = ["اُو", "بُو", "تُو", "ثُو", "جُو", "حُو", "دُو", "ذُو", "زُو", "سُو", "شُو", "عُو", "فُو", "كُو",
              "لُو", "مُو", "نُو", "هُو", "وُو", "يُو"]
DAMME_LONG_THICK = ["خُو", "رُو", "غُو", "قُو", "صُو", "ضُو", "طُو", "ظُو"]
DAMME_TENVIN = ["اٌ", "بٌ", "تٌ", "ثٌ", "جٌ", "حٌ", "دٌ", "ذٌ", "زٌ", "سٌ", "شٌ", "عٌ", "فٌ", "كٌ", "لٌ", "مٌ",
                "نٌ", "هٌـ", "وٌ", "ىٌ"]
DAMME_TENVIN_THICK = ["خٌ", "رٌ", "غٌ", "قٌ", "صٌ", "ضٌ", "طٌ", "ظٌ"]


def unit_damme() -> List[Dict]:
    c = "damme"
    return [
        lesson(c, "damme", "Damme", ye([damme(x) for x in HE], [f"{x}ُ" for x in BE], 1, 21),
               description='Damme hareke ince sesli harflere **"ü – u arası"** bir ses; kalın sesli harflere ise '
                           '**"u"** sesi verir. Aşağıdaki damme harekeli harfleri önce tek tek, daha sonra beşli '
                           "gruplar halinde **ritmik** usulle, uzatmadan okuyunuz."),
        lesson(c, "uygulama", "Uygulama", [{"rows": j(
            ["اُمَمٍ", "كُتُبٍ", "عِبَادُ", "اُنَاسٍ", "سُبُلَ", "وَطُبِعَ", "اُكُلٍ", "مَفَاتِحُ", "تَخَاصُمُ",
             "وَوُضِعَ", "يُؤَاخِذُ", "ف۪ى ظُلَلٍ", "بِمَا تَعِدُنَا", "كَمَا سُئِلَ", "خُطُوٰتِ", "بِغُلَامٍ عَل۪يمٍ",
             "فَلَا جُنَاحَ", "فَاِذَا نُفِخَ", "ف۪يهَا مَنٰفِعُ", "وَاِذَا قُرِئَ", "يَه۪يجُ فَتَرٰيهُ"], 3, 1)}],
            description="Aşağıdaki kelimeleri harekelerine dikkat ederek okuyunuz."),
    ]


def unit_dammenin_uzatilmasi() -> List[Dict]:
    c = "dammenin-uzatilmasi"
    return [
        lesson(c, "dammenin-uzatilmasi", "Damme'nin Uzatılması", ye(DAMME_LONG, DAMME_LONG_THICK, 1, 21),
               description='Damme\'den sonra gelen **"harekesiz Vâv harfi"** uzatma işlevi görür. Aşağıdaki '
                           "tabloda yer alan damme harekeli harfleri önce tek tek, daha sonra beşli guruplar "
                           "halinde **ritmik** usulle, **uzatarak** okuyunuz."),
        lesson(c, "karsilastirma", "Karşılaştırma", [
            {"title": THIN, "titleSound": "baslik2", "type": "sets",
             "rows": sets([[damme(x) for x in HE], DAMME_LONG], 1, 5)},
            {"title": THICK, "titleSound": "baslik3", "type": "sets",
             "rows": sets([[f"{x}ُ" for x in BE], DAMME_LONG_THICK], 21, 4)},
        ], description="Tablodaki damme harekelerin uzatılmayan ve uzatılan konumlarını mukâyese ederek "
                       "**ritmik** usulle okuyunuz. Bir harfin iki konumunu okuduktan sonra diğer harfe geçiniz."),
        lesson(c, "uygulama", "Uygulama", [{"rows": j(
            ["هُودًا", "رُوح۪ى", "تَكُونُ", "يَرِثُونَ", "عَلٰى نُورٍ", "صٰبِرُونَ", "يَغُوصُونَ", "فَذُوقُوهُ",
             "يَتَغَامَزُونَ", "سَتَجِدُونَ", "وَلَا فُسُوقَ", "خَلَطُوا عَمَلًا", "وَذَرُوا مَا بَقِىَ", "ظُهُورُهُمَا",
             "اَفَلَا تُوقِنُونَ", "وَكَانَ اَبُوهُمَا", "فَقَالُوا سَلٰمًا", "يُر۪يدُوا خِيَانَتَكَ"], 3, 1)}],
            description="Aşağıdaki kelimeleri harflerin uzatılmayan ve uzatılan konumlarına dikkat ederek "
                        "rehber eşliğinde okuyunuz."),
    ]


def unit_dammenin_tenvini() -> List[Dict]:
    c = "dammenin-tenvini"
    return [
        lesson(c, "dammenin-tenvini", "Damme'nin Tenvini (İki Damme)", ye(DAMME_TENVIN, DAMME_TENVIN_THICK, 1, 21),
               description='Harfin sesine **"n"** sesi ilave etmek olan **tenvin**, damme harekedeki **"u"** '
                           'sesini **"un"** sesine dönüştürür.'),
        lesson(c, "karsilastirma", "Karşılaştırma", [
            {"title": THIN, "titleSound": "baslik2", "type": "sets",
             "rows": sets([[damme(x) for x in HE], DAMME_LONG, DAMME_TENVIN], 1, 5)},
            {"title": THICK, "titleSound": "baslik3", "type": "sets",
             "rows": sets([[f"{x}ُ" for x in BE], DAMME_LONG_THICK, DAMME_TENVIN_THICK], 21, 4)},
        ], description="Damme'nin uzatılmayan, uzatılan ve tenvinli konumlarını mukâyese ederek **ritmik** "
                       "usulle okuyunuz. Bir harfin üç konumunu okuduktan sonra diğer harfe geçiniz."),
        lesson(c, "uygulama", "Uygulama", [{"rows": j(
            ["كُتُبٌ", "رَسُولٌ", "دَافِعٌ", "شَرَابٌ", "غَفُورٌ", "رِجَالٌ", "حَم۪يمٌ", "حَرَجٌ اِذَا",
             "لَعَل۪يمٌ حَل۪يمٌ", "وَحُورٌ ع۪ينٌ", "وَلَا خِلَالٌ", "مُتَشٰبِهٰتٌ", "كَانَ رِجَالٌ", "سَرَقَ اَخٌ",
             "عَذَابٌ عَظ۪يمٌ", "عَجُوزٌ عَق۪يمٌ", "كَانَ ل۪ى قَر۪ينٌ", "عَز۪يزٌ حَك۪يمٌ"], 3, 1)}],
            description="Tablodaki kelimeleri, harekelerin bütün konumlarına dikkat ederek okuyunuz."),
    ]


# --- Units 11-13: cezm, şedde, pekiştirme ------------------------------------


def unit_cezm() -> List[Dict]:
    c = "cezm"
    steps = [("اَصَ", "اَصْ", "اَصْلَحَ"), ("وِرَ", "وِرْ", "وِرْدًا"), ("يَئِسَ", "يَئِسْ", "يَئِسْنَ"),
             ("فَحُكَ", "فَحُكْ", "فَحُكْمُ"), ("اِذَ", "اِذْ", "اِذْ قَامُوا")]
    return [
        lesson(c, "cezm", "Cezm (Harflerin Birleştirilmesi)", [{"type": "build", "rows": [
            {"tiles": [tile(f"btn_{i * 3 + k + 1}", w) for k, w in enumerate(s)]} for i, s in enumerate(steps)]}],
            description="Cezm, üzerinde bulunduğu harfi kendinden önceki harfin harekesiyle birleştirir. Cezimli "
                        'harf aynı zamanda **"sakin harf"** olarak da ifade edilir.'),
        lesson(c, "kavrama", "Kavrama", [{"rows": j(
            ["بِئْ", "تِبْ", "مُتْ", "هَثْ", "يَجْ", "يَحْ", "سِخْ", "قَدْ", "عُذْ", "صِرْ", "وَزْ", "بِسْ", "خَشْ",
             "فَصْ", "رِضْ", "شَطْ", "تُظْ", "نَعْ", "مَغْ", "غُفْ", "فَقْ", "رَكْ", "زُلْ", "هُمْ", "مَنْ", "صِهْ",
             "اَوْ", "وَىْ"], 4, 1)}],
            description="Aşağıdaki cezimli harfleri bir önceki harfin harekesini **uzatmadan**, rehber eşliğinde okuyunuz."),
        lesson(c, "uygulama", "Uygulama", [{"rows": j(
            ["لَمْ", "وِزْرٌ", "اِفْكٌ", "فَاَوْفِ", "اَثْقَالَهَا", "مَخْذُولاً", "غَضْبٰنَ", "اُخْرِجَتْ",
             "فَلَا تُشْمِتْ", "قُلْ اَرَاَيْتُمْ", "مُسْلِمُونَ", "قُلْ ف۪يهِمَا", "تَسْتَعْجِلُونَ", "ذٰلِكُمْ اِصْر۪ى",
             "تُغْنِىَ عَنْهُمْ", "وَاِلٰهُكُمْ وَاحِدٌ", "مِنَ الْوِلْدَانِ", "سَاُر۪يكُمْ اٰيٰ۪تى"], 3, 1)}],
            description="Aşağıdaki kelimeleri uzman rehberliğinde okuyunuz."),
    ]


def unit_sedde() -> List[Dict]:
    c = "sedde"
    # The site shows these nine as images (ders/eb/12/<n>.png): a şeddeli word
    # with arrows down to its two halves, the sakin letter and the same letter
    # voweled. Transcribed from those images, in btn order.
    splits = [("تَزُّ", "زْ", "زُ"), ("حُرِّ", "رْ", "رِ"), ("فَكُّ", "كْ", "كُ"),
              ("مَرُّو", "رْ", "رُو"), ("اِنَّا", "نْ", "نَا"), ("مِمَّ", "مْ", "مَ"),
              ("مِنِّى", "نْ", "نِى"), ("صَفًّا", "فْ", "فًا"), ("كُلٌّ", "لْ", "لٌ")]
    return [
        lesson(c, "sedde", "Şedde", [{"type": "split", "rows": [
            {"tiles": [tile(f"btn_{i + k + 1}", w, parts=[a, b]) for k, (w, a, b) in enumerate(splits[i:i + 3])]}
            for i in range(0, 9, 3)]}],
            description="Şedde mutlaka bir hareke ile birlikte bulunur ve bulunduğu harfi bir önceki harf ile "
                        "birleştirip sesi bölmeden tekrar okutur. Bu durum, Türkçemizde iki sessiz harfin yan "
                        "yana gelmesine benzer. (Sı**hh**at, di**kk**at, ku**ll**uk, be**ll**ek, ce**nn**et vb.)"),
        lesson(c, "uygulama", "Uygulama", [{"rows": j(
            ["مِمَّا", "عَدَّ", "كُلٌّ", "تَوَّابًا", "تَبَّتْ", "تَطَّلِعُ", "يَمُدُّهُمْ", "يُكَذِّبُ", "مُسْتَقَرٌّ",
             "فَصَلِّ لِرَبِّكَ", "سَحَّارٍ عَل۪يمٍ", "وَلَا يَحُضُّ", "وَيَحِقَّ الْقَوْلُ", "اِمَّا يُعَذِّبُهُمْ",
             "بِاٰيٰتِ رَبِّنَا", "فَسَوّٰيهُنَّ سَبْعَ", "نُسَبِّحُ بِحَمْدِكَ", "مِمَّا ذُكِّرُوا"], 3, 1)}],
            description="Aşağıdaki şeddeli kelimeleri harekelerine dikkat ederek, uzman rehberliğinde "
                        "inceleyiniz ve okuyunuz."),
    ]


MURSALAT = [
    "وَالْمُرْسَلٰتِ عُرْفًاۙ", "فَالْعٰصِفٰتِ عَصْفًاۙ", "وَالنّٰشِرٰتِ نَشْرًاۙ", "فَالْفٰرِقٰتِ فَرْقًاۙ",
    "فَالْمُلْقِيٰتِ ذِكْرًاۙ", "عُذْرًا اَوْ نُذْرًاۙ", "اِنَّمَا تُوعَدُونَ لَوٰقِعٌۜ", "فَاِذَا النُّجُومُ طُمِسَتْۙ",
    "وَاِذَا السَّمَٓاءُ فُرِجَتْۙ", "وَاِذَا الْجِبَالُ نُسِفَتْۙ", "وَاِذَا الرُّسُلُ اُقِّتَتْۜ",
    "لِاَىِّ يَوْمٍ اُجِّلَتْۜ", "لِيَوْمِ الْفَصْلِۚ",
]


def unit_pekistirme() -> List[Dict]:
    return [lesson("pekistirme", "pekistirme", "Pekiştirme", [{
        "type": "ayet", "reference": "Mürselât, 1-13",
        "rows": [{"tiles": [tile(f"btn_{i + 1}", v, ayah=i + 1)]} for i, v in enumerate(MURSALAT)],
    }], description="Buraya kadar öğrendiklerinizi aşağıdaki ayetlerde pekiştiriniz.")]


UNITS = [
    (1, "harfler", "Harfler", unit_harfler),
    (2, "fetha", "Fetha", unit_fetha),
    (3, "fethanin-uzatilmasi", "Fetha'nın Uzatılması", unit_fethanin_uzatilmasi),
    (4, "fetha-tenvin", "Fetha'nın Tenvini", unit_fetha_tenvin),
    (5, "kesra", "Kesra", unit_kesra),
    (6, "kesranin-uzatilmasi", "Kesra'nın Uzatılması", unit_kesranin_uzatilmasi),
    (7, "kesranin-tenvini", "Kesra'nın Tenvini", unit_kesranin_tenvini),
    (8, "damme", "Damme", unit_damme),
    (9, "dammenin-uzatilmasi", "Damme'nin Uzatılması", unit_dammenin_uzatilmasi),
    (10, "dammenin-tenvini", "Damme'nin Tenvini", unit_dammenin_tenvini),
    (11, "cezm", "Cezm", unit_cezm),
    (12, "sedde", "Şedde", unit_sedde),
    (13, "pekistirme", "Pekiştirme", unit_pekistirme),
]


def sound_path(lesson_: Dict, name: str) -> str:
    """Path of a recording under SOUND_ROOT (and under the local audio cache)."""
    return f"{PART}/{lesson_['chapter']}/{lesson_['lesson']}/{name}.mp3"


def iter_sounds(lesson_: Dict):
    """Every recording a lesson refers to, in first-use order, without repeats."""
    seen = set()

    def emit(name):
        if name and name not in seen:
            seen.add(name)
            return [name]
        return []

    out = emit(lesson_.get("descriptionSound"))
    for g in lesson_["groups"]:
        for key in ("preNoteSound", "titleSound"):
            out += emit(g.get(key))
        for h in g.get("headers", []):
            out += emit(h["sound"])
        for r in g["rows"]:
            for t in r["tiles"]:
                out += emit(t["sound"])
            out += emit(r.get("ritmikSound"))
        out += emit(g.get("ritmikSound"))
        out += emit(g.get("noteSound"))
    return out


def build() -> List[Dict]:
    units = []
    for number, key, title, make in UNITS:
        lessons = make()
        for les in lessons:
            les["id"] = f"{les['chapter']}/{les['lesson']}"
        units.append({"number": number, "key": key, "title": title, "lessons": lessons})
    return units


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    json.dump(build(), sys.stdout, ensure_ascii=False, indent=1)
