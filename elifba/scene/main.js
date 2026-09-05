/*
 * The elifba scene.
 *
 * Implements the `window.__movie` contract from visuals/CONTRACT.md, so the
 * existing capture harness drives it unchanged. The one rule that matters
 * here: renderAt(t) is a pure function of t. No CSS transitions, no CSS
 * animations, no rAF, no Date.now() -- every animated value below is computed
 * from the frame's own timestamp. A CSS transition would tie the frame to how
 * long the machine took to get there, which is exactly the bug that makes a
 * re-render disagree with the first one.
 */
(function () {
  'use strict';

  var el = {};
  var timeline = null;

  // -- easing (pure) --------------------------------------------------------

  function clamp01(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
  function easeOutCubic(x) { x = clamp01(x); return 1 - Math.pow(1 - x, 3); }
  function easeOutBack(x) {
    x = clamp01(x);
    var c1 = 1.70158, c3 = c1 + 1;
    return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
  }

  /** Fade in at the head of a segment and back out at its tail. */
  function envelope(lt, dur, inDur, outDur) {
    return Math.min(easeOutCubic(lt / inDur), easeOutCubic((dur - lt) / outDur));
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

  // -- painting -------------------------------------------------------------

  function setText(node, text) {
    if (node.textContent !== text) node.textContent = text;
  }

  function paintDots(activeIndex, total, accent, allLit) {
    if (el.dots.childElementCount !== total) {
      el.dots.textContent = '';
      for (var i = 0; i < total; i++) {
        var d = document.createElement('div');
        d.className = 'dot';
        el.dots.appendChild(d);
      }
    }
    for (var j = 0; j < total; j++) {
      var dot = el.dots.children[j];
      var on = allLit || j <= activeIndex;
      var isActive = j === activeIndex;
      dot.style.background = on ? accent : '#E3D2BC';
      dot.style.opacity = on && !isActive ? '0.45' : '1';
      dot.style.transform = isActive ? 'scale(1.45)' : 'scale(1)';
    }
  }

  function paint(seg, lt) {
    var accent = seg.accent || '#B08968';
    var dur = seg.duration;

    // Everything off by default; each branch turns on only what it needs.
    el.card.style.opacity = '0';
    el.letterName.style.opacity = '0';
    el.harakatName.style.opacity = '0';
    el.say.style.opacity = '0';
    el.prompt.style.opacity = '0';

    el.glowInner.setAttribute('stop-color', accent);
    el.glowOuter.setAttribute('stop-color', accent);

    if (seg.kind === 'title') {
      var tv = envelope(lt, dur, 0.4, 0.35);
      // A gentle breathe so a held beat does not read as a frozen frame.
      var breathe = 1 + 0.02 * Math.sin((lt / dur) * Math.PI);
      el.prompt.style.opacity = String(tv);
      el.prompt.style.top = 'calc(var(--u) * 36.5)';
      el.prompt.style.fontSize = 'calc(var(--u) * 8.5)';
      el.prompt.style.transform =
        'scale(' + (0.94 + 0.06 * easeOutBack(lt / 0.45)) * breathe + ')';
      setText(el.prompt, seg.text || '');
      el.glowInner.setAttribute('stop-opacity', String(0.16 * tv));
      paintDots(-1, 4, accent, false);
      return;
    }

    // -- letter / harakat / repeat: the card is on screen --------------------

    var vis = Math.min(easeOutCubic(lt / 0.32), easeOutCubic((dur - lt) / 0.22));

    el.card.style.opacity = String(vis);
    el.card.style.transform =
      'translateX(-50%) scale(' + (0.95 + 0.05 * easeOutBack(lt / 0.45)) + ')';

    setText(el.glyphBase, seg.glyph);
    setText(el.glyphMark, seg.glyph + (seg.mark || ''));
    el.glyphMark.style.color = accent;

    // The mark fades in a beat after the letter has settled, so the child sees
    // "the letter I know" and then "the thing that was added to it".
    //
    // Only opacity is animated here. The accent layer also draws the base
    // letter underneath, hidden by the pixel-identical ink layer on top;
    // moving or scaling it would slide that copy out from behind its cover.
    var markT = seg.mark ? easeOutCubic((lt - 0.22) / 0.42) : 0;
    el.glyphMark.style.opacity = String(markT * vis);
    el.glyphBase.style.opacity = String(vis);

    el.glowInner.setAttribute('stop-opacity', String((0.08 + 0.14 * markT) * vis));

    if (seg.letterName) {
      // On a harakat beat `say` holds the syllable, so the letter's own name
      // would otherwise vanish from the screen just as it is being combined.
      el.letterName.style.opacity = String(easeOutCubic((lt - 0.28) / 0.34) * vis);
      setText(el.letterName, seg.letterName);
    }

    if (seg.harakatName) {
      var hv = easeOutCubic((lt - 0.34) / 0.36) * vis;
      el.harakatName.style.opacity = String(hv);
      el.harakatName.style.color = accent;
      el.harakatName.style.transform = 'translateY(' + (1 - hv) * 1.6 + 'vh)';
      setText(el.harakatName, seg.harakatName);
    }

    if (seg.kind === 'prompt') {
      // Sits where the syllable would be and at a smaller size, so handing
      // the turn over does not push the letter off the screen.
      var qv = envelope(lt, dur, 0.35, 0.3);
      el.prompt.style.opacity = String(qv);
      el.prompt.style.top = 'calc(var(--u) * 75.5)';
      el.prompt.style.fontSize = 'calc(var(--u) * 5.8)';
      el.prompt.style.transform = 'scale(' + (1 + 0.02 * Math.sin((lt / dur) * Math.PI)) + ')';
      setText(el.prompt, seg.text || '');
    } else if (seg.kind === 'repeat') {
      // The child's turn. The syllable is shown faintly and pulsing rather
      // than plainly, so a reading parent can prompt without the child simply
      // being handed the answer.
      var pulse = 0.30 + 0.16 * (0.5 + 0.5 * Math.sin((lt / dur) * Math.PI * 4));
      el.say.style.opacity = String(pulse * vis);
      el.say.style.transform = 'translateY(0)';
      el.say.style.color = accent;
      setText(el.say, seg.say || '');
    } else if (seg.say) {
      var sv = easeOutCubic((lt - 0.42) / 0.36) * vis;
      el.say.style.opacity = String(sv);
      el.say.style.color = '#2A2018';
      el.say.style.transform = 'translateY(' + (1 - sv) * 1.8 + 'vh)';
      setText(el.say, seg.say);
    }

    paintDots(seg.step === undefined ? -1 : seg.step, 4, accent,
              seg.kind === 'repeat' || seg.kind === 'prompt');
  }

  // -- contract -------------------------------------------------------------

  window.__movie = {
    ready: false,

    init: async function (cfg) {
      timeline = window.__timeline;
      if (!timeline || !Array.isArray(timeline.segments) || timeline.segments.length === 0) {
        throw new Error('elifba scene: window.__timeline has no segments');
      }

      var ids = ['stage', 'glowInner', 'glowOuter', 'title', 'card',
                 'glyphBase', 'glyphMark', 'letterName', 'harakatName', 'say', 'prompt', 'dots'];
      for (var i = 0; i < ids.length; i++) {
        el[ids[i]] = document.getElementById(ids[i]);
        if (!el[ids[i]]) throw new Error('elifba scene: missing #' + ids[i]);
      }

      el.stage.style.width = cfg.width + 'px';
      el.stage.style.height = cfg.height + 'px';
      el.stage.style.setProperty('--u', cfg.height / 100 + 'px');
      setText(el.title, timeline.title || '');

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
      paint(seg, t - seg.start);
      // Force a synchronous layout flush so the screenshot that follows sees
      // the values written above rather than the previous frame's.
      void el.stage.getBoundingClientRect();
    },
  };
})();
