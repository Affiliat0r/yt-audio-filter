/*
 * The elifba scene.
 *
 * Implements the `window.__movie` contract from visuals/CONTRACT.md, so the
 * existing capture harness drives it unchanged. The one rule that matters
 * here: renderAt(t) is a pure function of t. No CSS transitions, no CSS
 * animations, no rAF, no Date.now() -- every animated value below, the whole
 * garden included, is computed from the frame's own timestamp. A CSS
 * animation would tie the frame to how long the machine took to reach it,
 * which is exactly the bug that makes a re-render disagree with the first one.
 *
 * Nothing on screen is written in Latin script. A three-year-old cannot read
 * it, and a transliteration under the letter invites reading that instead of
 * the letter. What those words used to carry is now carried by the voice and
 * by the accent colour.
 */
(function () {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';

  /** Seconds the card takes to dissolve in or out where the letter changes. */
  var CARD_FADE = 0.5;
  /** Seconds the harakat takes to dissolve in, and to dissolve back out. */
  var MARK_IN = 0.45;
  var MARK_OUT = 0.4;
  /** Seconds the accent takes to travel from the previous beat's colour. */
  var ACCENT_FADE = 0.55;

  var el = {};
  var timeline = null;
  var flowers = [];

  // -- easing (pure) --------------------------------------------------------

  function clamp01(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
  function easeOutCubic(x) { x = clamp01(x); return 1 - Math.pow(1 - x, 3); }
  function easeOutBack(x) {
    x = clamp01(x);
    var c1 = 1.70158, c3 = c1 + 1;
    return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
  }

  /*
   * Blend two hex colours. The garden is the one thing that persists across a
   * beat change -- it never fades -- so switching its tint instantly would
   * leave the only hard cut in the video sitting on eighteen flowers.
   */
  function mixHex(a, b, amount) {
    if (a === b) return b;
    var pa = parseInt(a.slice(1), 16);
    var pb = parseInt(b.slice(1), 16);
    var m = clamp01(amount);
    return 'rgb(' +
      Math.round(((pa >> 16) & 255) * (1 - m) + ((pb >> 16) & 255) * m) + ', ' +
      Math.round(((pa >> 8) & 255) * (1 - m) + ((pb >> 8) & 255) * m) + ', ' +
      Math.round((pa & 255) * (1 - m) + (pb & 255) * m) + ')';
  }

  /** mulberry32 -- small, seeded, and identical on every machine. */
  function makeRng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // -- segment lookup -------------------------------------------------------

  /*
   * The segment covering time t. Linear scan: a lesson is a few hundred
   * segments at most and the scan keeps the lookup obviously correct. The
   * last segment also owns any time past the end, so a frame count rounded
   * up can never land on an undefined frame.
   */
  function segmentAt(t) {
    var segs = timeline.segments;
    for (var i = 0; i < segs.length; i++) {
      if (t < segs[i].start + segs[i].duration) return segs[i];
    }
    return segs[segs.length - 1];
  }

  // -- the garden -----------------------------------------------------------

  /*
   * Flowers are placed once, down the two margins either side of the card,
   * and only transformed afterwards. Positions come from a fixed seed, so the
   * arrangement is identical in every render -- a garden that reshuffled
   * between letters would become its own distraction.
   */
  function buildGarden(viewWidth, cardHalfWidth) {
    var rng = makeRng(0x5eed);
    var built = [];
    var margin = 3;
    var cols = 3;
    var rows = 3;

    // A jittered grid rather than free random placement. Pure random clumps --
    // it put five blooms in one corner and left the opposite margin bare --
    // and a clump beside the card competes with the letter for attention,
    // which is the one thing the background must never do.
    var regions = [
      { x0: margin, x1: viewWidth / 2 - cardHalfWidth - 3 },
      { x0: viewWidth / 2 + cardHalfWidth + 3, x1: viewWidth - margin },
    ];

    for (var s = 0; s < regions.length; s++) {
      var region = regions[s];
      var cellW = (region.x1 - region.x0) / cols;
      var cellH = 88 / rows;
      for (var c = 0; c < cols; c++) {
        for (var r = 0; r < rows; r++) {
          // Small on purpose: at a radius of 6 the bloom is about a tenth of
          // the frame height, which reads as pattern. Much larger and it reads
          // as subject.
          var rad = 2.2 + rng() * 3.8;
          built.push({
            x: region.x0 + cellW * (c + 0.5) + (rng() - 0.5) * cellW * 0.55,
            y: 6 + cellH * (r + 0.5) + (rng() - 0.5) * cellH * 0.55,
            r: rad,
            petals: rng() < 0.5 ? 5 : 6,
            spin: (rng() < 0.5 ? -1 : 1) * (5 + rng() * 9),
            phase: rng(),
            breathe: 0.4 + rng() * 0.35,
            bob: 0.5 + rng() * 1.2,
            bobSpeed: 0.25 + rng() * 0.3,
            alpha: 0.13 + (rad / 6) * 0.14,
          });
        }
      }
    }
    return built;
  }

  function makeFlowerNode(f) {
    var g = document.createElementNS(SVG_NS, 'g');
    for (var i = 0; i < f.petals; i++) {
      var petal = document.createElementNS(SVG_NS, 'ellipse');
      petal.setAttribute('cx', '0');
      petal.setAttribute('cy', String(-f.r * 0.54));
      petal.setAttribute('rx', String(f.r * 0.27));
      petal.setAttribute('ry', String(f.r * 0.54));
      petal.setAttribute('transform', 'rotate(' + (i * 360) / f.petals + ')');
      g.appendChild(petal);
    }
    // The centre keeps its own warm colour, so a bloom still reads as a flower
    // when the petals take the harakat's accent.
    var core = document.createElementNS(SVG_NS, 'circle');
    core.setAttribute('r', String(f.r * 0.26));
    core.setAttribute('fill', '#E8B455');
    g.appendChild(core);
    return g;
  }

  function paintGarden(t, accent) {
    // One slow bloom as the video opens, then the garden simply lives.
    var intro = easeOutCubic(t / 1.8);
    for (var i = 0; i < flowers.length; i++) {
      var f = flowers[i];
      var turn = f.spin * t + f.phase * 360;
      var breathe = 1 + 0.07 * Math.sin(t * f.breathe + f.phase * 6.283);
      var dy = f.bob * Math.sin(t * f.bobSpeed + f.phase * 6.283);
      var scale = breathe * (0.55 + 0.45 * intro);

      f.node.setAttribute(
        'transform',
        'translate(' + f.x.toFixed(3) + ' ' + (f.y + dy).toFixed(3) + ') ' +
          'rotate(' + turn.toFixed(3) + ') scale(' + scale.toFixed(4) + ')',
      );
      f.node.setAttribute('fill', accent);
      f.node.setAttribute('opacity', (f.alpha * intro).toFixed(4));
    }
  }

  // -- painting -------------------------------------------------------------

  function setText(node, text) {
    if (node.textContent !== text) node.textContent = text;
  }

  function paint(seg, lt, t) {
    var dur = seg.duration;
    var accent = mixHex(seg.prevAccent || seg.accent, seg.accent,
                        easeOutCubic(lt / ACCENT_FADE));

    // The garden deliberately does not fade with the beats -- it just lives,
    // and only its tint travels.
    paintGarden(t, accent);

    el.glowInner.setAttribute('stop-color', accent);
    el.glowOuter.setAttribute('stop-color', accent);

    // The card dissolves only where the letter behind it actually changes.
    // Within one letter the glyph is the same from the naming beat to the last
    // repeat, so dipping between those beats would blink it for no reason.
    var fin = seg.fadeIn ? easeOutCubic(lt / CARD_FADE) : 1;
    var fout = seg.fadeOut ? easeOutCubic((dur - lt) / CARD_FADE) : 1;
    var vis = Math.min(fin, fout);

    el.card.style.opacity = String(vis);
    el.card.style.transform = 'translateX(-50%) scale(' +
      (seg.fadeIn ? 0.95 + 0.05 * easeOutBack(lt / 0.6) : 1) + ')';

    setText(el.glyphBase, seg.glyph);
    setText(el.glyphMark, seg.glyph + (seg.mark || ''));
    el.glyphMark.style.color = accent;

    // The mark fades in a beat after the letter has settled, so the child sees
    // "the letter I know" and then "the thing that was added to it" -- and it
    // fades back out before the beat ends, so ustun -> esre reads as a
    // dissolve even across the beats where the card itself holds.
    //
    // Only opacity is animated here. The accent layer also draws the base
    // letter underneath, hidden by the pixel-identical ink layer on top;
    // moving or scaling it would slide that copy out from behind its cover.
    var markT = seg.mark
      ? Math.min(easeOutCubic((lt - 0.2) / MARK_IN), easeOutCubic((dur - lt) / MARK_OUT))
      : 0;
    el.glyphMark.style.opacity = String(markT * vis);
    el.glyphBase.style.opacity = String(vis);

    el.glowInner.setAttribute('stop-opacity', String((0.08 + 0.16 * markT) * vis));

    // "Your turn" has to be said without words now, so it is said with light:
    // the card's outline breathes for exactly the beats the child should be
    // speaking over.
    if (seg.kind === 'prompt' || seg.kind === 'repeat') {
      var pulse = 0.5 + 0.5 * Math.sin(lt * Math.PI * 2 - Math.PI / 2);
      el.ring.style.opacity = String((0.15 + 0.4 * pulse) * vis);
      el.ring.style.borderColor = accent;
      el.ring.style.transform = 'translateX(-50%) scale(' + (0.985 + 0.02 * pulse) + ')';
    } else {
      el.ring.style.opacity = '0';
    }

  }

  // -- contract -------------------------------------------------------------

  window.__movie = {
    ready: false,

    init: async function (cfg) {
      timeline = window.__timeline;
      if (!timeline || !Array.isArray(timeline.segments) || timeline.segments.length === 0) {
        throw new Error('elifba scene: window.__timeline has no segments');
      }

      var ids = ['stage', 'garden', 'glowInner', 'glowOuter', 'ring', 'card',
                 'glyphBase', 'glyphMark'];
      for (var i = 0; i < ids.length; i++) {
        el[ids[i]] = document.getElementById(ids[i]);
        if (!el[ids[i]]) throw new Error('elifba scene: missing #' + ids[i]);
      }

      el.stage.style.width = cfg.width + 'px';
      el.stage.style.height = cfg.height + 'px';
      el.stage.style.setProperty('--u', cfg.height / 100 + 'px');

      // The garden works in the same 1%-of-height units as the CSS, so a
      // flower at x=20 lands where `calc(var(--u) * 20)` would put it. Taking
      // the viewBox width from the real aspect ratio keeps circles circular at
      // any output size instead of stretching them.
      var viewWidth = (cfg.width / cfg.height) * 100;
      el.garden.setAttribute('viewBox', '0 0 ' + viewWidth.toFixed(4) + ' 100');
      flowers = buildGarden(viewWidth, 34);
      for (var j = 0; j < flowers.length; j++) {
        flowers[j].node = makeFlowerNode(flowers[j]);
        el.garden.appendChild(flowers[j].node);
      }

      // A frame captured before the Arabic face has loaded silently ships
      // system-font letterforms, so block on it and fail loudly if it is
      // genuinely absent.
      await document.fonts.load('400 100px ElifbaArabic');
      await document.fonts.ready;
      if (!document.fonts.check('400 100px ElifbaArabic')) {
        throw new Error('elifba scene: the Arabic font never loaded; refusing to render fallback glyphs');
      }

      this.ready = true;
    },

    renderAt: async function (t) {
      var seg = segmentAt(t);
      paint(seg, t - seg.start, t);
      // Force a synchronous layout flush so the screenshot that follows sees
      // the values written above rather than the previous frame's.
      void el.stage.getBoundingClientRect();
    },
  };
})();
