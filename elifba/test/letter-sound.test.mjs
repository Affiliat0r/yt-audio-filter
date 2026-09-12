/**
 * The letter sounds have to be usable, and the narration has to stay Turkish.
 *
 * This is the test the shipped video failed. It was voiced with
 * ar-SA-ZariyahNeural, which stretches damma into a long "oo": measured across
 * five letters, fatha came back at 0.23s, kasra 0.20s and damma 0.36s -- a 61%
 * spread between the three sounds a child is supposed to be contrasting. Every
 * clip was also far too short to imitate. Slowing that voice down did not help;
 * the ratio held at 1.55 at every rate, so the voice itself had to change.
 *
 * It also guards a second defect with a different shape. Elif and hemze carry
 * no consonant, and a bare alif with a vowel is not a pronounceable Arabic
 * word -- so every Arabic voice tried resolved "اِ" into the definite article
 * ال and put an audible L into it. That is not a tuning problem and no
 * spelling fixed it; those two letters are read by the Turkish narrator, whose
 * bare vowel is exactly what the book asks for there.
 *
 * Nothing here claims a sound is *correct* -- only a person can judge an
 * Arabic phoneme, which is why elifba/test/transcribe.py exists for listening
 * by machine and the audition script for listening by ear. What these
 * thresholds do is rule out what cannot be right: a syllable too brief to
 * hear, or three vowels presented at wildly different lengths.
 *
 * Needs the network on first run; afterwards the clip cache serves it.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { buildTimeline, spokenId, spokenLines } from '../timeline.mjs';
import { synthesise } from '../voice.mjs';
import { mean, speechSeconds, spread } from './audio-metrics.mjs';

const ELIFBA_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const lesson = JSON.parse(readFileSync(path.join(ELIFBA_DIR, 'lesson.json'), 'utf8'));

/** Short enough that a three-year-old cannot catch it. */
export const MIN_MEAN_SECONDS = 0.28;

/** How unevenly the three harakat may be presented. */
export const MAX_SPREAD = 0.35;

/** Damma against fatha. Above this the voice is elongating it into "oo". */
export const DAMMA_RATIO = [0.7, 1.35];

/** Stand-in clip lengths, so timeline shape can be checked without the network. */
function stubDurations(lessonData, ids) {
  return new Map(spokenLines(lessonData, ids).map((line) => [spokenId(line), 1]));
}

/** A spread of letters: two shallow, one tall, one deep, one wide. */
const SAMPLE = ['ا', 'ب', 'ت', 'م', 'س'];

let byMark = null;

test.before(async () => {
  const lines = [];
  for (const letter of SAMPLE) {
    for (const h of lesson.harakat) lines.push({ text: letter + h.mark, role: 'letters' });
  }

  const { files } = await synthesise(lines, {
    cacheDir: path.join(ELIFBA_DIR, 'voice'),
    voices: lesson.voices,
    rates: lesson.rates,
  });

  byMark = [];
  for (const h of lesson.harakat) {
    const secs = [];
    for (const letter of SAMPLE) {
      secs.push(await speechSeconds(files.get(spokenId({ text: letter + h.mark, role: 'letters' }))));
    }
    byMark.push({ id: h.id, seconds: secs, mean: mean(secs) });
  }
}, { timeout: 600_000 });

test('a letter sound lasts long enough to be copied', () => {
  const tooShort = byMark
    .map((m) => ({ harakat: m.id, mean: Number(m.mean.toFixed(3)) }))
    .filter((m) => m.mean < MIN_MEAN_SECONDS);
  assert.deepEqual(tooShort, [], `mean speech under ${MIN_MEAN_SECONDS}s`);
});

test('the three harakat are presented at comparable length', () => {
  const measured = spread(byMark.map((m) => m.mean));
  assert.ok(
    measured <= MAX_SPREAD,
    `harakat lengths spread ${(measured * 100).toFixed(0)}%, limit ${MAX_SPREAD * 100}% ` +
      `(${byMark.map((m) => `${m.id} ${m.mean.toFixed(2)}s`).join(', ')})`,
  );
});

test('otre is a short vowel, not a long one', () => {
  const fatha = byMark.find((m) => m.id === 'ustun').mean;
  const damma = byMark.find((m) => m.id === 'otre').mean;
  const ratio = damma / fatha;
  assert.ok(
    ratio >= DAMMA_RATIO[0] && ratio <= DAMMA_RATIO[1],
    `otre/üstün is ${ratio.toFixed(2)}, expected ${DAMMA_RATIO[0]}..${DAMMA_RATIO[1]} ` +
      '(a ratio near 1.5 means the voice is saying "oo" rather than "u")',
  );
});

test('the letters are not sounded by a dialect that changes them', () => {
  // Egyptian reads ج as /g/; the book calls that letter Cim and teaches it as
  // /dʒ/, so an ar-EG voice would contradict the curriculum every time that
  // letter came round. The Maghrebi voices are out for their vowel system,
  // which is the furthest from what is being taught here. Everything else --
  // Gulf, Levantine, Peninsular -- reads these letters close enough to fusha.
  const banned = /^ar-(EG|DZ|MA|TN)-/;
  assert.doesNotMatch(
    lesson.voices.letters,
    banned,
    `${lesson.voices.letters} is a dialect that changes the letters being taught`,
  );
  assert.match(lesson.voices.letters, /^ar-/, 'letter sounds must come from an Arabic voice');
});

test('a letter with no consonant is sounded by the narrator, not the Arabic voice', () => {
  // Elif and hemze carry no consonant, so on their own "اَ" and "ءَ" are not
  // pronounceable Arabic words. Every TTS tried resolved them into something
  // that is -- usually the definite article ال -- putting an audible L into a
  // sound that has none. The book's reading of those is a bare vowel, which
  // the Turkish narrator can simply say.
  const vowelOnly = lesson.letters.filter((l) => l.vowelOnly).map((l) => l.id);
  assert.deepEqual(vowelOnly, ['elif', 'hemze'], 'the vowel-only letters');

  for (const id of vowelOnly) {
    const letter = lesson.letters.find((l) => l.id === id);
    const timeline = buildTimeline(lesson, [id], stubDurations(lesson, [id]));
    for (const seg of timeline.segments.filter((s) => s.kind === 'harakat')) {
      assert.equal(
        seg.speak.role, 'narration',
        `${letter.name} ${seg.harakatName} must be spoken by the narrator`,
      );
      assert.ok(
        letter.say.includes(seg.speak.text.replace(/[.!?]$/, '')),
        `${letter.name} should say a bare vowel, got ${JSON.stringify(seg.speak.text)}`,
      );
    }
  }
});

test('a letter with a consonant keeps the Arabic voice', () => {
  const timeline = buildTimeline(lesson, ['be'], stubDurations(lesson, ['be']));
  for (const seg of timeline.segments.filter((s) => s.kind === 'harakat')) {
    assert.equal(seg.speak.role, 'letters', 'Be must be sounded in Arabic');
  }
});

test('the spoken narration stays Turkish', () => {
  assert.match(lesson.voices.narration, /^tr-TR-/, 'narration must be a Turkish voice');
  // "Şimdi sen söyle!" is an instruction to the child, not a letter, so it
  // belongs to the Turkish narrator however the letters are voiced.
  assert.match(lesson.prompt, /^Şimdi sen söyle/, 'the hand-over prompt must be the Turkish line');
});
