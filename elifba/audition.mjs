#!/usr/bin/env node
/**
 * Voice auditions -- the questions only a listener can settle.
 *
 * The measurements in test/letter-sound.test.mjs can prove a sound is too
 * short to copy, or that three vowels are being presented at wildly different
 * lengths. They cannot prove a phoneme is *right*. That needs ears, so this
 * builds the comparisons and gets out of the way.
 *
 *   node elifba/audition.mjs --suite sounds     # which Arabic voice
 *   node elifba/audition.mjs --suite narration  # which Turkish voice
 *
 * Each candidate becomes one mp3 saying the same lines, so they can be played
 * back to back.
 */

import { execFile } from 'node:child_process';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import { spokenId } from './timeline.mjs';
import { synthesise } from './voice.mjs';

const execFileAsync = promisify(execFile);
const ELIFBA_DIR = path.dirname(fileURLToPath(import.meta.url));

/** Seconds of quiet between lines, so each is heard on its own. */
const GAP = 0.75;

// --------------------------------------------------------------------------
// suite: sounds -- which Arabic voice sounds the letters
// --------------------------------------------------------------------------

/*
 * Elif and Be under all three harakat, then four letters whose sound a
 * Turkish voice could not distinguish -- ح against خ, and ص against س. If a
 * voice is worth having, it is on those pairs that you hear why.
 */
const SOUND_SCRIPT = [
  'اَ', 'اِ', 'اُ',
  'بَ', 'بِ', 'بُ',
  'حَ', 'خَ', 'صَ', 'سَ',
];

const SOUND_CANDIDATES = [
  {
    // What shipped. Kept in the line-up so the difference is audible rather
    // than asserted: this is the one that stretches otre into "oo".
    id: '1-shipped-zariyah-fast',
    voice: 'ar-SA-ZariyahNeural',
    rate: '-8%',
  },
  { id: '2-hamed-slow-CHOSEN', voice: 'ar-SA-HamedNeural', rate: '-40%' },
  { id: '3-zariyah-slow', voice: 'ar-SA-ZariyahNeural', rate: '-40%' },
  { id: '4-salma-slow-egyptian', voice: 'ar-EG-SalmaNeural', rate: '-40%' },
];

// --------------------------------------------------------------------------
// suite: narration -- which Turkish voice reads the lesson
// --------------------------------------------------------------------------

const NARRATION_SCRIPT = ['Elif.', 'Be.', 'Şimdi sen söyle!'];

const NARRATION_CANDIDATES = [
  { id: '1-emel-CHOSEN', voice: 'tr-TR-EmelNeural', rate: '-8%' },
  { id: '2-ahmet-male', voice: 'tr-TR-AhmetNeural', rate: '-8%' },
  { id: '3-emma-multilingual', voice: 'en-US-EmmaMultilingualNeural', rate: '-8%' },
];

const SUITES = {
  sounds: { script: SOUND_SCRIPT, candidates: SOUND_CANDIDATES, role: 'letters' },
  narration: { script: NARRATION_SCRIPT, candidates: NARRATION_CANDIDATES, role: 'narration' },
};

/** Concatenate clips with a gap between each, into one mp3. */
async function joinToMp3(clips, cacheDir, outPath, id) {
  const silence = path.join(cacheDir, 'gap.wav');
  await execFileAsync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
    '-t', String(GAP), '-c:a', 'pcm_s16le', silence,
  ]);

  const parts = [];
  for (const clip of clips) {
    const wav = path.join(cacheDir, `${id}_${parts.length}.wav`);
    await execFileAsync('ffmpeg', [
      '-hide_banner', '-loglevel', 'error', '-y',
      '-i', clip, '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', wav,
    ]);
    parts.push(wav, silence);
  }

  const listFile = path.join(cacheDir, `${id}.txt`);
  await writeFile(listFile, parts.map((p) => `file '${p.replace(/\\/g, '/')}'`).join('\n'), 'utf8');
  await execFileAsync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'concat', '-safe', '0', '-i', listFile,
    '-c:a', 'libmp3lame', '-b:a', '160k', outPath,
  ]);
}

async function main() {
  const suiteArg = process.argv.indexOf('--suite');
  const name = suiteArg === -1 ? 'sounds' : process.argv[suiteArg + 1];
  const suite = SUITES[name];
  if (!suite) {
    throw new Error(`--suite must be one of ${Object.keys(SUITES).join(', ')}, got ${JSON.stringify(name)}`);
  }

  const lesson = JSON.parse(await readFile(path.join(ELIFBA_DIR, 'lesson.json'), 'utf8'));
  const outDir = path.join(ELIFBA_DIR, 'out', `audition-${name}`);
  await rm(outDir, { recursive: true, force: true });
  const cacheDir = path.join(outDir, 'cache');
  await mkdir(cacheDir, { recursive: true });

  const lines = suite.script.map((text) => ({ text, role: suite.role }));

  for (const candidate of SOUND_CANDIDATES.concat(NARRATION_CANDIDATES).filter((c) =>
    suite.candidates.includes(c))) {
    const { files } = await synthesise(lines, {
      cacheDir,
      voices: { [suite.role]: candidate.voice },
      rates: { [suite.role]: candidate.rate },
    });
    await joinToMp3(
      lines.map((l) => files.get(spokenId(l))),
      cacheDir,
      path.join(outDir, `${candidate.id}.mp3`),
      candidate.id,
    );
    const inUse = lesson.voices[suite.role] === candidate.voice &&
      lesson.rates[suite.role] === candidate.rate;
    console.log(
      `${candidate.id.padEnd(24)} ${candidate.voice} @ ${candidate.rate}${inUse ? '   <- in use' : ''}`,
    );
  }

  await rm(cacheDir, { recursive: true, force: true });
  console.log(`\nsays: ${suite.script.join(' ')}`);
  console.log(`auditions in ${outDir}`);
}

main().catch((err) => {
  console.error(err.stack || String(err));
  process.exit(1);
});
