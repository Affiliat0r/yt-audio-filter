#!/usr/bin/env node
/**
 * Find a way of asking for the letter sounds that comes back usable.
 *
 * Two things are wrong with handing a voice a bare "بَ" at conversational
 * rate, and both are measurable rather than matters of taste:
 *
 *   1. It is far too short. 0.15-0.25s of speech is a flick; a child cannot
 *      hear it, let alone copy it.
 *   2. It is not consistent across the three harakat. Damma comes back around
 *      twice the length of fatha and kasra -- the voice stretches it into a
 *      long "oo" -- so the three sounds a child is meant to be contrasting are
 *      not presented alike.
 *
 * So sweep voice and rate, and score each candidate on those two properties:
 * how long the syllables are, and how far apart the three are from each other.
 * Neither says whether a sound is *correct* -- only a person can judge that --
 * but they do rule out the candidates that cannot be.
 *
 *   node elifba/test/sound-sweep.mjs
 */

import { execFile } from 'node:child_process';
import { mkdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import { spokenId } from '../timeline.mjs';
import { synthesise } from '../voice.mjs';

const execFileAsync = promisify(execFile);
const ELIFBA_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

const MARKS = ['َ', 'ِ', 'ُ']; // fatha, kasra, damma
const LETTERS = ['ا', 'ب', 'ت', 'م', 'س']; // elif be te mim sin

const CANDIDATES = [
  { id: 'zariyah -8% (current)', voice: 'ar-SA-ZariyahNeural', rate: '-8%' },
  { id: 'zariyah -40%', voice: 'ar-SA-ZariyahNeural', rate: '-40%' },
  { id: 'hamed -25% (SA male)', voice: 'ar-SA-HamedNeural', rate: '-25%' },
  { id: 'hamed -40% (SA male)', voice: 'ar-SA-HamedNeural', rate: '-40%' },
  { id: 'salma -40% (EG)', voice: 'ar-EG-SalmaNeural', rate: '-40%' },
  { id: 'shakir -40% (EG male)', voice: 'ar-EG-ShakirNeural', rate: '-40%' },
];

async function speechSeconds(file) {
  const { stderr } = await execFileAsync('ffmpeg', [
    '-hide_banner', '-nostats', '-i', file,
    '-af', 'silencedetect=noise=-45dB:d=0.06', '-f', 'null', '-',
  ]);
  const { stdout } = await execFileAsync('ffprobe', [
    '-v', 'error', '-show_entries', 'format=duration',
    '-of', 'default=nw=1:nk=1', file,
  ]);
  let silent = 0;
  for (const m of String(stderr).matchAll(/silence_duration: ([\d.]+)/g)) silent += Number(m[1]);
  return Math.max(0, Number(stdout.trim()) - silent);
}

function mean(xs) {
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

async function main() {
  const cacheDir = path.join(ELIFBA_DIR, 'out', 'sound-sweep');
  await rm(cacheDir, { recursive: true, force: true });
  await mkdir(cacheDir, { recursive: true });

  console.log('candidate              fatha  kasra  damma |  mean  spread  damma/fatha');
  console.log('-'.repeat(76));

  for (const c of CANDIDATES) {
    const lines = [];
    for (const letter of LETTERS) {
      for (const mark of MARKS) lines.push({ text: letter + mark, role: 'letters' });
    }
    const { files } = await synthesise(lines, {
      cacheDir, voices: { letters: c.voice }, rate: c.rate,
    });

    // Average each harakat across the sample letters, so one awkward letter
    // cannot decide the verdict.
    const perMark = [];
    for (const mark of MARKS) {
      const secs = [];
      for (const letter of LETTERS) {
        secs.push(await speechSeconds(files.get(spokenId({ text: letter + mark, role: 'letters' }))));
      }
      perMark.push(mean(secs));
    }

    const avg = mean(perMark);
    const spread = (Math.max(...perMark) - Math.min(...perMark)) / avg;
    console.log(
      c.id.padEnd(22),
      perMark.map((s) => s.toFixed(2).padStart(5)).join('  '), '|',
      avg.toFixed(2).padStart(5),
      (spread * 100).toFixed(0).padStart(6) + '%',
      (perMark[2] / perMark[0]).toFixed(2).padStart(11),
    );
  }

  console.log('\nspread = (longest - shortest) / mean across the three harakat; lower is more even');
}

main().catch((err) => {
  console.error(err.stack || String(err));
  process.exit(1);
});
