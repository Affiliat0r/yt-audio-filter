#!/usr/bin/env node
/**
 * Voice audition.
 *
 * Renders the exact lines a lesson speaks in each candidate voice, so they can
 * be compared by ear rather than by voice-list adjective.
 *
 *   node elifba/audition.mjs --suite turkish
 *   node elifba/audition.mjs --suite arabic
 *
 * The `turkish` suite asks which voice sounds least mechanical. Isolated
 * single syllables are the hard case there: a one-syllable utterance gives a
 * TTS no sentence to hang an intonation contour on, so it comes out flat
 * however good the model is, which is why the candidates vary rate and
 * trailing punctuation as well as voice.
 *
 * The `arabic` suite asks a different question: whether the letters should be
 * sounded by an Arabic voice reading Arabic script. A Turkish voice cannot
 * distinguish the letters Turkish collapses -- it says the same "ha" for ح, خ
 * and ه, the same "sa" for س and ص -- so the suite deliberately uses the four
 * letters where that collapse is audible, and compares full-Arabic against
 * keeping the Diyanet's Turkish letter *names* over Arabic letter *sounds*.
 */

import { execFile } from 'node:child_process';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import { synthesise } from './voice.mjs';

const execFileAsync = promisify(execFile);
const ELIFBA_DIR = path.dirname(fileURLToPath(import.meta.url));

/** Seconds of quiet between lines, so each is heard on its own. */
const GAP = 0.7;

// --------------------------------------------------------------------------
// suite: turkish -- which voice, and how fast
// --------------------------------------------------------------------------

const TURKISH_SCRIPT = ['Be', 'be', 'bi', 'bu', 'Şimdi sen söyle!'];

const TURKISH_CANDIDATES = [
  { id: '1-current-emel-slow', voice: 'tr-TR-EmelNeural', rate: '-20%', punctuate: false },
  { id: '2-emel-natural-rate', voice: 'tr-TR-EmelNeural', rate: '-8%', punctuate: true },
  { id: '3-emma-multilingual', voice: 'en-US-EmmaMultilingualNeural', rate: '-8%', punctuate: true },
  { id: '4-ava-multilingual', voice: 'en-US-AvaMultilingualNeural', rate: '-8%', punctuate: true },
  { id: '5-ahmet-male', voice: 'tr-TR-AhmetNeural', rate: '-8%', punctuate: true },
];

// --------------------------------------------------------------------------
// suite: arabic -- should the letters be sounded in Arabic
// --------------------------------------------------------------------------

/** U+064E fatha, U+0650 kasra, U+064F damma. */
const MARKS = ['َ', 'ِ', 'ُ'];

/*
 * Four letters chosen because Turkish collapses them and Arabic does not:
 * be is the control, ha is ح (not خ and not ه), sad is ص (not س), and ayn is
 * ع, which a Turkish voice renders as a bare vowel. If an Arabic voice is
 * worth switching to, it is on these that you will hear it.
 */
const PHONEME_LETTERS = [
  { glyph: 'ب', turkish: 'Be', arabic: 'باء' },
  { glyph: 'ح', turkish: 'Ha', arabic: 'حاء' },
  { glyph: 'ص', turkish: 'Sad', arabic: 'صاد' },
  { glyph: 'ع', turkish: 'Ayn', arabic: 'عين' },
];

const ARABIC_CANDIDATES = [
  {
    id: 'A-all-arabic-zariyah',
    nameLang: 'arabic',
    nameVoice: 'ar-SA-ZariyahNeural',
    sayVoice: 'ar-SA-ZariyahNeural',
  },
  {
    id: 'B-all-arabic-salma-egyptian',
    nameLang: 'arabic',
    nameVoice: 'ar-EG-SalmaNeural',
    sayVoice: 'ar-EG-SalmaNeural',
  },
  {
    // The Diyanet book names the letters in Turkish. This keeps that and
    // borrows the Arabic voice only for the sound the mark makes.
    id: 'C-turkish-name-arabic-sound',
    nameLang: 'turkish',
    nameVoice: 'tr-TR-EmelNeural',
    sayVoice: 'ar-SA-ZariyahNeural',
  },
];

// --------------------------------------------------------------------------

/** Concatenate clips with a gap between each, into one mp3. */
async function joinToMp3(entries, cacheDir, outPath, id) {
  const silence = path.join(cacheDir, 'gap.wav');
  await execFileAsync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
    '-t', String(GAP), '-c:a', 'pcm_s16le', silence,
  ]);

  const parts = [];
  for (const clip of entries) {
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

async function runTurkish(outDir, cacheDir) {
  for (const candidate of TURKISH_CANDIDATES) {
    // A trailing full stop on a bare syllable is the cheapest prosody we can
    // buy: it turns "bi" from a flat token into a falling contour.
    const lines = TURKISH_SCRIPT.map((line) =>
      candidate.punctuate && !/[!?.]$/.test(line) ? `${line}.` : line);

    const { files } = await synthesise(lines, {
      cacheDir, voice: candidate.voice, rate: candidate.rate,
    });
    await joinToMp3(lines.map((l) => files.get(l)), cacheDir,
                    path.join(outDir, `${candidate.id}.mp3`), candidate.id);
    console.log(`${candidate.id.padEnd(30)} ${candidate.voice} @ ${candidate.rate}`);
  }
}

async function runArabic(outDir, cacheDir) {
  for (const candidate of ARABIC_CANDIDATES) {
    const clips = [];

    for (const letter of PHONEME_LETTERS) {
      const name = candidate.nameLang === 'arabic' ? letter.arabic : `${letter.turkish}.`;
      const { files: nameFiles } = await synthesise([name], {
        cacheDir, voice: candidate.nameVoice, rate: '-8%',
      });
      clips.push(nameFiles.get(name));

      // The syllable is just the letter carrying the mark -- exactly what is
      // drawn on the card -- so the voice is reading the same thing the child
      // is looking at rather than a transliteration of it.
      const syllables = MARKS.map((mark) => letter.glyph + mark);
      const { files: sayFiles } = await synthesise(syllables, {
        cacheDir, voice: candidate.sayVoice, rate: '-8%',
      });
      for (const syllable of syllables) clips.push(sayFiles.get(syllable));
    }

    await joinToMp3(clips, cacheDir, path.join(outDir, `${candidate.id}.mp3`), candidate.id);
    console.log(
      `${candidate.id.padEnd(30)} name=${candidate.nameVoice} say=${candidate.sayVoice}`,
    );
  }
}

async function main() {
  const suiteArg = process.argv.indexOf('--suite');
  const suite = suiteArg === -1 ? 'turkish' : process.argv[suiteArg + 1];
  if (suite !== 'turkish' && suite !== 'arabic') {
    throw new Error(`--suite must be "turkish" or "arabic", got ${JSON.stringify(suite)}`);
  }

  const outDir = path.join(ELIFBA_DIR, 'out', `audition-${suite}`);
  await rm(outDir, { recursive: true, force: true });
  await mkdir(outDir, { recursive: true });
  const cacheDir = path.join(outDir, 'cache');
  await mkdir(cacheDir, { recursive: true });

  if (suite === 'turkish') await runTurkish(outDir, cacheDir);
  else await runArabic(outDir, cacheDir);

  await rm(cacheDir, { recursive: true, force: true });
  console.log(`\nauditions in ${outDir}`);
}

main().catch((err) => {
  console.error(err.stack || String(err));
  process.exit(1);
});
