/* Elif-Ba video engine.
 *
 * Draws one frame of an episode for any time t. Nothing here runs on a clock:
 * every position, size and opacity is computed from t and the timeline, so
 * render.py can step through frames one by one and get the same video every time.
 *
 *   window.__TIMELINE   the storyboard (set before this script runs)
 *   window.seek(t)      draw the frame at t seconds
 *
 * The guide is a kamış kalem (reed pen) with its divit (inkwell) — no character,
 * no face. "Oku! ... kalemle öğreten Rabbin" (Alak 96:1-5). The pen either stays
 * clear of the letters or does something meaningful with them:
 *   - it points from outside the card's edge, or from below like a pointer
 *     stick held from beneath — never across a glyph, never lying under one
 *     (a stroke under a letter reads as a kasra);
 *   - it *places* each dot: the dots lift off, the pen taps where each belongs,
 *     the dot appears and the pen leaves — near a dot only to make it;
 *   - during the child's turn it goes back to its inkwell: the teacher waits.
 */
(() => {
  "use strict";

  const TL = window.__TIMELINE;
  const $ = (sel) => document.querySelector(sel);
  const scenesLayer = $("#scenes");
  const fxLayer = $("#fx");
  const penEl = $("#pen");
  const wellBack = $("#well-back");
  const wellFront = $("#well-front");

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

  // ---------------------------------------------------------------- beats
  const beatsByTarget = {};
  for (const b of TL.beats) (beatsByTarget[b.target] ||= []).push(b);

  function beatAt(target, t, tail = 0.35) {
    const list = beatsByTarget[target];
    if (!list) return null;
    for (const b of list) if (t >= b.t && t < b.t + b.dur + tail) return b;
    return null;
  }
  const beatIn = (target, a, b) => (beatsByTarget[target] || []).find((x) => x.t >= a && x.t < b) || null;
  const beatsIn = (target, a, b) => (beatsByTarget[target] || []).filter((x) => x.t >= a && x.t < b);

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
  const phasesOf = (scene, kind) => (scene.phases || []).filter((p) => p.kind === kind);

  // ---------------------------------------------------------------- icons (no faces)
  const ICON = {
    speaker: `<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="49" fill="#E3F0FB"/>
      <path d="M24 42h12l16-13v42L36 58H24z" fill="#1C7CB8" stroke="#1C7CB8" stroke-width="4" stroke-linejoin="round"/>
      <path d="M62 38q8 12 0 24M70 30q14 20 0 40" fill="none" stroke="#1C7CB8" stroke-width="6" stroke-linecap="round"/></svg>`,
    // "we are listening to you": an ear, nothing more
    ear: `<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="49" fill="#FFEBD6"/>
      <path d="M34 47c0-13 8-23 19-23s19 9 19 21c0 10-6 14-10 19-4 4-3 12-10 14-6 2-11-2-12-6"
        fill="none" stroke="#E07B00" stroke-width="7" stroke-linecap="round"/>
      <path d="M44 47c0-6 4-11 9-11s9 4 9 9c0 5-4 7-6 10" fill="none" stroke="#E07B00" stroke-width="6" stroke-linecap="round"/></svg>`,
    star: `<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="49" fill="#FFF4CC"/>
      <path d="M50 18l9 20 22 2-17 14 5 22-19-11-19 11 5-22-17-14 22-2z" fill="#F5B800" stroke="#E09A00" stroke-width="3" stroke-linejoin="round"/></svg>`,
    question: `<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="49" fill="#EFE6FB"/>
      <path d="M36 38c0-9 7-15 15-15s15 6 15 14c0 10-12 11-12 21" fill="none" stroke="#8E44AD" stroke-width="9" stroke-linecap="round"/>
      <circle cx="54" cy="74" r="6" fill="#8E44AD"/></svg>`,
  };
  const STAR_PATH = "M50 6l13 28 30 3-23 20 7 30-27-16-27 16 7-30-23-20 30-3z";
  const CHECK = `<svg viewBox="0 0 100 100" width="84" height="84"><path d="M24 52l17 17 36-38" fill="none" stroke="#fff" stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const label = (key) => (TL.labels || {})[key] || "";

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

  // ---------------------------------------------------------------- the pen and its inkwell
  // Pen artwork is 80x520 units, drawn at 0.8 scale; its nib tip is at (40, 512).
  // Angle a: 0 = nib straight down (body above); 180 = nib up, body below (held from beneath).
  const PEN_W = 64, PEN_H = 416, TIP_X = 32, TIP_Y = 409.6;
  penEl.setAttribute("width", PEN_W);
  penEl.setAttribute("height", PEN_H);
  penEl.setAttribute("viewBox", "0 0 80 520");
  penEl.innerHTML = `
    <defs>
      <linearGradient id="reed" x1="0" x2="1">
        <stop offset="0" stop-color="#E7B866"/><stop offset=".45" stop-color="#C98E3C"/><stop offset="1" stop-color="#94601F"/>
      </linearGradient>
      <linearGradient id="nib" x1="0" x2="1">
        <stop offset="0" stop-color="#6B4A2A"/><stop offset="1" stop-color="#3B2614"/>
      </linearGradient>
    </defs>
    <path d="M22 26 Q40 6 58 26 L58 372 Q57 404 50 432 L30 432 Q23 404 22 372 Z" fill="url(#reed)"/>
    <path d="M27 30 L27 368" stroke="#FFF3D6" stroke-width="4" stroke-linecap="round" opacity=".45"/>
    <path d="M22 132 Q40 140 58 132 M22 262 Q40 270 58 262" stroke="#7A4A18" stroke-width="3" fill="none" opacity=".45"/>
    <path d="M30 432 Q40 440 50 432 L43.5 506 Q40 516 36.5 506 Z" fill="url(#nib)"/>
    <path d="M40 452 L40 500" stroke="#F6E2B0" stroke-width="1.6" opacity=".55"/>`;
  wellBack.innerHTML = `<ellipse cx="110" cy="40" rx="56" ry="16" fill="#120D22"/>`;
  wellFront.innerHTML = `
    <defs><linearGradient id="glaze" x1="0" x2="1">
      <stop offset="0" stop-color="#2F5597"/><stop offset=".5" stop-color="#1F3A6E"/><stop offset="1" stop-color="#132447"/></linearGradient></defs>
    <path d="M54 40 Q40 60 36 90 Q34 150 110 156 Q186 150 184 90 Q180 60 166 40 Q110 62 54 40 Z" fill="url(#glaze)"/>
    <path d="M44 84 Q110 104 176 84" stroke="#C9962B" stroke-width="7" fill="none"/>
    <path d="M110 112 q-14 -14 0 -26 q14 12 0 26 Z M92 114 q-10 -6 -6 -18 M128 114 q10 -6 6 -18"
      fill="#E8C66A" stroke="#E8C66A" stroke-width="3" stroke-linecap="round"/>
    <path d="M54 40 Q110 62 166 40" stroke="#E2B04A" stroke-width="7" fill="none" stroke-linecap="round"/>
    <path d="M50 56 Q46 76 48 96" stroke="#fff" stroke-width="6" opacity=".25" fill="none" stroke-linecap="round"/>`;
  const WELL = { x: 1600, y: 880 };           // top-left of the inkwell artwork (220x170)
  const HOME = { x: WELL.x + 110, y: WELL.y + 76, a: 14 };      // nib down in the ink
  const LIFT = { x: WELL.x + 110, y: WELL.y - 40, a: 14 };      // just lifted out
  for (const el of [wellBack, wellFront]) {
    el.style.left = `${WELL.x}px`;
    el.style.top = `${WELL.y}px`;
  }

  /** Interpolate a pose along [[time, pose], ...]. ``resolve`` turns a stored
   *  pose into stage coordinates at time t (card-relative poses follow the card). */
  function track(t, keys, resolve = (p) => p) {
    const at = (i) => resolve(keys[i][1], t);
    if (t <= keys[0][0]) return at(0);
    for (let i = 1; i < keys.length; i++) {
      if (t < keys[i][0]) {
        const p0 = at(i - 1), p1 = at(i);
        const k = easeInOut(prog(t, keys[i - 1][0], keys[i][0]));
        return { x: lerp(p0.x, p1.x, k), y: lerp(p0.y, p1.y, k), a: lerp(p0.a, p1.a, k) };
      }
    }
    return at(keys.length - 1);
  }

  /** A quick tap toward what the pen points at, while ``target`` sounds. */
  function tap(t, target) {
    const b = beatAt(target, t, 0);
    if (!b) return 0;
    return Math.sin(Math.PI * clamp((t - b.t) / 0.28));
  }

  let penOpacity = 1;
  function drawPen(t, pose, tapK = 0) {
    penEl.style.opacity = penOpacity;
    wellBack.style.opacity = wellFront.style.opacity = penOpacity;
    const rad = (pose.a * Math.PI) / 180;
    // a tap moves the nib the way it points
    const x = pose.x - Math.sin(rad) * 18 * tapK, y = pose.y + Math.cos(rad) * 18 * tapK;
    const a = pose.a + 0.8 * Math.sin(t * 1.3);     // a living hand is never quite still
    penEl.style.transformOrigin = `${TIP_X}px ${TIP_Y}px`;
    penEl.style.transform = `translate(${x - TIP_X}px, ${y - TIP_Y}px) rotate(${a}deg)`;
  }

  // ---------------------------------------------------------------- fx
  function stars(t, t0, cx, cy, n = 12, spread = 420, colors = ["#F5B800", "#FF7A59", "#4FC3F7", "#7ED957"]) {
    const k = (t - t0) / 1.4;
    if (k < 0 || k > 1) return "";
    let html = "";
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2 + 0.3 * Math.sin(i * 12.9898);
      const dist = spread * easeOutCubic(k) * (0.7 + 0.3 * ((i * 7) % 5) / 4);
      const x = cx + Math.cos(a) * dist, y = cy + Math.sin(a) * dist - 60 * k;
      const s = (1 - k) * (0.8 + 0.6 * ((i * 3) % 4) / 3);
      html += `<svg class="star" viewBox="0 0 100 100" style="left:${x - 35}px;top:${y - 35}px;
        transform:scale(${s}) rotate(${k * 200 + i * 30}deg);opacity:${1 - k * k}">
        <path d="${STAR_PATH}" fill="${colors[i % colors.length]}"/></svg>`;
    }
    return html;
  }

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

  const checkBadge = (x, y, k) =>
    `<div class="check" style="left:${x}px;top:${y}px;transform:scale(${k});opacity:${clamp(k * 2)}">${CHECK}</div>`;

  // ---------------------------------------------------------------- glyph images
  // Each letter is drawn on a canvas once. Its dots are the small separate blobs
  // of ink (touching dots, ث in this font, are split with k-means). From that the
  // engine makes the letter without its dots and one sprite per dot, so the pen
  // can put the dots back one at a time.
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
    const inkW = m.actualBoundingBoxLeft + m.actualBoundingBoxRight;
    const inkH = m.actualBoundingBoxAscent + m.actualBoundingBoxDescent;
    const x = (box - inkW) / 2 + m.actualBoundingBoxLeft;
    const y = (box - inkH) / 2 + m.actualBoundingBoxAscent;
    ctx.fillStyle = color;
    ctx.fillText(letter, x, y);

    const img = ctx.getImageData(0, 0, box, box);
    const data = img.data;
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
        pts.push([px, py]);
        for (const [ax, ay] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const qx = px + ax, qy = py + ay;
          if (qx < 0 || qy < 0 || qx >= gw || qy >= gw) continue;
          const q = qy * gw + qx;
          if (!seen[q] && ink(qx, qy)) { seen[q] = 1; stack.push(q); }
        }
      }
      comps.push(pts);
    }
    comps.sort((a, b) => b.length - a.length);
    let blobs = comps.slice(1);
    while (blobs.length && blobs.length < dotCount) {
      blobs.sort((a, b) => b.length - a.length);
      const big = blobs.shift(), k = Math.min(dotCount - blobs.length, 3);
      blobs.push(...kmeans(big, k));
    }
    const chosen = blobs.sort((a, b) => a.length - b.length).slice(0, dotCount)
      .map((pts) => ({ pts, c: centroid(pts, step) }))
      .sort((a, b) => (Math.abs(a.c.y - b.c.y) > 25 ? b.c.y - a.c.y : b.c.x - a.c.x));   // lower first, right to left

    // label grid: which dot (if any) each cell belongs to
    const labels = new Int8Array(gw * gw).fill(-1);
    chosen.forEach(({ pts }, k) => { for (const [px, py] of pts) labels[py * gw + px] = k; });
    const labelOf = (px, py) => {               // nearest labelled cell within 2 cells
      const gx = px >> 1, gy = py >> 1;
      for (let r = 0; r <= 2; r++)
        for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
          const qx = gx + dx, qy = gy + dy;
          if (qx < 0 || qy < 0 || qx >= gw || qy >= gw) continue;
          const l = labels[qy * gw + qx];
          if (l >= 0) return l;
        }
      return -1;
    };
    const noDots = new ImageData(new Uint8ClampedArray(data), box, box);
    const sprites = chosen.map(({ pts }, k) => {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const [px, py] of pts) { x0 = Math.min(x0, px); y0 = Math.min(y0, py); x1 = Math.max(x1, px); y1 = Math.max(y1, py); }
      x0 = Math.max(0, x0 * step - 5); y0 = Math.max(0, y0 * step - 5);
      x1 = Math.min(box - 1, x1 * step + 6); y1 = Math.min(box - 1, y1 * step + 6);
      const w = x1 - x0 + 1, h = y1 - y0 + 1;
      const sc = document.createElement("canvas");
      sc.width = w; sc.height = h;
      const sctx = sc.getContext("2d");
      const out = sctx.createImageData(w, h);
      for (let py = y0; py <= y1; py++) for (let px = x0; px <= x1; px++) {
        const o = (py * box + px) * 4;
        if (data[o + 3] === 0 || labelOf(px, py) !== k) continue;
        const q = ((py - y0) * w + (px - x0)) * 4;
        out.data.set(data.subarray(o, o + 4), q);
        noDots.data[o + 3] = 0;
      }
      sctx.putImageData(out, 0, 0);
      return { url: sc.toDataURL("image/png"), x: x0, y: y0, w, h };
    });
    const nd = document.createElement("canvas");
    nd.width = nd.height = box;
    nd.getContext("2d").putImageData(noDots, 0, 0);
    const inkBox = { top: y - m.actualBoundingBoxAscent, bottom: y + m.actualBoundingBoxDescent,
      left: x - m.actualBoundingBoxLeft, right: x + m.actualBoundingBoxRight };
    return (glyphCache[key] = {
      url: cv.toDataURL("image/png"), urlNoDots: nd.toDataURL("image/png"), sprites,
      dots: chosen.map((d) => d.c), ink: inkBox, box,
    });
  }

  function centroid(pts, step = 1) {
    let sx = 0, sy = 0;
    for (const [x, y] of pts) { sx += x; sy += y; }
    const n = pts.length;
    return { x: (sx / n) * step, y: (sy / n) * step, r: Math.sqrt((n * step * step) / Math.PI) };
  }

  function kmeans(pts, k) {
    const centers = [pts[0]];
    while (centers.length < k) {
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

  /** Soft glows *behind* the dots — never a shape that could be counted as one more dot. */
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

  const SHADOW = "filter:drop-shadow(0 10px 0 rgba(0,0,0,.07))";

  /** The letter's image, or (while its dots are being placed) the dotless letter
   *  plus each dot sprite at scale ``dotScale(i)``. */
  function glyphLayers(g, size, dotScale) {
    if (!dotScale) return `<img src="${g.url}" style="position:absolute;inset:0;width:${size}px;height:${size}px;${SHADOW}">`;
    let html = `<img src="${g.urlNoDots}" style="position:absolute;inset:0;width:${size}px;height:${size}px;${SHADOW}">`;
    g.sprites.forEach((sp, i) => {
      const s = dotScale(i);
      if (s.k <= 0.001) return;
      html += `<img src="${sp.url}" style="position:absolute;left:${sp.x}px;top:${sp.y + s.dy}px;width:${sp.w}px;height:${sp.h}px;
        transform:scale(${s.k});opacity:${clamp(s.o)};${SHADOW}">`;
    });
    return html;
  }

  // ---------------------------------------------------------------- letter scene
  const CARD = { y: 180, w: 700, center: 610, left: 230 };

  /** The card sits centred while it has the stage to itself and slides left
   *  when a prompt appears beside it. */
  function cardX(scene, t) {
    const listen = phasesOf(scene, "listen")[0];
    return lerp(CARD.center, CARD.left, easeInOut(prog(t, listen.start - 0.3, listen.start + 0.3)));
  }

  /** How the pen approaches a dot, by where the dot sits on the letter. */
  function approach(kind) {
    if (kind === "dots_below_1") return { dx: 0, dy: 1, a: 180 };    // from below, held from beneath
    if (kind === "dots_mid_1") return { dx: -1, dy: 0, a: -90 };     // from the open side (ج opens left)
    return { dx: 0.25, dy: -1, a: 22 };                              // from above
  }

  function letterPenKeys(scene, g) {
    // card-relative poses carry rel: true and follow the card as it slides
    const side = { x: CARD.w + 16, y: CARD.w / 2, a: 22, rel: true };    // just outside the card's right edge
    const keys = [[scene.start, HOME], [scene.start + 0.4, LIFT]];
    const meet = phasesOf(scene, "meet")[0];
    keys.push([meet.start + 1.0, side]);
    const dots = phasesOf(scene, "dots")[0];
    const n = g.dots.length;
    if (n) {
      const ap = approach(scene.dots);
      g.dots.forEach((d, i) => {
        const b = beatIn(`dot:${i}`, dots.start, dots.end);
        if (!b) return;
        const len = Math.hypot(ap.dx, ap.dy);
        const ux = ap.dx / len, uy = ap.dy / len;
        const near = { x: d.x + ux * (d.r * 0.4), y: d.y + uy * (d.r * 0.4), a: ap.a, rel: true };
        const away = { x: d.x + ux * 150, y: d.y + uy * 150, a: ap.a, rel: true };
        keys.push([b.t - 0.3, away], [b.t, near], [b.t + 0.12, near], [b.t + 0.5, away]);
      });
      const last = beatIn(`dot:${n - 1}`, dots.start, dots.end);
      keys.push([(last ? last.t : dots.start) + 1.0, side]);
    }
    for (const say of phasesOf(scene, "say")) {
      // the child's turn: the pen goes back to its inkwell — the teacher waits
      keys.push([say.start + 0.1, side], [say.start + 0.6, LIFT], [say.turn + 0.2, HOME]);
      keys.push([say.end - 0.75, HOME], [say.end - 0.45, LIFT], [say.end + 0.05, side]);
    }
    const praise = phasesOf(scene, "praise")[0];
    keys.push([praise.start + 0.5, LIFT], [scene.end - 0.35, LIFT], [scene.end, HOME]);
    return keys.sort((a, b) => a[0] - b[0]);
  }

  function drawLetter(scene, t) {
    const ph = phaseAt(scene, t) || scene.phases[scene.phases.length - 1];
    const pop = easeOutBack(prog(t, scene.start + 0.5, scene.start + 1.1));
    const e = env("letter", t);
    const cxCard = cardX(scene, t);
    const cx = cxCard + CARD.w / 2, cy = CARD.y + CARD.w / 2;
    const g = glyphImage(scene.letter, scene.color, 700, 430, DOT_COUNT[scene.dots] || 0);

    // the child's turn: dim the room, light the card, let it pulse
    const say = phasesOf(scene, "say").find((p) => t >= p.turn && t < p.end);
    let turnK = 0, pulse = 0;
    if (say) {
      turnK = Math.min(easeOutCubic(prog(t, say.turn, say.turn + 0.4)), 1 - easeInOut(prog(t, say.end - 0.25, say.end)));
      pulse = 0.04 * Math.sin(2 * Math.PI * 1.3 * (t - say.turn)) * turnK;
    }

    // dots: lift off, then placed back one by one by the pen
    let dotScale = null, halos = "";
    const dots = phasesOf(scene, "dots")[0];
    if (g.dots.length && t >= dots.start && t < dots.end) {
      const lift = prog(t, dots.start + 0.15, dots.start + 0.75);
      dotScale = (i) => {
        const b = beatIn(`dot:${i}`, dots.start, dots.end);
        if (b && t >= b.t + 0.05) return { k: easeOutBack(prog(t, b.t + 0.05, b.t + 0.4)), o: 1, dy: 0 };
        return { k: 1 - easeInOut(lift) * 0.6, o: 1 - easeInOut(lift), dy: -40 * easeInOut(lift) };
      };
      halos = dotHalos(g, (i) => {
        const b = beatIn(`dot:${i}`, dots.start, dots.end);
        if (!b || t < b.t + 0.05) return 0;
        return Math.max(0.4, 1 - 0.6 * prog(t, b.t, b.t + 0.9)) * easeOutCubic(prog(t, b.t + 0.05, b.t + 0.25));
      });
    }

    const praise = phasesOf(scene, "praise")[0];
    const hop = t >= praise.start ? Math.sin(Math.PI * prog(t, praise.start, praise.start + 0.5)) * 0.05 : 0;
    let html = "";
    if (turnK > 0) {
      html += `<div style="position:absolute;inset:0;background:rgba(70,45,20,${0.13 * turnK})"></div>
        <div style="position:absolute;left:${cx - 620}px;top:${cy - 620}px;width:1240px;height:1240px;border-radius:50%;
          background:radial-gradient(circle, rgba(255,226,160,${0.75 * turnK}) 0%, rgba(255,226,160,0) 62%)"></div>`;
    }
    html += rings(t, "letter", cx, cy, 330, scene.color);
    html += `<div class="card ${scene.heavy ? "heavy" : ""}" style="--c:${scene.color};left:${cxCard}px;top:${CARD.y}px;
      transform:scale(${lerp(0.2, 1, pop) * (1 + 0.035 * e + hop + pulse)});opacity:${clamp(pop * 1.5)}">
      <div style="position:absolute;inset:0;transform-origin:50% ${g.ink.bottom}px;transform:scale(${1 + 0.09 * e})${
        scene.dots === "alif_tall" && ph.kind === "dots"
          ? ` scaleY(${lerp(0.45, 1, easeOutBack(prog(t, ph.start + 0.25, ph.start + 1.5)))})` : ""}">
        ${halos}${glyphLayers(g, 700, dotScale)}</div></div>`;

    // a green check after each echo: the same mark the game uses later
    for (const echo of phasesOf(scene, "echo")) {
      const b = beatIn("letter", echo.start, echo.end);
      if (b && t >= b.t + b.dur && t < echo.end + 0.3) {
        const k = easeOutBack(prog(t, b.t + b.dur, b.t + b.dur + 0.35)) * (1 - prog(t, echo.end, echo.end + 0.3));
        html += checkBadge(cxCard - 34, CARD.y - 34, k);
      }
    }

    const bx = 1080, by = 370;   // above the pen when it rests in its inkwell
    if (ph.kind === "listen" || ph.kind === "echo") {
      html += bubble("speaker", label("listen"), bx, by, prog(t, ph.start, ph.start + 0.4));
    } else if (ph.kind === "say" || ph.kind === "turn") {
      const s2 = phasesOf(scene, "say").find((p) => t >= p.start && t < p.end) || phasesOf(scene, "say")[0];
      const ring = t >= s2.turn ? timerRing(t, s2.turn, s2.end, 150, scene.color) : "";
      html += bubble("ear", label("say"), bx, by, prog(t, s2.start, s2.start + 0.4),
        `<div style="position:relative;width:150px;height:150px;margin-left:6px">${ring}</div>`);
    } else if (ph.kind === "praise") {
      html += bubble("star", label("praise"), bx, by, prog(t, ph.start, ph.start + 0.4));
      html += checkBadge(cxCard - 34, CARD.y - 34, easeOutBack(prog(t, ph.start, ph.start + 0.35)));
      fxLayer.innerHTML += stars(t, ph.start, cx, cy, 14, 520);
    }

    scene._pen ||= letterPenKeys(scene, g);
    const resolve = (p, tt) => (p.rel ? { x: cardX(scene, tt) + p.x, y: CARD.y + p.y, a: p.a } : p);
    const inDots = t >= dots.start && t < dots.end;
    drawPen(t, track(t, scene._pen, resolve), inDots ? 0 : tap(t, "letter"));
    return html;
  }

  // ---------------------------------------------------------------- game
  const GAME = { W: 540, Y: 210, XS: [330, 1050] };   // the space below belongs to the pointer
  /** Pointing up at a card from beneath it: nib just under its bottom edge, body below. */
  const fromBelow = (x, bottom) => ({ x, y: bottom + 26, a: 180 });

  function gamePenKeys(scene) {
    const { W, Y, XS } = GAME;
    const at = (i) => fromBelow(XS[i] + W / 2, Y + W);
    const ask = phasesOf(scene, "ask")[0], think = phasesOf(scene, "think")[0];
    const ti = scene.options.indexOf(scene.target);
    const keys = [[scene.start, HOME], [scene.start + 0.4, LIFT]];
    // back and forth between the two cards, always beneath them: "this one ... or this one?"
    let side = 1, tk = ask.start + 1.0;
    while (tk < think.end - 0.6) {
      keys.push([tk, at(side)], [tk + 0.55, at(side)]);
      side = 1 - side;
      tk += 1.25;
    }
    keys.push([think.end + 0.3, at(ti)], [scene.end - 0.55, at(ti)], [scene.end, HOME]);
    return keys.sort((a, b) => a[0] - b[0]);
  }

  function drawGame(scene, t) {
    const reveal = phasesOf(scene, "reveal")[0];
    const think = phasesOf(scene, "think")[0];
    const rk = easeOutBack(prog(t, reveal.start, reveal.start + 0.6));
    const { W, Y, XS } = GAME;
    let html = "";
    scene.options.forEach((opt, i) => {
      const isTarget = opt === scene.target;
      const pop = easeOutBack(prog(t, scene.start + 0.3 + i * 0.15, scene.start + 0.9 + i * 0.15));
      let scale = lerp(0.2, 1, pop), op = clamp(pop * 1.5), glow = "";
      if (t >= reveal.start) {
        if (isTarget) { scale *= 1 + 0.1 * rk; glow = `, 0 0 0 26px rgba(47,168,102,${0.35 * clamp(rk)})`; }
        else { scale *= lerp(1, 0.85, clamp(rk)); op *= lerp(1, 0.28, clamp(rk)); }
      }
      const e = isTarget ? env("answer", t) : 0;
      const cx = XS[i] + W / 2, cy = Y + W / 2;
      const g = glyphImage(opt, NEUTRAL_INK, W, 330, DOT_COUNT[scene.dots[i]] || 0);
      let halos = "";
      if (isTarget && t >= reveal.start) {
        const b = beatIn("reveal-dots", reveal.start, reveal.end);
        if (b && t >= b.t) halos = dotHalos(g, () => Math.max(0.5, 1 - 0.5 * prog(t, b.t, b.t + b.dur)));
      }
      if (isTarget) html += rings(t, "answer", cx, cy, 250, "#2FA866");
      html += `<div class="card" style="--c:${t >= reveal.start && isTarget ? "#2FA866" : NEUTRAL_RIM};left:${XS[i]}px;top:${Y}px;
        width:${W}px;height:${W}px;border-radius:80px;transform:scale(${scale});opacity:${op};
        box-shadow:0 26px 50px rgba(120,80,30,.18), inset 0 0 0 14px var(--c)${glow}">
        <div style="position:absolute;inset:0;transform:scale(${1 + 0.08 * e})">${halos}${glyphLayers(g, W, null)}</div></div>`;
      if (isTarget && t >= reveal.start) {
        html += checkBadge(XS[i] - 30, Y - 30, rk);
        fxLayer.innerHTML += stars(t, reveal.start + 0.1, cx, cy, 12, 440);
      }
    });
    const askK = prog(t, scene.start + 0.5, scene.start + 0.9);
    if (t < reveal.start) {
      const extra = t >= think.start
        ? `<div style="position:relative;width:150px;height:150px;margin-left:6px">${timerRing(t, think.start, think.end, 150, "#8E44AD")}</div>` : "";
      html += bubble("question", label("which"), 640, 26, askK, extra);
    } else if (scene.round === scene.rounds) {
      html += bubble("star", label("praise"), 760, 26, prog(t, reveal.start + 0.6, reveal.start + 1.0));
    }
    // progress as short bars, not dots: this is an episode about counting dots
    html += `<div class="progress">${Array.from({ length: scene.rounds }, (_, i) =>
      `<span style="${i < scene.round ? "background:#8E44AD" : ""}"></span>`).join("")}</div>`;
    scene._pen ||= gamePenKeys(scene);
    drawPen(t, track(t, scene._pen), tap(t, "answer"));
    return html;
  }

  // ---------------------------------------------------------------- recap
  function recapLayout(scene) {
    const n = scene.letters.length;
    const S = n > 4 ? 270 : 300, gap = 60;
    const total = n * S + (n - 1) * gap, x0 = (1920 - total) / 2, y = 300;
    // Arabic reads right to left: the first letter sits on the right.
    return { S, y, xs: scene.letters.map((_, i) => x0 + (n - 1 - i) * (S + gap)) };
  }

  function recapPenKeys(scene) {
    const { S, y, xs } = recapLayout(scene);
    const at = (i) => fromBelow(xs[i] + S / 2, y + S);
    const keys = [[scene.start, HOME], [scene.start + 0.5, LIFT]];
    for (const p of scene.phases) {
      if (p.index === undefined) continue;
      keys.push([p.start, at(p.index)], [p.end - 0.05, at(p.index)]);
    }
    keys.push([scene.end - 0.5, keys[keys.length - 1][1]], [scene.end, HOME]);
    return keys.sort((a, b) => a[0] - b[0]);
  }

  function drawRecap(scene, t) {
    const { S, y, xs } = recapLayout(scene);
    const ph = phaseAt(scene, t);
    let html = "";
    scene.letters.forEach((l, i) => {
      const x = xs[i];
      const pop = easeOutBack(prog(t, scene.start + 0.2 + i * 0.12, scene.start + 0.7 + i * 0.12));
      const active = ph && ph.index === i;
      const e = env(`recap:${i}`, t);
      const lift = active ? -22 : 0;
      html += rings(t, `recap:${i}`, x + S / 2, y + S / 2, S / 2 + 10, scene.colors[i]);
      html += `<div class="chip" style="--c:${scene.colors[i]};left:${x}px;top:${y + lift}px;width:${S}px;height:${S}px;
        transform:scale(${lerp(0.2, 1, pop) * (1 + 0.06 * e + (active ? 0.08 : 0))});
        opacity:${clamp(pop * 1.5) * (ph && ph.index !== undefined && !active ? 0.5 : 1)}">
        <div class="glyph" style="font-size:${S * 0.7}px">${l}</div></div>`;
      if (active && ph.kind === "turn") {
        html += `<div style="position:absolute;left:${x + S / 2 - 75}px;top:${y - 190}px;width:150px;height:150px">
          ${timerRing(t, ph.start, ph.end, 150, scene.colors[i])}</div>`;
      }
    });
    if (ph && ph.kind === "intro") html += bubble("speaker", label("listen"), 780, 70, prog(t, ph.start, ph.start + 0.4));
    if (ph && ph.kind === "your-turn") html += bubble("ear", label("say"), 740, 70, prog(t, ph.start, ph.start + 0.4));
    scene._pen ||= recapPenKeys(scene);
    const k = ph && ph.index !== undefined ? tap(t, `recap:${ph.index}`) : 0;
    drawPen(t, track(t, scene._pen), k);
    return html;
  }

  // ---------------------------------------------------------------- title, besmele, outro
  const chipX = (scene, i, size, gap = 40) => {
    const n = scene.letters.length, total = n * size + (n - 1) * gap;
    return (1920 - total) / 2 + (n - 1 - i) * (size + gap);
  };

  /** A row of letter chips; chip i appears with its name (beat ``prefix:i``). */
  function namedChips(scene, t, y, size, prefix, wave = false) {
    return scene.letters.map((l, i) => {
      const b = beatIn(`${prefix}:${i}`, scene.start, scene.end);
      const at = b ? b.t - 0.25 : scene.start;
      const pop = easeOutBack(prog(t, at, at + 0.5));
      const e = env(`${prefix}:${i}`, t);
      const dy = wave ? Math.sin(t * 3.2 - i * 0.8) * 10 : 0;
      return `<div class="chip" style="--c:${scene.colors[i]};left:${chipX(scene, i, size)}px;top:${y + dy}px;width:${size}px;height:${size}px;
        transform:scale(${lerp(0.1, 1, pop) * (1 + 0.08 * e)});opacity:${clamp(pop * 1.5)}">
        <div class="glyph" style="font-size:${size * 0.7}px">${l}</div></div>`;
    }).join("");
  }

  function chipPenKeys(scene, y, size, prefix, endKeys) {
    const keys = [[scene.start, HOME], [scene.start + 0.5, LIFT]];
    scene.letters.forEach((_, i) => {
      const b = beatIn(`${prefix}:${i}`, scene.start, scene.end);
      if (!b) return;
      const p = fromBelow(chipX(scene, i, size) + size / 2, y + size + 10);
      keys.push([b.t, p], [b.t + b.dur, p]);
    });
    return keys.concat(endKeys).sort((a, b) => a[0] - b[0]);
  }

  function drawTitle(scene, t) {
    const k = easeOutBack(prog(t, scene.start + 0.1, scene.start + 0.8));
    let html = `<div class="big-title" style="top:110px;font-size:190px;transform:scale(${k});letter-spacing:4px">
      Elif<span style="color:#E4572E">-</span>Ba</div>`;
    html += `<div class="big-title" style="top:360px;font-size:64px;font-weight:700;color:#6B6575;opacity:${prog(t, scene.start + 0.4, scene.start + 0.9)}">
      ${scene.episode}. Ders</div>`;
    html += namedChips(scene, t, 500, 230, "title");
    scene._pen ||= chipPenKeys(scene, 500, 230, "title", [[scene.end - 0.5, LIFT], [scene.end, HOME]]);
    const i = scene.letters.findIndex((_, j) => beatAt(`title:${j}`, t, 0));
    drawPen(t, track(t, scene._pen), i >= 0 ? tap(t, `title:${i}`) : 0);
    return html;
  }

  function drawBesmele(scene, t) {
    const k = easeInOut(prog(t, scene.start + 0.1, scene.start + 1.4));
    drawPen(t, HOME);
    return `<div class="frame" style="opacity:${k}"></div>
      <div class="besmele" style="opacity:${k};transform:translateY(${(1 - k) * 20}px)">بِسْمِ اللّٰهِ الرَّحْمٰنِ الرَّحِيمِ</div>
      <div class="big-title" style="top:650px;font-size:46px;font-weight:600;color:#8A6A3A;opacity:${k * 0.9}">
        Bismillâhirrahmânirrahîm</div>`;
  }

  function drawOutro(scene, t) {
    const k = easeOutBack(prog(t, scene.start, scene.start + 0.7));
    let html = `<div class="big-title" style="top:140px;font-size:170px;color:#E4572E;transform:scale(${k})">${label("praise")}</div>`;
    html += namedChips(scene, t, 470, 220, "outro", true);
    fxLayer.innerHTML += stars(t, scene.start + 0.2, 960, 250, 16, 760);
    const settle = phasesOf(scene, "settle")[0];
    fxLayer.innerHTML += stars(t, settle.start, 960, 250, 16, 760);
    scene._pen ||= chipPenKeys(scene, 470, 220, "outro", [[settle.start + 0.2, LIFT], [settle.start + 0.9, HOME]]);
    const i = scene.letters.findIndex((_, j) => beatAt(`outro:${j}`, t, 0));
    drawPen(t, track(t, scene._pen), i >= 0 ? tap(t, `outro:${i}`) : 0);
    return html;
  }

  const NEUTRAL_INK = "#3A3550", NEUTRAL_RIM = "#D3C4A9";
  const DRAW = { besmele: drawBesmele, title: drawTitle, letter: drawLetter, game: drawGame, recap: drawRecap, outro: drawOutro };

  // ---------------------------------------------------------------- frame
  window.seek = function seek(t) {
    drawBackground(t);
    fxLayer.innerHTML = "";
    let html = "";
    for (const scene of TL.scenes) {
      if (t < scene.start - 0.001 || t >= scene.end) continue;
      // One scene at a time: it fades out over its own last moments, and the next
      // grows in after it has gone — never one letter on top of another.
      const fade = scene.type === "outro" ? 1.2 : 0.3;
      const op = 1 - easeInOut(prog(t, scene.end - fade, scene.end));
      // The pen and inkwell carry across scene changes; they are absent for the
      // besmele, arrive with the title, and leave with the outro.
      penOpacity = scene.type === "besmele" ? 0
        : scene.type === "title" ? easeInOut(prog(t, scene.start, scene.start + 0.6))
        : scene.type === "outro" ? op : 1;
      const inner = DRAW[scene.type](scene, t);
      html += `<div class="layer" style="opacity:${op}">${inner}</div>`;
      fxLayer.style.opacity = op;
    }
    scenesLayer.innerHTML = html;
  };

  window.__duration = TL.duration;
  window.__ready = document.fonts.ready.then(() =>
    Promise.all([document.fonts.load('700 100px "Scheherazade"', "ب"), document.fonts.load('800 60px "Nunito"', "Şş")]));
})();
