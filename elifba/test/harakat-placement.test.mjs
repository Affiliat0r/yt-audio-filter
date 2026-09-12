/**
 * Where the harakat sits, for every letter in the book.
 *
 * This is the test the shipped video failed. Amiri places marks the way
 * running text needs them -- at a fixed height above the baseline, so that
 * marks line up across a line -- which on a single letter blown up to fill a
 * card reads as broken: the fatha floats 89px above a shallow ب at a 200px
 * font while hugging a tall ا, and the kasra, pinned just under the baseline,
 * lands *inside* deep letters like ع and م.
 *
 * So the scene positions marks itself, and these are the properties that
 * placement has to hold for all thirty letters at once. Tolerances are in em
 * rather than pixels so the test means the same thing at any render height.
 *
 * Slow by nature: it drives headless Chrome and measures ~120 renders.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { probeGlyphs } from './glyph-probe.mjs';

const ELIFBA_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const lesson = JSON.parse(readFileSync(path.join(ELIFBA_DIR, 'lesson.json'), 'utf8'));

/** Gap between mark and letter, as a fraction of the font size. */
export const GAP_MIN_EM = 0.04;
export const GAP_MAX_EM = 0.18;

/** How far the mark's centre may sit from the letter's centre. */
export const CENTRE_TOLERANCE_EM = 0.04;

/** Marks are small; anything this big is the letter's outline leaking in. */
export const MARK_MAX_EM = 0.45;

const MARKS_ABOVE = new Set(['ustun', 'otre']);

let rows = null;

test.before(async () => {
  rows = await probeGlyphs({ lesson, height: 1080 });
}, { timeout: 900_000 });

test('every letter gets a mark that is actually drawn', () => {
  const missing = rows.filter((r) => !r.mark || !r.ink);
  assert.deepEqual(
    missing.map((r) => `${r.letterName} ${r.harakat}`),
    [],
    'these pairs rendered no mark, or no letter',
  );
});

test('the mark is small -- it is a mark, not a second letter', () => {
  const oversized = rows
    .filter((r) => r.mark)
    .map((r) => ({
      pair: `${r.letterName} ${r.harakat}`,
      w: (r.mark.width / r.fontSizePx).toFixed(3),
      h: (r.mark.height / r.fontSizePx).toFixed(3),
    }))
    .filter((r) => Number(r.w) > MARK_MAX_EM || Number(r.h) > MARK_MAX_EM);
  assert.deepEqual(oversized, [], `marks wider or taller than ${MARK_MAX_EM}em`);
});

test('the mark is centred over the letter it belongs to', () => {
  const offset = rows
    .filter((r) => r.mark && r.ink)
    .map((r) => ({
      pair: `${r.letterName} ${r.harakat}`,
      dxEm: Number(((r.mark.centreX - r.ink.centreX) / r.fontSizePx).toFixed(3)),
    }))
    .filter((r) => Math.abs(r.dxEm) > CENTRE_TOLERANCE_EM);
  assert.deepEqual(offset, [], `marks off-centre by more than ${CENTRE_TOLERANCE_EM}em`);
});

test('the mark clears the letter without drifting away from it', () => {
  const bad = rows
    .filter((r) => r.mark && r.ink)
    .map((r) => {
      // Above-marks are measured from the letter's top, below-marks from its
      // bottom; a negative gap means the mark has landed on the letter.
      const gapPx = MARKS_ABOVE.has(r.harakat)
        ? r.ink.minY - r.mark.maxY
        : r.mark.minY - r.ink.maxY;
      return {
        pair: `${r.letterName} ${r.harakat}`,
        gapEm: Number((gapPx / r.fontSizePx).toFixed(3)),
      };
    })
    .filter((r) => r.gapEm < GAP_MIN_EM || r.gapEm > GAP_MAX_EM);
  assert.deepEqual(bad, [], `gaps outside ${GAP_MIN_EM}em..${GAP_MAX_EM}em`);
});

test('the mark stays inside the card', () => {
  const escaped = rows
    .filter((r) => r.mark)
    .map((r) => ({
      pair: `${r.letterName} ${r.harakat}`,
      box: `${r.mark.minX},${r.mark.minY}..${r.mark.maxX},${r.mark.maxY}`,
      card: `${r.card.width}x${r.card.height}`,
    }))
    .filter((_, i) => {
      const r = rows.filter((x) => x.mark)[i];
      return (
        r.mark.minX < 0 || r.mark.minY < 0 ||
        r.mark.maxX >= r.card.width || r.mark.maxY >= r.card.height
      );
    });
  assert.deepEqual(escaped, [], 'marks clipped by the card edge');
});
