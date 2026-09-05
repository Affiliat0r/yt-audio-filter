#!/usr/bin/env node
/**
 * Voice audition.
 *
 * Renders the exact lines the lesson uses -- isolated letter names and
 * syllables, then the hand-over -- in each candidate voice, so they can be
 * compared by ear rather than by voice-list adjective.
 *
 * Isolated single syllables are the hard case and the reason the first pass
 * sounded mechanical: a one-syllable utterance gives a TTS no sentence to put
 * an intonation contour on, so it comes out flat however good the model is.
 * That is why the candidates below vary the rate and the trailing punctuation
 * as well as the voice -- a trailing full stop buys a falling contour that a
 * bare token does not get.
 *
 *   node elifba/audition.mjs
 */

import { execFile } from 'node:child_process';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import { synthesise } from './voice.mjs';

const execFileAsync = promisify(execFile);
const ELIFBA_DIR = path.dirname(fileURLToPath(import.meta.url));
const OUT_DIR = path.join(ELIFBA_DIR, 'out', 'audition');

/** The lines a real lesson speaks, in the order it speaks them. */
const SCRIPT = ['Be', 'be', 'bi', 'bu', 'Şimdi sen söyle!'];

const CANDIDATES = [
  { id: '1-current-emel-slow', voice: 'tr-TR-EmelNeural', rate: '-20%', punctuate: false },
  { id: '2-emel-natural-rate', voice: 'tr-TR-EmelNeural', rate: '-8%', punctuate: true },
  { id: '3-emma-multilingual', voice: 'en-US-EmmaMultilingualNeural', rate: '-8%', punctuate: true },
  { id: '4-ava-multilingual', voice: 'en-US-AvaMultilingualNeural', rate: '-8%', punctuate: true },
  { id: '5-ahmet-male', voice: 'tr-TR-AhmetNeural', rate: '-8%', punctuate: true },
];

/** Seconds of quiet between lines, so each is heard on its own. */
const GAP = 0.7;

async function main() {
  await rm(OUT_DIR, { recursive: true, force: true });
  await mkdir(OUT_DIR, { recursive: true });
  const cacheDir = path.join(OUT_DIR, 'cache');

  for (const candidate of CANDIDATES) {
    // A trailing full stop on a bare syllable is the cheapest prosody we can
    // buy: it turns "bi" from a flat token into a falling contour.
    const lines = SCRIPT.map((line) =>
      candidate.punctuate && !/[!?.]$/.test(line) ? `${line}.` : line);

    const { files } = await synthesise(lines, {
      cacheDir,
      voice: candidate.voice,
      rate: candidate.rate,
    });

    const parts = [];
    const silence = path.join(cacheDir, `gap_${GAP}.wav`);
    await execFileAsync('ffmpeg', [
      '-hide_banner', '-loglevel', 'error', '-y',
      '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
      '-t', String(GAP), '-c:a', 'pcm_s16le', silence,
    ]);

    for (const line of lines) {
      const wav = path.join(cacheDir, `${candidate.id}_${parts.length}.wav`);
      await execFileAsync('ffmpeg', [
        '-hide_banner', '-loglevel', 'error', '-y',
        '-i', files.get(line),
        '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', wav,
      ]);
      parts.push(wav, silence);
    }

    const listFile = path.join(cacheDir, `${candidate.id}.txt`);
    await writeFile(
      listFile,
      parts.map((p) => `file '${p.replace(/\\/g, '/')}'`).join('\n'),
      'utf8',
    );

    const outPath = path.join(OUT_DIR, `${candidate.id}.mp3`);
    await execFileAsync('ffmpeg', [
      '-hide_banner', '-loglevel', 'error', '-y',
      '-f', 'concat', '-safe', '0', '-i', listFile,
      '-c:a', 'libmp3lame', '-b:a', '160k', outPath,
    ]);

    console.log(`${candidate.id.padEnd(22)} ${candidate.voice} @ ${candidate.rate}`);
  }

  await rm(cacheDir, { recursive: true, force: true });
  console.log(`\n${CANDIDATES.length} auditions in ${OUT_DIR}`);
}

main().catch((err) => {
  console.error(err.stack || String(err));
  process.exit(1);
});
