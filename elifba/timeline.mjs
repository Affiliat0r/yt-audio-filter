/**
 * Lesson -> timeline.
 *
 * Pure: given the lesson data and how long each spoken line actually turned
 * out to be, produce the ordered segment list that both the scene and the
 * audio track are built from. Keeping it pure is what lets the two agree --
 * the picture and the sound are two readings of this one array, so they
 * cannot drift.
 *
 * The per-letter shape is the Diyanet drill:
 *
 *     letter alone -> ustun -> esre -> otre -> "simdi sen soyle" -> the
 *     three marks again in silence, for the child to say.
 */

/** Seconds of quiet left after a spoken line before the segment ends. */
export const TAIL = Object.freeze({
  title: 0.9,
  letter: 1.0,
  harakat: 1.15,
  prompt: 0.5,
});

/** Floor on each segment, so a very short word still gets room to land. */
export const MIN = Object.freeze({
  title: 3.0,
  letter: 2.3,
  harakat: 2.5,
  prompt: 2.0,
});

/** The child's turn is silent, so its length is fixed rather than derived. */
export const REPEAT_SECONDS = 2.2;

/**
 * @param {number|undefined} spoken  measured length of the line, seconds
 * @param {'title'|'letter'|'harakat'|'prompt'} kind
 */
function beatLength(spoken, kind) {
  return Math.max(MIN[kind], (spoken || 0) + TAIL[kind]);
}

/**
 * @param {object} lesson              parsed lesson.json
 * @param {string[]} letterIds         which letters, in order
 * @param {Map<string, number>} spoken text -> measured seconds
 * @param {{title?: string}} [opts]
 * @returns {{title: string, totalSeconds: number, segments: object[]}}
 */
export function buildTimeline(lesson, letterIds, spoken, opts = {}) {
  const byId = new Map(lesson.letters.map((l) => [l.id, l]));
  const missing = letterIds.filter((id) => !byId.has(id));
  if (missing.length) {
    throw new Error(`timeline: no such letter(s) in lesson.json: ${missing.join(', ')}`);
  }
  if (lesson.harakat.length !== 3) {
    throw new Error(`timeline: expected 3 harakat, got ${lesson.harakat.length}`);
  }

  const title = opts.title || lesson.title || 'Elif Ba Öğreniyorum';
  const segments = [];
  let t = 0;

  const push = (seg) => {
    segments.push({ ...seg, start: Number(t.toFixed(6)) });
    t += seg.duration;
  };

  push({
    kind: 'title',
    text: title,
    say: null,
    speak: title,
    duration: beatLength(spoken.get(title), 'title'),
    accent: '#B08968',
  });

  for (const id of letterIds) {
    const letter = byId.get(id);

    // 1. the letter on its own, named
    push({
      kind: 'letter',
      glyph: letter.glyph,
      mark: '',
      harakatName: '',
      say: letter.name,
      speak: letter.name,
      step: 0,
      duration: beatLength(spoken.get(letter.name), 'letter'),
      accent: '#B08968',
    });

    // 2-4. the same letter under each harakat
    lesson.harakat.forEach((h, i) => {
      const syllable = letter.say[i];
      push({
        kind: 'harakat',
        glyph: letter.glyph,
        letterName: letter.name,
        mark: h.mark,
        harakatName: h.name,
        say: syllable,
        speak: syllable,
        step: i + 1,
        duration: beatLength(spoken.get(syllable), 'harakat'),
        accent: h.accent,
      });
    });

    // 5. hand it over
    push({
      kind: 'prompt',
      glyph: letter.glyph,
      letterName: letter.name,
      mark: '',
      text: lesson.prompt,
      say: null,
      speak: lesson.prompt,
      duration: beatLength(spoken.get(lesson.prompt), 'prompt'),
      accent: '#B08968',
    });

    // 6. the child's turn -- same three marks, no voice
    lesson.harakat.forEach((h, i) => {
      push({
        kind: 'repeat',
        glyph: letter.glyph,
        letterName: letter.name,
        mark: h.mark,
        harakatName: h.name,
        say: letter.say[i],
        speak: null,
        step: i + 1,
        duration: REPEAT_SECONDS,
        accent: h.accent,
      });
    });
  }

  return { title, totalSeconds: Number(t.toFixed(6)), segments };
}

/**
 * Every distinct line the voice has to say, in first-appearance order.
 * Deduplicated because letters share syllables -- "se, si, su" is both Se and
 * Sin -- and synthesising each once keeps a 30-letter build honest.
 *
 * @returns {string[]}
 */
export function spokenLines(lesson, letterIds, opts = {}) {
  const byId = new Map(lesson.letters.map((l) => [l.id, l]));
  const seen = new Set();
  const out = [];
  const add = (text) => {
    if (text && !seen.has(text)) {
      seen.add(text);
      out.push(text);
    }
  };

  add(opts.title || lesson.title || 'Elif Ba Öğreniyorum');
  add(lesson.prompt);
  for (const id of letterIds) {
    const letter = byId.get(id);
    if (!letter) continue;
    add(letter.name);
    letter.say.forEach(add);
  }
  return out;
}
