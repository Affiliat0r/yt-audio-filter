/* Elif-Ba video engine.
 *
 * Draws one frame of an episode for any time t. Nothing here runs on a clock:
 * every position, size and opacity is computed from t and the timeline, so
 * render.py can step through frames one by one and get the same video every time.
 *
 *   window.__TIMELINE   the storyboard (set before this script runs)
 *   window.seek(t)      draw the frame at t seconds
 */
(() => {
  "use strict";

  const TL = window.__TIMELINE;
  const $ = (sel) => document.querySelector(sel);
  const scenesLayer = $("#scenes");
  const fxLayer = $("#fx");
  const mascotEl = $("#mascot");

  // ---------------------------------------------------------------- maths
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, k) => a + (b - a) * k;
  const prog = (t, a, b) => clamp((t - a) / Math.max(1e-6, b - a));
  const easeOutCubic = (k) => 1 - Math.pow(1 - k, 3);
  const easeInOut = (k) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);
  const easeOutBack = (k) => {
    const c1 = 1.70158, c3 = c1 + 1;
    return 1 + c3 * Math.pow(k - 1, 3) + c1 * Math.pow(k - 1, 2);
  };
  /** 0 → 1 → 0 window with soft edges. */
  const windowed = (t, a, b, fadeIn = 0.3, fadeOut = 0.3) =>
    Math.min(easeOutCubic(prog(t, a, a + fadeIn)), 1 - easeInOut(prog(t, b - fadeOut, b)));

  // ---------------------------------------------------------------- beats
  // A beat is "this sound is playing now"; the target is what should react.
  const beatsByTarget = {};
  for (const b of TL.beats) (beatsByTarget[b.target] ||= []).push(b);

  /** The beat on ``target`` that covers t (plus a short tail), or null. */
  function beatAt(target, t, tail = 0.35) {
    const list = beatsByTarget[target];
    if (!list) return null;
    for (const b of list) if (t >= b.t && t < b.t + b.dur + tail) return b;
    return null;
  }

  /** Loudness-like envelope of the sound on ``target``: fast attack, slow fall. */
  function env(target, t) {
    const b = beatAt(target, t);
    if (!b) return 0;
    const k = t - b.t;
    const attack = clamp(k / 0.07);
    const body = k < b.dur ? 1 - 0.45 * (k / b.dur) : 0.55 * (1 - (k - b.dur) / 0.35);
    return clamp(attack * body);
  }

  const phaseAt = (scene, t) => (scene.phases || []).find((p) => t >= p.start && t < p.end) || null;

  // ---------------------------------------------------------------- icons
  const ICON = {
    ear: `<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="49" fill="#E3F0FB"/>
      <path d="M34 47c0-13 8-23 19-23s19 9 19 21c0 10-6 14-10 19-4 4-3 12-10 14-6 2-11-2-12-6"
        fill="none" stroke="#1C7CB8" stroke-width="7" stroke-linecap="round"/>
      <path d="M44 47c0-6 4-11 9-11s9 4 9 9c0 5-4 7-6 10" fill="none" stroke="#1C7CB8" stroke-width="6" stroke-linecap="round"/></svg>`,
    talk: `<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="49" fill="#FFEBD6"/>
      <circle cx="40" cy="52" r="24" fill="#FFA62B"/><circle cx="33" cy="45" r="4" fill="#3A2A1A"/>
      <ellipse cx="47" cy="61" rx="7" ry="8" fill="#7A2E00"/>
      <path d="M70 40q8 10 0 22M78 33q13 17 0 36" fill="none" stroke="#E07B00" stroke-width="5" stroke-linecap="round"/></svg>`,
    star: `<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="49" fill="#FFF4CC"/>
      <path d="M50 18l9 20 22 2-17 14 5 22-19-11-19 11 5-22-17-14 22-2z" fill="#F5B800" stroke="#E09A00" stroke-width="3" stroke-linejoin="round"/></svg>`,
    question: `<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="49" fill="#EFE6FB"/>
      <path d="M36 38c0-9 7-15 15-15s15 6 15 14c0 10-12 11-12 21" fill="none" stroke="#8E44AD" stroke-width="9" stroke-linecap="round"/>
      <circle cx="54" cy="74" r="6" fill="#8E44AD"/></svg>`,
  };
  const STAR_PATH = "M50 6l13 28 30 3-23 20 7 30-27-16-27 16 7-30-23-20 30-3z";
  const CHECK = `<svg viewBox="0 0 100 100" width="84" height="84"><path d="M24 52l17 17 36-38" fill="none" stroke="#fff" stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

  const cueText = (key) => TL.cues[key] || "";

  // When the Turkish voice speaks, the mascot's mouth moves with it.
  const cueSpans = TL.audio.filter((a) => a.kind === "cue").map((a) => [a.t, a.t + a.dur]);
  function talking(t) {
    for (const [a, b] of cueSpans) if (t >= a && t < b) return clamp((t - a) / 0.05) * clamp((b - t) / 0.08);
    return 0;
  }
  let sceneOpacity = 1;   // the mascot fades with the scene that owns it

  // ---------------------------------------------------------------- background
  const BLOBS = [
    [180, 160, 260, "#FFD8A8"], [1720, 220, 200, "#C8EBDD"], [1500, 930, 300, "#FFE3EC"],
    [300, 900, 230, "#D8ECFF"], [960, 60, 160, "#FFF0B8"], [1080, 1010, 180, "#E5DCFA"],
  ];
  const blobEls = BLOBS.map(([, , r, c]) => {
    const d = document.createElement("div");
    d.className = "blob";
    Object.assign(d.style, { width: `${r}px`, height: `${r}px`, background: c, opacity: 0.55 });
    $("#blobs").appendChild(d);
    return d;
  });
  function drawBackground(t) {
    BLOBS.forEach(([x, y, r], i) => {
      const dx = Math.sin(t * 0.21 + i * 1.7) * 26, dy = Math.cos(t * 0.17 + i * 2.3) * 20;
      blobEls[i].style.transform = `translate(${x - r / 2 + dx}px, ${y - r / 2 + dy}px)`;
    });
    $("#pattern").style.transform = `translate(${(t * 6) % 120}px, ${(t * 3) % 120}px)`;
  }

  // ---------------------------------------------------------------- mascot
  mascotEl.innerHTML = `
    <ellipse cx="130" cy="250" rx="70" ry="9" fill="rgba(0,0,0,.10)"/>
    <g id="m-body">
      <ellipse id="m-footL" cx="100" cy="236" rx="26" ry="14" fill="#E07B00"/>
      <ellipse id="m-footR" cx="160" cy="236" rx="26" ry="14" fill="#E07B00"/>
      <ellipse id="m-armL" cx="42" cy="165" rx="18" ry="34" fill="#FF9A1F"/>
      <ellipse id="m-armR" cx="218" cy="165" rx="18" ry="34" fill="#FF9A1F"/>
      <circle cx="130" cy="140" r="96" fill="#FFA62B" stroke="#E98A0C" stroke-width="5"/>
      <ellipse cx="96" cy="92" rx="30" ry="18" fill="#fff" opacity=".35" transform="rotate(-25 96 92)"/>
      <ellipse cx="80" cy="168" rx="16" ry="10" fill="#FF7A7A" opacity=".55"/>
      <ellipse cx="180" cy="168" rx="16" ry="10" fill="#FF7A7A" opacity=".55"/>
      <g id="m-eyes">
        <ellipse cx="102" cy="128" rx="21" ry="25" fill="#fff"/>
        <ellipse cx="158" cy="128" rx="21" ry="25" fill="#fff"/>
        <circle id="m-pupilL" cx="104" cy="130" r="11" fill="#2B2018"/>
        <circle id="m-pupilR" cx="160" cy="130" r="11" fill="#2B2018"/>
        <circle id="m-glintL" cx="108" cy="125" r="4" fill="#fff"/>
        <circle id="m-glintR" cx="164" cy="125" r="4" fill="#fff"/>
      </g>
      <path id="m-mouth" fill="#7A2E00" stroke="#7A2E00" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
    </g>`;
  const M = (id) => mascotEl.querySelector(id);
  const MOUTH = {
    smile: "M106 172 Q130 194 154 172",
    grin: "M102 166 Q130 214 158 166 Q130 180 102 166 Z",
    open: "M112 168 Q130 206 148 168 Q130 176 112 168 Z",
    o: "M122 176 a8 10 0 1 0 16 0 a8 10 0 1 0 -16 0 Z",
    calm: "M112 176 Q130 186 148 176",
  };

  /**
   * Pose the mascot.  state: idle | wave | look | listen | encourage | happy | think | calm
   * (x, y) is the top-left of its 260px box.
   */
  function drawMascot(t, state, x, y, opacity = 1, look = [0, 0]) {
    opacity *= sceneOpacity;
    mascotEl.style.opacity = opacity;
    if (opacity <= 0.001) return;
    let bob = Math.sin(t * 2.4) * 6, tilt = 0, jump = 0, squash = 1;
    let armL = 0, armR = 0, armLy = 0, mouth = "smile";
    let [lx, ly] = look;

    if (state === "wave") { armR = -60 + Math.sin(t * 9) * 25; mouth = "grin"; }
    if (state === "look") { mouth = "smile"; }
    if (state === "listen") { tilt = -9; armL = 55; armLy = -48; mouth = "calm"; lx = -4; ly = -2; }
    if (state === "encourage") {
      mouth = "open"; armL = -30 + Math.sin(t * 5) * 10; armR = 30 - Math.sin(t * 5) * 10;
      tilt = Math.sin(t * 3) * 3; lx = 0; ly = 2;
    }
    if (state === "happy") {
      const ph = (t * 2.2) % 1;
      jump = Math.max(0, Math.sin(ph * Math.PI)) * 46;
      squash = 1 - 0.06 * Math.cos(ph * Math.PI * 2);
      armL = -70; armR = 70; mouth = "grin";
    }
    if (state === "think") { lx = Math.sin(t * 2.2) * 7; ly = -5; mouth = "o"; tilt = Math.sin(t * 1.4) * 4; }
    if (state === "point") {          // looks and points from one card to the other
      const side = Math.sin(t * 1.6);
      lx = -9 * side; ly = -3;
      armL = 40 + 50 * Math.max(0, side); armLy = -20 * Math.max(0, side);
      armR = -40 - 50 * Math.max(0, -side);
      tilt = 4 * side;
    }
    if (state === "no") { tilt = Math.sin(t * 7) * 9; mouth = "calm"; }
    if (state === "calm") { mouth = "calm"; bob *= 0.5; }

    // blink every ~3.6 s, deterministic
    const bk = (t % 3.6) / 3.6;
    const blink = bk > 0.955 ? 0.12 : 1;
    M("#m-eyes").setAttribute("transform", `translate(0 ${128 * (1 - blink)}) scale(1 ${blink})`);
    M("#m-pupilL").setAttribute("cx", 104 + lx); M("#m-pupilL").setAttribute("cy", 130 + ly);
    M("#m-pupilR").setAttribute("cx", 160 + lx); M("#m-pupilR").setAttribute("cy", 130 + ly);
    M("#m-glintL").setAttribute("cx", 108 + lx); M("#m-glintL").setAttribute("cy", 125 + ly);
    M("#m-glintR").setAttribute("cx", 164 + lx); M("#m-glintR").setAttribute("cy", 125 + ly);
    const talk = state === "happy" ? 0 : talking(t);
    if (talk > 0) {
      const h = 8 + 26 * talk * (0.45 + 0.55 * Math.abs(Math.sin(t * 17)));
      M("#m-mouth").setAttribute("d", `M110 168 Q130 ${172 + h} 150 168 Q130 176 110 168 Z`);
      M("#m-mouth").setAttribute("fill", "#7A2E00");
    } else {
      M("#m-mouth").setAttribute("d", MOUTH[mouth]);
      M("#m-mouth").setAttribute("fill", mouth === "smile" || mouth === "calm" ? "none" : "#7A2E00");
    }
    M("#m-armL").setAttribute("transform", `translate(0 ${armLy}) rotate(${armL} 42 140)`);
    M("#m-armR").setAttribute("transform", `rotate(${armR} 218 140)`);
    mascotEl.style.transform =
      `translate(${x}px, ${y - jump + bob}px) rotate(${tilt}deg) scale(${1 / squash}, ${squash})`;
  }

  // ---------------------------------------------------------------- fx
  /** A burst of stars from (cx, cy) starting at t0. Deterministic. */
  function stars(t, t0, cx, cy, n = 12, spread = 420, colors = ["#F5B800", "#FF7A59", "#4FC3F7", "#7ED957"]) {
    const k = (t - t0) / 1.4;
    if (k < 0 || k > 1) return "";
    let html = "";
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2 + 0.3 * Math.sin(i * 12.9898);
      const dist = spread * easeOutCubic(k) * (0.7 + 0.3 * ((i * 7) % 5) / 4);
      const x = cx + Math.cos(a) * dist, y = cy + Math.sin(a) * dist - 60 * k;
      const s = (1 - k) * (0.7 + 0.5 * ((i * 3) % 4) / 3);
      html += `<svg class="star" viewBox="0 0 100 100" style="left:${x - 35}px;top:${y - 35}px;
        transform:scale(${s}) rotate(${k * 200 + i * 30}deg);opacity:${1 - k * k}">
        <path d="${STAR_PATH}" fill="${colors[i % colors.length]}"/></svg>`;
    }
    return html;
  }

  /** Expanding sound rings around (cx, cy) while a beat plays. */
  function rings(t, target, cx, cy, base, color) {
    const b = beatAt(target, t, 0.5);
    if (!b) return "";
    let html = "";
    for (let i = 0; i < 3; i++) {
      const k = ((t - b.t) * 0.9 + i / 3) % 1;
      const fade = clamp(1 - (t - b.t - b.dur) / 0.5);
      const r = base + k * 170;
      html += `<div class="ring" style="--c:${color};left:${cx - r}px;top:${cy - r}px;width:${2 * r}px;height:${2 * r}px;
        opacity:${(1 - k) * 0.45 * fade};border-width:${10 - 6 * k}px"></div>`;
    }
    return html;
  }

  /** A countdown ring: full at a, empty at b. */
  function timerRing(t, a, b, size, color) {
    const k = prog(t, a, b);
    const r = size / 2 - 9, c = 2 * Math.PI * r;
    return `<svg class="timer" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="rgba(0,0,0,.08)" stroke-width="12"/>
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="12"
        stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${c * k}"
        transform="rotate(-90 ${size / 2} ${size / 2})"/></svg>`;
  }

  function bubble(icon, text, x, y, appear, extra = "") {
    const s = lerp(0.6, 1, easeOutBack(appear));
    return `<div class="bubble" style="left:${x}px;top:${y}px;opacity:${clamp(appear * 1.6)};
      transform:scale(${s});transform-origin:0 50%">${ICON[icon]}<span>${text}</span>${extra}</div>`;
  }

  // ---------------------------------------------------------------- glyph images
  // The big letter is drawn on a canvas once, so the engine knows where its dots
  // are: they are the small separate blobs of ink. Pointing at the real dot on
  // the real letter beats any diagram for a three-year-old.
  const DOT_COUNT = { dots_below_1: 1, dots_above_1: 1, dots_above_2: 2, dots_above_3: 3, dots_mid_1: 1 };
  const glyphCache = {};

  function glyphImage(letter, color, box, fontPx, dotCount) {
    const key = `${letter}|${color}|${box}|${fontPx}`;
    if (glyphCache[key]) return glyphCache[key];
    const cv = document.createElement("canvas");
    cv.width = cv.height = box;
    const ctx = cv.getContext("2d");
    ctx.font = `700 ${fontPx}px "Scheherazade"`;
    ctx.direction = "rtl";
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    const m = ctx.measureText(letter);
    // centre the ink, not the advance box
    const inkW = m.actualBoundingBoxLeft + m.actualBoundingBoxRight;
    const inkH = m.actualBoundingBoxAscent + m.actualBoundingBoxDescent;
    const x = (box - inkW) / 2 + m.actualBoundingBoxLeft;
    const y = (box - inkH) / 2 + m.actualBoundingBoxAscent;
    ctx.fillStyle = "rgba(0,0,0,.07)";
    ctx.fillText(letter, x, y + 10);   // soft drop shadow
    ctx.fillStyle = color;
    ctx.fillText(letter, x, y);

    // connected components of the coloured ink (shadow excluded by alpha)
    const { data } = ctx.getImageData(0, 0, box, box);
    const step = 2, gw = Math.ceil(box / step), seen = new Uint8Array(gw * gw), comps = [];
    const ink = (gx, gy) => data[((gy * step) * box + gx * step) * 4 + 3] > 140;
    for (let gy = 0; gy < gw; gy++) for (let gx = 0; gx < gw; gx++) {
      const i = gy * gw + gx;
      if (seen[i] || !ink(gx, gy)) continue;
      const pts = [];
      const stack = [i];
      seen[i] = 1;
      while (stack.length) {
        const j = stack.pop(), px = j % gw, py = (j - px) / gw;
        pts.push([px * step, py * step]);
        for (const [ax, ay] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const qx = px + ax, qy = py + ay;
          if (qx < 0 || qy < 0 || qx >= gw || qy >= gw) continue;
          const q = qy * gw + qx;
          if (!seen[q] && ink(qx, qy)) { seen[q] = 1; stack.push(q); }
        }
      }
      comps.push(pts);
    }
    comps.sort((a, b) => b.length - a.length);       // body first
    let blobs = comps.slice(1);                        // everything but the body
    // Some fonts draw two or three dots touching, as one blob. Split the biggest
    // blob until the count is right (k-means, seeded with far-apart points).
    while (blobs.length && blobs.length < dotCount) {
      blobs.sort((a, b) => b.length - a.length);
      const big = blobs.shift(), k = Math.min(dotCount - blobs.length, 3);
      blobs.push(...kmeans(big, k));
    }
    const dots = blobs.sort((a, b) => a.length - b.length).slice(0, dotCount).map(centroid)
      .sort((a, b) => (Math.abs(a.y - b.y) > 25 ? b.y - a.y : b.x - a.x));   // lower first, right to left
    const ink_ = { top: y - m.actualBoundingBoxAscent, bottom: y + m.actualBoundingBoxDescent,
      left: x - m.actualBoundingBoxLeft, right: x + m.actualBoundingBoxRight };
    return (glyphCache[key] = { url: cv.toDataURL("image/png"), dots, ink: ink_ });
  }

  function centroid(pts) {
    let sx = 0, sy = 0;
    for (const [x, y] of pts) { sx += x; sy += y; }
    const n = pts.length;
    return { x: sx / n, y: sy / n, r: Math.sqrt((n * 4) / Math.PI) };
  }

  function kmeans(pts, k) {
    const centers = [pts[0]];
    while (centers.length < k) {           // farthest-point seeding
      let best = pts[0], bd = -1;
      for (const p of pts) {
        const d = Math.min(...centers.map((c) => (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2));
        if (d > bd) { bd = d; best = p; }
      }
      centers.push(best);
    }
    let groups = [];
    for (let it = 0; it < 12; it++) {
      groups = centers.map(() => []);
      for (const p of pts) {
        let bi = 0, bd = Infinity;
        centers.forEach((c, i) => { const d = (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2; if (d < bd) { bd = d; bi = i; } });
        groups[bi].push(p);
      }
      groups.forEach((g, i) => { if (g.length) { const c = centroid(g); centers[i] = [c.x, c.y]; } });
    }
    return groups.filter((g) => g.length);
  }

  /** Soft glows *behind* the real dots — never a shape of their own that could be
   *  counted as one more dot. ``strength(i)`` (0..1) drives each glow. */
  function dotHalos(g, strength) {
    return g.dots.map((d, i) => {
      const s = strength(i);
      if (s <= 0.001) return "";
      const r = Math.max(d.r * 2.6, 54) * (0.85 + 0.3 * s);
      return `<div style="position:absolute;left:${d.x - r}px;top:${d.y - r}px;width:${2 * r}px;height:${2 * r}px;
        border-radius:50%;background:radial-gradient(circle, rgba(255,196,0,${0.8 * s}) 0%,
        rgba(255,196,0,${0.38 * s}) 42%, rgba(255,196,0,0) 72%)"></div>`;
    }).join("");
  }

  /** The beat on ``target`` that starts inside [a, b), or null. */
  function beatIn(target, a, b) {
    return (beatsByTarget[target] || []).find((x) => x.t >= a && x.t < b) || null;
  }

  // ---------------------------------------------------------------- scenes
  const CARD = { x: 230, y: 180, w: 700 };
  const NEUTRAL_INK = "#3A3550", NEUTRAL_RIM = "#D3C4A9";

  function drawLetter(scene, t) {
    const ph = phaseAt(scene, t) || scene.phases[scene.phases.length - 1];
    const pop = easeOutBack(prog(t, scene.start, scene.start + 0.7));
    const e = env("letter", t);
    const breathe = ph.kind === "turn" || (ph.kind === "say" && t >= ph.turn) ? 0.03 * Math.sin(t * 4) : 0;
    const cx = CARD.x + CARD.w / 2, cy = CARD.y + CARD.w / 2;

    const g = glyphImage(scene.letter, scene.color, 700, 430, DOT_COUNT[scene.dots] || 0);
    let halos = "";
    if (ph.kind === "dots") {
      // each dot lights up as it is counted ("Bir! İki! Üç!") and stays softly lit
      halos = dotHalos(g, (i) => {
        const b = beatIn(`dot:${i}`, ph.start, ph.end);
        if (!b || t < b.t) return 0;
        return Math.max(0.45, env(`dot:${i}`, t));
      });
    }
    let html = rings(t, "letter", cx, cy, 330, scene.color);
    html += `<div class="card ${scene.heavy ? "heavy" : ""}" style="--c:${scene.color};left:${CARD.x}px;top:${CARD.y}px;
      transform:scale(${lerp(0.2, 1, pop) * (1 + 0.035 * e)});opacity:${clamp(pop * 1.5)}">
      <div style="position:absolute;inset:0;transform-origin:50% ${g.ink.bottom}px;transform:scale(${1 + 0.09 * e + breathe})${
        scene.dots === "alif_tall" && ph.kind === "dots"
          ? ` scaleY(${lerp(0.45, 1, easeOutBack(prog(t, ph.start + 0.25, ph.start + 1.25)))})` : ""}">
        ${halos}<img src="${g.url}" style="position:absolute;inset:0;width:700px;height:700px"></div></div>`;

    const bx = 1080, byy = 560;
    let state = "idle", look = [-6, 0];
    if (ph.kind === "meet") { state = "wave"; }
    if (ph.kind === "dots") {
      state = scene.dots === "dots_none" ? "no" : "look"; look = [-8, -4];
    } else if (["listen", "echo"].includes(ph.kind)) {
      html += bubble("ear", cueText("listen"), bx, byy, prog(t, ph.start, ph.start + 0.4));
      state = "listen";
    } else if (ph.kind === "say" || ph.kind === "turn") {
      // "say" spans the cue and the child's silent turn; "turn" is just the silence.
      const says = scene.phases.filter((p) => p.kind === "say");
      const sayPhase = says.find((p) => t >= p.start && t < p.end) || says[0];
      const text = cueText(says.indexOf(sayPhase) === 1 ? "say_again" : "say");
      const ring = t >= sayPhase.turn ? timerRing(t, sayPhase.turn, sayPhase.end, 150, scene.color) : "";
      html += bubble("talk", text, 1010, byy, prog(t, sayPhase.start, sayPhase.start + 0.4),
        `<div style="position:relative;width:150px;height:150px;margin-left:6px">${ring}</div>`);
      state = "encourage";
    } else if (ph.kind === "praise") {
      const key = TL.audio.find((a) => a.kind === "cue" && Math.abs(a.t - ph.start) < 0.01);
      const word = key ? TL.cues[key.path.split("/").pop().replace(".mp3", "")] : "Aferin!";
      html += bubble("star", word, bx, byy, prog(t, ph.start, ph.start + 0.4));
      fxLayer.innerHTML += stars(t, ph.start, cx, cy, 14, 480);
      state = "happy";
    }
    drawMascot(t, state, 1530, 700, 1, look);
    return html;
  }

  function drawGame(scene, t) {
    const ph = phaseAt(scene, t) || scene.phases[scene.phases.length - 1];
    const reveal = scene.phases.find((p) => p.kind === "reveal");
    const think = scene.phases.find((p) => p.kind === "think");
    const rk = easeOutBack(prog(t, reveal.start, reveal.start + 0.6));
    const W = 540, Y = 150, XS = [330, 1050];
    let html = "";
    scene.options.forEach((opt, i) => {
      const isTarget = opt === scene.target;
      const pop = easeOutBack(prog(t, scene.start + i * 0.15, scene.start + 0.6 + i * 0.15));
      const wob = ph.kind === "think" ? Math.sin(t * 5 + i * Math.PI) * 2.5 : 0;
      let scale = lerp(0.2, 1, pop), op = clamp(pop * 1.5), glow = "";
      if (t >= reveal.start) {
        if (isTarget) { scale *= 1 + 0.1 * rk; glow = `, 0 0 0 26px rgba(47,168,102,${0.35 * clamp(rk)})`; }
        else { scale *= lerp(1, 0.85, clamp(rk)); op *= lerp(1, 0.28, clamp(rk)); }
      }
      const e = isTarget ? env("answer", t) : 0;
      const cx = XS[i] + W / 2, cy = Y + W / 2;
      // same neutral ink on both cards: only the dots can tell them apart
      const g = glyphImage(opt, NEUTRAL_INK, W, 330, DOT_COUNT[scene.dots[i]] || 0);
      let halos = "";
      if (isTarget && t >= reveal.start) {
        const b = beatIn("reveal-dots", reveal.start, reveal.end);
        if (b && t >= b.t) halos = dotHalos(g, () => Math.max(0.5, env("reveal-dots", t)));
      }
      if (isTarget) html += rings(t, "answer", cx, cy, 250, "#2FA866");
      html += `<div class="card" style="--c:${t >= reveal.start && isTarget ? "#2FA866" : NEUTRAL_RIM};left:${XS[i]}px;top:${Y}px;
        width:${W}px;height:${W}px;border-radius:80px;transform:scale(${scale}) rotate(${wob}deg);opacity:${op};
        box-shadow:0 26px 50px rgba(120,80,30,.18), inset 0 0 0 14px var(--c)${glow}">
        <div style="position:absolute;inset:0;transform:scale(${1 + 0.08 * e})">${halos}
          <img src="${g.url}" style="position:absolute;inset:0;width:${W}px;height:${W}px"></div></div>`;
      if (isTarget && t >= reveal.start) {
        html += `<div class="check" style="left:${XS[i] + W - 100}px;top:${Y - 30}px;transform:scale(${rk})">${CHECK}</div>`;
        fxLayer.innerHTML += stars(t, reveal.start + 0.1, cx, cy, 12, 420);
      }
    });
    const askK = prog(t, scene.start + 0.5, scene.start + 0.9);
    let extra = "";
    if (t >= think.start && t < reveal.start) {
      extra = `<div style="position:relative;width:150px;height:150px;margin-left:6px">${timerRing(t, think.start, think.end, 150, "#8E44AD")}</div>`;
    }
    if (t < reveal.start) html += bubble("question", cueText("which_show"), 560, 790, askK, extra);
    else html += bubble("star", cueText("this_one"), 790, 790, prog(t, reveal.start, reveal.start + 0.4));
    html += `<div class="progress">${Array.from({ length: scene.rounds }, (_, i) =>
      `<span style="${i < scene.round ? "background:#8E44AD" : ""}"></span>`).join("")}</div>`;
    const state = t >= reveal.start ? "happy" : "point";
    drawMascot(t, state, 1560, 700);
    return html;
  }

  function drawRecap(scene, t) {
    const n = scene.letters.length;
    const S = n > 4 ? 270 : 300, gap = 50;
    const total = n * S + (n - 1) * gap, x0 = (1920 - total) / 2, y = 250;
    const ph = phaseAt(scene, t);
    let html = "";
    // Arabic reads right to left: the first letter sits on the right.
    scene.letters.forEach((l, i) => {
      const x = x0 + (n - 1 - i) * (S + gap);
      const pop = easeOutBack(prog(t, scene.start + i * 0.12, scene.start + 0.5 + i * 0.12));
      const active = ph && ph.index === i;
      const e = env(`recap:${i}`, t);
      const lift = active ? -26 : 0;
      html += rings(t, `recap:${i}`, x + S / 2, y + S / 2, S / 2 + 10, scene.colors[i]);
      html += `<div class="chip" style="--c:${scene.colors[i]};left:${x}px;top:${y + lift}px;width:${S}px;height:${S}px;
        transform:scale(${lerp(0.2, 1, pop) * (1 + 0.06 * e + (active ? 0.08 : 0))});opacity:${clamp(pop * 1.5) * (ph && ph.index !== undefined && !active ? 0.55 : 1)}">
        <div class="glyph" style="font-size:${S * 0.7}px">${l}</div></div>`;
      if (active && ph.kind === "turn") {
        html += `<div style="position:absolute;left:${x + S / 2 - 75}px;top:${y + S + 30}px;width:150px;height:150px">
          ${timerRing(t, ph.start, ph.end, 150, scene.colors[i])}</div>`;
      }
    });
    let state = "idle";
    if (ph && ph.kind === "intro") { html += bubble("ear", cueText("first_listen"), 700, 780, prog(t, ph.start, ph.start + 0.4)); state = "listen"; }
    if (ph && ph.kind === "your-turn") { html += bubble("talk", cueText("say"), 700, 780, prog(t, ph.start, ph.start + 0.4)); state = "encourage"; }
    if (ph && ph.kind === "turn") state = "encourage";
    if (ph && (ph.kind === "model" || ph.kind === "echo")) state = "listen";
    drawMascot(t, state, 1560, 720);
    return html;
  }

  function chipsRow(scene, t, y, size, wave = false) {
    const n = scene.letters.length, gap = 40;
    const total = n * size + (n - 1) * gap, x0 = (1920 - total) / 2;
    return scene.letters.map((l, i) => {
      const pop = easeOutBack(prog(t, scene.start + 0.6 + i * 0.22, scene.start + 1.1 + i * 0.22));
      const dy = wave ? Math.sin(t * 4 - i * 0.8) * 18 : 0;
      const x = x0 + (n - 1 - i) * (size + gap);
      return `<div class="chip" style="--c:${scene.colors[i]};left:${x}px;top:${y + dy}px;width:${size}px;height:${size}px;
        transform:scale(${lerp(0.1, 1, pop)});opacity:${clamp(pop * 1.5)}">
        <div class="glyph" style="font-size:${size * 0.7}px">${l}</div></div>`;
    }).join("");
  }

  function drawTitle(scene, t) {
    const k = easeOutBack(prog(t, scene.start, scene.start + 0.7));
    let html = `<div class="big-title" style="top:120px;font-size:190px;transform:scale(${k});letter-spacing:4px">
      Elif<span style="color:#E4572E">-</span>Ba</div>`;
    html += `<div class="big-title" style="top:370px;font-size:64px;font-weight:500;color:#6B6575;opacity:${prog(t, scene.start + 0.3, scene.start + 0.8)}">
      ${scene.episode}. Ders</div>`;
    html += chipsRow(scene, t, 520, 230);
    drawMascot(t, "wave", 1560, 710);
    return html;
  }

  function drawBesmele(scene, t) {
    const k = easeInOut(prog(t, scene.start + 0.1, scene.start + 1.4));
    drawMascot(t, "calm", 1600, 780, 0);
    return `<div class="frame" style="opacity:${k}"></div>
      <div class="besmele" style="opacity:${k};transform:translateY(${(1 - k) * 20}px)">بِسْمِ اللّٰهِ الرَّحْمٰنِ الرَّحِيمِ</div>
      <div class="big-title" style="top:650px;font-size:46px;font-weight:500;color:#8A6A3A;opacity:${k * 0.9}">
        Bismillâhirrahmânirrahîm</div>`;
  }

  function drawOutro(scene, t) {
    const k = easeOutBack(prog(t, scene.start, scene.start + 0.7));
    let html = `<div class="big-title" style="top:140px;font-size:170px;color:#E4572E;transform:scale(${k})">Aferin!</div>`;
    html += chipsRow(scene, t, 470, 220, true);
    fxLayer.innerHTML += stars(t, scene.start + 0.2, 960, 240, 16, 700);
    fxLayer.innerHTML += stars(t, scene.start + 1.6, 960, 240, 16, 700);
    const settle = (scene.phases || []).find((p) => p.kind === "settle");
    drawMascot(t, settle && t >= settle.start ? "wave" : "happy", 795, 690);
    return html;
  }

  const DRAW = { besmele: drawBesmele, title: drawTitle, letter: drawLetter, game: drawGame, recap: drawRecap, outro: drawOutro };

  // ---------------------------------------------------------------- frame
  window.seek = function seek(t) {
    drawBackground(t);
    fxLayer.innerHTML = "";
    let html = "";
    let any = false;
    for (const scene of TL.scenes) {
      if (t < scene.start - 0.001 || t >= scene.end) continue;
      // One scene at a time: it fades out over its own last moments, and the next
      // grows in after it has gone — never one letter on top of another.
      const fade = scene.type === "outro" ? 1.2 : 0.3;
      sceneOpacity = 1 - easeInOut(prog(t, scene.end - fade, scene.end));
      const inner = DRAW[scene.type](scene, t);
      html += `<div class="layer" style="opacity:${sceneOpacity}">${inner}</div>`;
      fxLayer.style.opacity = sceneOpacity;
      any = true;
    }
    if (!any) drawMascot(t, "idle", 1600, 780, 0);
    scenesLayer.innerHTML = html;
  };

  window.__duration = TL.duration;
  window.__ready = document.fonts.ready.then(() =>
    Promise.all([document.fonts.load('700 100px "Scheherazade"', "ب"), document.fonts.load('800 60px "Nunito"', "Şş")]));
})();
