#!/usr/bin/env node
/**
 * Can a harakat be drawn on its own?
 *
 * The fix for mark placement is to take the mark off the letter and position
 * it by hand, which means rendering a combining character with no base. Some
 * shapers insert a dotted circle (U+25CC) when they see that, which would be a
 * non-starter. Chrome's behaviour is the only thing that matters here, so ask
 * Chrome rather than the spec.
 *
 * Prints, per mark, the measured ink box of the mark alone against the ink box
 * of a lone dotted circle. If the shaper is adding a circle the mark's box
 * comes back the same size as the circle's; if it is not, it comes back small.
 */

import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_DIR = path.dirname(path.dirname(HERE));
const requireFromVisuals = createRequire(
  pathToFileURL(path.join(REPO_DIR, 'visuals', 'package.json')),
);
const puppeteer = requireFromVisuals('puppeteer-core');

const SAMPLES = [
  ['fatha alone', '\u064E'],
  ['kasra alone', '\u0650'],
  ['damma alone', '\u064F'],
  ['dotted circle', '\u25CC'],
  ['dotted+fatha', '\u25CC\u064E'],
  ['be alone', '\u0628'],
  ['be+fatha', '\u0628\u064E'],
];

const PAGE = `<!doctype html><meta charset="utf-8">
<style>
  @font-face { font-family:'ElifbaArabic';
    src: local('Amiri'), local('Amiri Regular'),
         url('file:///C:/Windows/Fonts/Amiri-Regular.ttf') format('truetype'); }
  body { margin:0 }
</style><canvas id="c" width="400" height="400"></canvas>`;

function measureInPage(samples) {
  const canvas = document.getElementById('c');
  const ctx = canvas.getContext('2d');
  const font = '400 200px ElifbaArabic';
  ctx.font = font;
  return samples.map(([label, text]) => {
    const m = ctx.measureText(text);
    return {
      label,
      codepoints: [...text].map((ch) => ch.codePointAt(0).toString(16)),
      advance: Number(m.width.toFixed(2)),
      left: Number((m.actualBoundingBoxLeft ?? 0).toFixed(2)),
      right: Number((m.actualBoundingBoxRight ?? 0).toFixed(2)),
      ascent: Number((m.actualBoundingBoxAscent ?? 0).toFixed(2)),
      descent: Number((m.actualBoundingBoxDescent ?? 0).toFixed(2)),
    };
  });
}

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH ||
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  headless: true,
  args: ['--allow-file-access-from-files'],
});
try {
  const page = await browser.newPage();
  await page.setContent(PAGE, { waitUntil: 'load' });
  await page.evaluate(async () => {
    await document.fonts.load('400 200px ElifbaArabic');
    await document.fonts.ready;
    return document.fonts.check('400 200px ElifbaArabic');
  });
  const rows = await page.evaluate(measureInPage, SAMPLES);
  console.log('label           codepoints        advance   left  right ascent descent  inkW  inkH');
  for (const r of rows) {
    const inkW = (r.left + r.right).toFixed(1);
    const inkH = (r.ascent + r.descent).toFixed(1);
    console.log(
      r.label.padEnd(15),
      r.codepoints.join('+').padEnd(16),
      String(r.advance).padStart(7),
      String(r.left).padStart(6),
      String(r.right).padStart(6),
      String(r.ascent).padStart(6),
      String(r.descent).padStart(7),
      String(inkW).padStart(5),
      String(inkH).padStart(5),
    );
  }
} finally {
  await browser.close();
}
