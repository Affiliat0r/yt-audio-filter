import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  LETTERS, MIN, NARRATION, REPEAT_SECONDS, TAIL,
  buildTimeline, spokenForm, spokenId, spokenLines,
} from '../timeline.mjs';

const ELIFBA_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const lesson = JSON.parse(readFileSync(path.join(ELIFBA_DIR, 'lesson.json'), 'utf8'));

/** Every line long enough that the tail, not the floor, decides the length. */
function durationsFor(lines, seconds = 4) {
  return new Map(lines.map((line) => [spokenId(line), seconds]));
}

function timelineFor(letterIds, seconds) {
  const lines = spokenLines(lesson, letterIds);
  return buildTimeline(lesson, letterIds, durationsFor(lines, seconds));
}

test('segments tile the timeline with no gap and no overlap', () => {
  const tl = timelineFor(['elif', 'be', 'te']);

  assert.equal(tl.segments[0].start, 0);
  for (let i = 1; i < tl.segments.length; i++) {
    const prev = tl.segments[i - 1];
    // This is the invariant the whole build rests on: the narration track is
    // built by concatenating one clip-plus-silence block per segment, so any
    // gap here would desynchronise the voice from the picture for the rest of
    // the video.
    assert.ok(
      Math.abs(tl.segments[i].start - (prev.start + prev.duration)) < 1e-6,
      `segment ${i} starts at ${tl.segments[i].start}, expected ${prev.start + prev.duration}`,
    );
  }

  const summed = tl.segments.reduce((acc, s) => acc + s.duration, 0);
  assert.ok(Math.abs(summed - tl.totalSeconds) < 1e-6);
});

test('each letter runs the Diyanet drill in order', () => {
  const tl = timelineFor(['be']);
  assert.deepEqual(
    tl.segments.map((s) => s.kind),
    ['title', 'letter', 'harakat', 'harakat', 'harakat', 'prompt', 'repeat', 'repeat', 'repeat'],
  );
  assert.deepEqual(
    tl.segments.filter((s) => s.harakatName).map((s) => s.harakatName),
    ['üstün', 'esre', 'ötre', 'üstün', 'esre', 'ötre'],
  );
  assert.deepEqual(
    tl.segments.filter((s) => s.kind === 'harakat').map((s) => s.say),
    ['be', 'bi', 'bu'],
  );
});

test('the mark shown is the one the harakat names', () => {
  const tl = timelineFor(['be']);
  const marks = tl.segments.filter((s) => s.kind === 'harakat').map((s) => s.mark);
  // U+064E fatha, U+0650 kasra, U+064F damma -- the three the unit teaches.
  assert.deepEqual(marks, ['َ', 'ِ', 'ُ']);
  for (const seg of tl.segments.filter((s) => s.kind === 'harakat')) {
    assert.equal(seg.glyph, 'ب', 'be should stay be under every mark');
  }
});

test('every spoken segment has a line that will be synthesised for it', () => {
  const letterIds = lesson.letters.map((l) => l.id);
  const all = spokenLines(lesson, letterIds);
  const lines = new Set(all.map(spokenId));
  const tl = buildTimeline(lesson, letterIds, durationsFor(all));

  for (const seg of tl.segments) {
    if (seg.speak === null) continue;
    // A segment whose line was never synthesised gets no clip, and buildTrack
    // would silently emit silence there instead of failing.
    assert.ok(lines.has(spokenId(seg.speak)), `nothing will be synthesised for ${spokenId(seg.speak)}`);
  }
});

test('spokenLines deduplicates syllables shared between letters', () => {
  // Se and Sin are both read "se, si, su" in Turkish, so the pair must not
  // cost six clips.
  const lines = spokenLines(lesson, ['se', 'sin']);
  const ids = lines.map(spokenId);
  assert.equal(new Set(ids).size, ids.length, 'lines should be unique');
  const texts = lines.map((l) => l.text);
  assert.ok(texts.includes('Se.') && texts.includes('Sin.'));
  // Both letters are drawn differently, so their sounds are different clips
  // even though the book reads both "se, si, su".
  assert.ok(texts.includes('سَ') && texts.includes('ثَ'));
});

