#!/usr/bin/env node
/**
 * Is the Arabic voice sounding the letter, or naming it?
 *
 * A bare vowelled letter like "بَ" is not a word, and a TTS handed one can
 * reasonably do either: produce the syllable "ba", or fall back to reading the
 * character's name, "باء". Those are completely different things to put in
 * front of a child, and by ear on a single clip they are easy to confuse --
 * both start with the same consonant.
 *
 * Length separates them cleanly. A CV syllable is a couple of hundred
 * milliseconds of speech; a letter name is two syllables and roughly twice
 * that. So synthesise the candidates side by side and measure the *speech*,
 * with edge-tts's leading and trailing silence trimmed off, because the raw
 * file duration is padded and says nothing.
 *
 *   node elifba/test/sound-compare.mjs
 */

import { mkdir, readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { spokenId } from '../timeline.mjs';
import { synthesise } from '../voice.mjs';
import { speechSeconds } from './audio-metrics.mjs';

const ELIFBA_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

// Runs against whatever the lesson is configured to use, so it stays a live
// check rather than a record of one voice we happened to reject.
const lesson = JSON.parse(
  await readFile(path.join(ELIFBA_DIR, 'lesson.json'), 'utf8'),
);
const VOICE = lesson.voices.letters;
const RATE = lesson.rates.letters;

/**
 * Each row: what the card shows, the syllable we want, and the letter's real
 * Arabic name -- the thing we are afraid it is saying instead.
 */
const CASES = [
  { label: 'elif + fatha', syllable: 'اَ', name: 'ألف' },
  { label: 'elif + kasra', syllable: 'اِ', name: 'ألف' },
  { label: 'elif + damma', syllable: 'اُ', name: 'ألف' },
  { label: 'be + fatha', syllable: 'بَ', name: 'باء' },
  { label: 'be + kasra', syllable: 'بِ', name: 'باء' },
  { label: 'be + damma', syllable: 'بُ', name: 'باء' },
  { label: 'te + fatha', syllable: 'تَ', name: 'تاء' },
  { label: 'mim + fatha', syllable: 'مَ', name: 'ميم' },
];


async function main() {
  const cacheDir = path.join(ELIFBA_DIR, 'out', 'sound-compare');
  await rm(cacheDir, { recursive: true, force: true });
  await mkdir(cacheDir, { recursive: true });

  const lines = [];
  for (const c of CASES) {
    lines.push({ text: c.syllable, role: 'letters' });
    lines.push({ text: c.name, role: 'letters' });
  }

  const { files } = await synthesise(lines, {
    cacheDir,
    voices: { letters: VOICE },
    rate: RATE,
  });

  console.log(`voice ${VOICE} @ ${RATE}\n`);
  console.log('case            shown   speech   |  name     speech   | ratio  verdict');
  console.log('-'.repeat(78));

  for (const c of CASES) {
    const sylSecs = await speechSeconds(files.get(spokenId({ text: c.syllable, role: 'letters' })));
    const nameSecs = await speechSeconds(files.get(spokenId({ text: c.name, role: 'letters' })));
    const ratio = sylSecs / nameSecs;
    // A syllable should be clearly shorter than the two-syllable letter name.
    // At better than ~0.8 of it, the voice is very likely saying the name.
    const verdict = ratio > 0.8 ? 'NAMING THE LETTER?' : 'syllable';
    console.log(
      c.label.padEnd(14),
      c.syllable.padEnd(6), sylSecs.toFixed(2).padStart(6), '  |',
      c.name.padEnd(6), nameSecs.toFixed(2).padStart(6), '  |',
      ratio.toFixed(2).padStart(5), ' ', verdict,
    );
  }
}

main().catch((err) => {
  console.error(err.stack || String(err));
  process.exit(1);
});
