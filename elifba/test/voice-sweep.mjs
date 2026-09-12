#!/usr/bin/env node
/**
 * Pick an Arabic voice, on two things at once.
 *
 * 1. Evenness. Some voices stretch damma into a long "oo", so the three
 *    vowels a child is meant to be contrasting arrive at very different
 *    lengths. ar-SA-ZariyahNeural does this at every rate.
 *
 * 2. Elif. A bare alif carrying a vowel is not a word, and a TTS resolves it
 *    as the definite article "ال" -- so "اِ" comes back with an L in it. That
 *    is a wrong *sound*, not merely an awkward one, and it is the first letter
 *    in the book.
 *
 * Every candidate is synthesised under its own role, so one pass writes a
 * single clip index covering all of them and transcribe.py can read it:
 *
 *   node elifba/test/voice-sweep.mjs
 *   python elifba/test/transcribe.py --cache elifba/out/voice-sweep --model medium --cpu
 */

import { mkdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { spokenId } from '../timeline.mjs';
import { synthesise } from '../voice.mjs';
import { mean, speechSeconds, spread } from './audio-metrics.mjs';

const ELIFBA_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
export const SWEEP_DIR = path.join(ELIFBA_DIR, 'out', 'voice-sweep');

const MARKS = ['َ', 'ِ', 'ُ'];

/** Letters with a real consonant, so evenness is measured free of the elif problem. */
const CONSONANTS = ['ب', 'ت', 'م', 'س'];

/**
 * Spellings that might get a clean vowel out of a voice for elif: the bare
 * alif as the book prints it, then alif carrying hamza, then hamza alone.
 */
const ELIF_SPELLINGS = [
  ['bare alif', ['اَ', 'اِ', 'اُ']],
  ['alif+hamza', ['أَ', 'إِ', 'أُ']],
  ['hamza alone', ['ءَ', 'ءِ', 'ءُ']],
];

/*
 * Female, and dialects that read ج as /dʒ/. Egyptian is excluded on content
 * -- it reads that letter as /g/ and the book calls it Cim -- and so are the
 * Maghrebi voices, whose vowel system is furthest from what is being taught.
 */
const CANDIDATES = [
  'ar-SA-ZariyahNeural',
  'ar-AE-FatimaNeural',
  'ar-KW-NouraNeural',
  'ar-QA-AmalNeural',
  'ar-BH-LailaNeural',
  'ar-JO-SanaNeural',
  'ar-IQ-RanaNeural',
  'ar-OM-AyshaNeural',
  'ar-SY-AmanyNeural',
  'ar-YE-MaryamNeural',
];

const RATE = '-40%';

async function main() {
  await rm(SWEEP_DIR, { recursive: true, force: true });
  await mkdir(SWEEP_DIR, { recursive: true });

  // One role per candidate, so a single synthesise call covers every voice and
  // leaves one index.json behind for the transcriber to read.
  const voices = {};
  const rates = {};
  const lines = [];
  CANDIDATES.forEach((voice, i) => {
    const role = `v${i}`;
    voices[role] = voice;
    rates[role] = RATE;
    for (const letter of CONSONANTS) {
      for (const mark of MARKS) lines.push({ text: letter + mark, role });
    }
    for (const [, spellings] of ELIF_SPELLINGS) {
      for (const text of spellings) lines.push({ text, role });
    }
  });

  const { files } = await synthesise(lines, { cacheDir: SWEEP_DIR, voices, rates });

  console.log('voice                  fatha  kasra  damma | spread  damma/fatha | elif spellings (secs)');
  console.log('-'.repeat(100));

  for (let i = 0; i < CANDIDATES.length; i++) {
    const role = `v${i}`;
    const perMark = [];
    for (const mark of MARKS) {
      const secs = [];
      for (const letter of CONSONANTS) {
        secs.push(await speechSeconds(files.get(spokenId({ text: letter + mark, role }))));
      }
      perMark.push(mean(secs));
    }

    const elif = [];
    for (const [label, spellings] of ELIF_SPELLINGS) {
      const secs = [];
      for (const text of spellings) {
        secs.push(await speechSeconds(files.get(spokenId({ text, role }))));
      }
      elif.push(`${label} ${secs.map((s) => s.toFixed(2)).join('/')}`);
    }

    console.log(
      CANDIDATES[i].padEnd(22),
      perMark.map((s) => s.toFixed(2).padStart(5)).join('  '), '|',
      (spread(perMark) * 100).toFixed(0).padStart(5) + '%',
      (perMark[2] / perMark[0]).toFixed(2).padStart(12), '|',
      elif.join('  '),
    );
  }

  console.log(`\nclips in ${SWEEP_DIR}`);
  console.log('now transcribe them to see which spellings come back with an L in them:');
  console.log(`  python elifba/test/transcribe.py --cache ${path.relative(process.cwd(), SWEEP_DIR)} --model medium --cpu`);
}

main().catch((err) => {
  console.error(err.stack || String(err));
  process.exit(1);
});
