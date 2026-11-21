"""Build a copy of the Arabic font whose vowel marks are blank.

The clay sprites colour the harakat on their own: the whole text is painted in
the mark's colour, then the letters over it in ink. Painting the letters by
*removing* the marks from the string does not work, because Scheherazade
spaces letters apart to keep neighbouring marks from colliding, so the
mark-less string is narrower and the ink lands shifted against the marks
(سَاَلَ: 491 px with marks, 472 px without). This font keeps every glyph,
advance and positioning rule and only empties the mark outlines, so the same
string lays out identically and simply draws no marks.

The OFL reserves the name "Scheherazade" for the original, so the modified
copy is named "Markless Arabic".

    python -m elifba.markless_font
"""

from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph

FONTS = Path(__file__).parent / "engine" / "fonts"
SOURCE = FONTS / "ScheherazadeNew-Bold.ttf"
TARGET = FONTS / "MarklessArabic-Bold.ttf"
FAMILY = "Markless Arabic"

# the engine's HARAKAT class: fathatan..sukun, superscript alif, U+06EA, U+0656
MARKS = [*range(0x064B, 0x0653), 0x0670, 0x06EA, 0x0656]


def mark_glyphs(font: TTFont) -> set:
    """The mark glyphs for MARKS, plus every variant and shadda ligature the
    font substitutes them with. Ligatures with other marks (hamza, small meem)
    are left alone: those carry part of the letter."""
    cmap = font.getBestCmap()
    start = {cmap[c] for c in MARKS if c in cmap}
    gdef = font["GDEF"].table.GlyphClassDef.classDefs
    found, changed = set(start), True
    while changed:
        changed = False
        for lookup in font["GSUB"].table.LookupList.Lookup:
            for sub in lookup.SubTable:
                sub = sub.ExtSubTable if lookup.LookupType == 7 else sub
                outputs = []
                for a, b in (getattr(sub, "mapping", None) or {}).items():
                    if a in found:
                        outputs += b if isinstance(b, list) else [b]
                for a, bs in (getattr(sub, "alternates", None) or {}).items():
                    if a in found:
                        outputs += bs
                for first, ligs in (getattr(sub, "ligatures", None) or {}).items():
                    for lig in ligs:
                        parts = [first, *lig.Component]
                        if all(p in found or p == "uni034F" for p in parts):
                            outputs.append(lig.LigGlyph)
                for g in outputs:
                    # maddah is reached through a substitution chain but is not
                    # a vowel mark here (آ may be built from it): keep it drawn
                    if g not in found and gdef.get(g) == 3 and not g.startswith("uni0653"):
                        found.add(g)
                        changed = True
    return found


#: one-hareke fonts, for words whose marks differ (ders 5: fetha, kesra and damme
#: each in its own colour). Each draws only its own mark and nothing else, at the
#: exact place Scheherazade puts it; the letters come from the markless font.
HAREKE_CODES = {"fetha": "064E", "kesra": "0650", "damme": "064F"}


def hareke_target(hareke: str) -> Path:
    return FONTS / f"Marks{hareke.capitalize()}-Bold.ttf"


def hareke_family(hareke: str) -> str:
    return f"Marks {hareke.capitalize()}"


def hareke_glyphs(font: TTFont, hareke: str) -> set:
    """The mark glyphs that draw this hareke: its variants and shadda ligatures."""
    code = HAREKE_CODES[hareke]
    return {g for g in mark_glyphs(font) if code in g}


def _flatten(font: TTFont, names: set) -> None:
    """Turn composite glyphs into plain outlines. A kept composite can be built
    from glyphs that must be blank (shadda-kesra uses the fetha glyph), and
    keeping those parts would draw a fetha in the kesra colour."""
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    glyph_set, glyf = font.getGlyphSet(), font["glyf"]
    flat = {}
    for name in names:
        if glyf[name].isComposite():
            # a TTGlyphPen alone would record the components again, not their outlines
            rec = DecomposingRecordingPen(glyph_set)
            glyph_set[name].draw(rec)
            pen = TTGlyphPen(None)
            rec.replay(pen)
            flat[name] = pen.glyph()
    for name, glyph in flat.items():
        glyf[name] = glyph


def _rename(font: TTFont, family: str) -> None:
    for rec in font["name"].names:
        if rec.nameID in (1, 3, 4, 6, 16, 21):
            text = rec.toUnicode()
            text = text.replace("Scheherazade New", family).replace("ScheherazadeNew", family.replace(" ", ""))
            rec.string = text


def build() -> Path:
    font = TTFont(SOURCE)
    glyf = font["glyf"]
    blank = mark_glyphs(font)
    for name in blank:
        glyf[name] = Glyph()            # no outline; advance and anchors untouched
    _rename(font, FAMILY)
    font.save(TARGET)
    print(f"{TARGET.name}: {len(blank)} mark glyphs blanked")

    for hareke in HAREKE_CODES:
        font = TTFont(SOURCE)
        glyf = font["glyf"]
        keep = hareke_glyphs(font, hareke)
        _flatten(font, keep)
        for name in font.getGlyphOrder():
            if name not in keep:
                glyf[name] = Glyph()
        _rename(font, hareke_family(hareke))
        font.save(hareke_target(hareke))
        print(f"{hareke_target(hareke).name}: {len(keep)} glyphs kept")
    return TARGET


if __name__ == "__main__":
    build()