test('narration is punctuated for prosody; the Arabic lines are left bare', () => {
  // The full stop gives a short Turkish line a falling contour instead of a
  // flat one, and adding a second to a line already ending in "!" would be
  // read aloud as a stumble.
  assert.equal(spokenForm('Be'), 'Be.');
  assert.equal(spokenForm('Şimdi sen söyle!'), 'Şimdi sen söyle!');
  assert.equal(spokenForm(spokenForm('Be')), 'Be.');

  for (const line of spokenLines(lesson, ['be'])) {
    if (line.role === NARRATION) {
      assert.match(line.text, /[!?.]$/, `${JSON.stringify(line.text)} has no terminal punctuation`);
      assert.doesNotMatch(line.text, /\.\.$/, `${JSON.stringify(line.text)} was punctuated twice`);
    } else {
      // The Arabic lines go to the voice exactly as they were auditioned.
      assert.doesNotMatch(line.text, /[.!?]/, 'Arabic lines must stay bare');
    }
  }
});

test('the sound is spoken in Arabic, the name in Turkish', () => {
  const tl = timelineFor(['ha']);

  const name = tl.segments.find((s) => s.kind === 'letter');
  assert.equal(name.speak.role, NARRATION);
  assert.equal(name.speak.text, 'Ha.', "the book's Turkish name for the letter");

  // Turkish has one "ha" for ح, خ and ه, so the sound cannot come from the
  // Turkish voice. It reads the Arabic that is drawn on the card.
  const sounds = tl.segments.filter((s) => s.kind === 'harakat');
  assert.deepEqual(sounds.map((s) => s.speak.role), [LETTERS, LETTERS, LETTERS]);
  assert.deepEqual(sounds.map((s) => s.speak.text), ['حَ', 'حِ', 'حُ']);
  for (const seg of sounds) {
    assert.equal(seg.speak.text, seg.glyph + seg.mark, 'the voice reads what is on the card');
  }

  const prompt = tl.segments.find((s) => s.kind === 'prompt');
  assert.equal(prompt.speak.role, NARRATION);
});

test('a segment is the spoken line plus its tail, or the floor if that is longer', () => {
  const long = timelineFor(['be'], 4);
  const harakat = long.segments.find((s) => s.kind === 'harakat');
  assert.ok(Math.abs(harakat.duration - (4 + TAIL.harakat)) < 1e-9);

  const short = timelineFor(['be'], 0.2);
  const floored = short.segments.find((s) => s.kind === 'harakat');
  assert.equal(floored.duration, MIN.harakat);
});

test("the child's turn is silent and fixed length", () => {
  const tl = timelineFor(['be'], 9);
  for (const seg of tl.segments.filter((s) => s.kind === 'repeat')) {
    assert.equal(seg.speak, null);
    assert.equal(seg.duration, REPEAT_SECONDS);
  }
});

test('the prompt beat still carries a letter to point at', () => {
  const tl = timelineFor(['be']);
  const prompt = tl.segments.find((s) => s.kind === 'prompt');
  assert.equal(prompt.glyph, 'ب');
  assert.equal(prompt.mark, '');
  assert.equal(prompt.text, lesson.prompt);
});

test('the opening beat carries a letter, since every beat shows the card', () => {
  // Nothing is written in Latin any more, so a title beat with no glyph would
  // open the video on an empty card.
  const tl = timelineFor(['te', 'be']);
  const title = tl.segments[0];
  assert.equal(title.kind, 'title');
  assert.equal(title.glyph, 'ت', "should be the lesson's first letter");
  assert.equal(title.mark, '');
});

test('every segment has a glyph for the card to show', () => {
  const tl = timelineFor(['elif', 'be', 'te']);
  for (const seg of tl.segments) {
    assert.ok(seg.glyph, `${seg.kind} at ${seg.start}s has no glyph`);
  }
});

test('an unknown letter id is rejected before anything is rendered', () => {
  assert.throws(
    () => buildTimeline(lesson, ['be', 'nosuchletter'], new Map()),
    /nosuchletter/,
  );
});

test('lesson.json covers the whole alphabet with three readings each', () => {
  assert.equal(lesson.letters.length, 30, '28 letters plus lamelif and hemze');
  assert.equal(lesson.harakat.length, 3);
  for (const letter of lesson.letters) {
    assert.equal(letter.say.length, 3, `${letter.id} needs a reading per harakat`);
    assert.ok(letter.glyph.length >= 1 && letter.name.length >= 2, `${letter.id} is incomplete`);
  }
  assert.equal(new Set(lesson.letters.map((l) => l.id)).size, 30, 'ids must be unique');
});
