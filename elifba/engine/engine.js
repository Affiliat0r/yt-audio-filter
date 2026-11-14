/* Elif-Ba video engine.
 *
 * Draws one frame of the video for any time t. Nothing here runs on a clock:
 * every position, size and opacity is computed from t and the timeline, so
 * render.py can step through frames one by one and get the same video every time.
 *
 *   window.__TIMELINE   the storyboard (set before this script runs)
 *   window.seek(t)      draw the frame at t seconds
 *
 * The only companion is a kamış kalem (reed pen) with its divit (inkwell) — no
 * character, no face; "Oku! ... kalemle öğreten Rabbin" (Alak 96:1-5). It stands
 * just outside the card and taps toward the letter as its name is said. It never
 * crosses a letter.
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

  /** Loudness-like envelope of the sound on ``target``: fast attack, slow fall. */
  function env(target, t) {
    const b = beatAt(target, t);
    if (!b) return 0;
    const k = t - b.t;
    const attack = clamp(k / 0.07);
    const body = k < b.dur ? 1 - 0.45 * (k / b.dur) : 0.55 * (1 - (k - b.dur) / 0.35);
    return clamp(attack * body);
  }

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
  // Angle a: 0 = nib straight down, body above it.
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

  /** Interpolate a pose along [[time, pose], ...]. Holds at both ends. */
  function track(t, keys) {
    if (t <= keys[0][0]) return keys[0][1];
    for (let i = 1; i < keys.length; i++) {
      if (t < keys[i][0]) {
        const [t0, p0] = keys[i - 1], [t1, p1] = keys[i];
        const k = easeInOut(prog(t, t0, t1));
        return { x: lerp(p0.x, p1.x, k), y: lerp(p0.y, p1.y, k), a: lerp(p0.a, p1.a, k) };
      }
    }
    return keys[keys.length - 1][1];
  }

  /** A quick tap toward the letter while ``target`` sounds. */
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
    const x = pose.x - Math.sin(rad) * 18 * tapK, y = pose.y + Math.cos(rad) * 18 * tapK;   // the nib moves the way it points
    const a = pose.a + 0.8 * Math.sin(t * 1.3);     // a living hand is never quite still
    penEl.style.transformOrigin = `${TIP_X}px ${TIP_Y}px`;
    penEl.style.transform = `translate(${x - TIP_X}px, ${y - TIP_Y}px) rotate(${a}deg)`;
  }

  // ---------------------------------------------------------------- fx
  const STAR_PATH = "M50 6l13 28 30 3-23 20 7 30-27-16-27 16 7-30-23-20 30-3z";
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

  /** Soft rings spreading from the card while its name sounds. */
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

  // ---------------------------------------------------------------- the letter, drawn once per colour
  // Drawn on a canvas so the ink itself is centred in the card (a text box would
  // centre the advance width, which leaves letters like ى or ر visibly off-centre).
  // With ``markColor``, the harakat are drawn in their own colour: the whole text
  // in markColor first, then the bare letter over it in ``color`` — what is left
  // showing in markColor is exactly the mark.
  const HARAKAT = /[ً-ْٰ۪ٖ]/g;
  const glyphCache = {};
  function glyphImage(letter, color, box, fontPx, markColor = null) {
    const key = `${letter}|${color}|${box}|${fontPx}|${markColor}`;
    if (glyphCache[key]) return glyphCache[key];
    const cv = document.createElement("canvas");
    cv.width = cv.height = box;
    const ctx = cv.getContext("2d");
    ctx.direction = "rtl";
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    // A tall letter with a mark on it (غَ) can outgrow the card: shrink only those,
    // so the ink always fits inside 76% of the box. Everything else keeps its size.
    ctx.font = `700 ${fontPx}px "Scheherazade"`;
    let m = ctx.measureText(letter);
    const fit = Math.min(1, (0.76 * box) / (m.actualBoundingBoxAscent + m.actualBoundingBoxDescent),
      (0.76 * box) / (m.actualBoundingBoxLeft + m.actualBoundingBoxRight));
    if (fit < 1) {
      ctx.font = `700 ${Math.floor(fontPx * fit)}px "Scheherazade"`;
      m = ctx.measureText(letter);
    }
    const inkW = m.actualBoundingBoxLeft + m.actualBoundingBoxRight;
    const inkH = m.actualBoundingBoxAscent + m.actualBoundingBoxDescent;
    const x = (box - inkW) / 2 + m.actualBoundingBoxLeft, y = (box - inkH) / 2 + m.actualBoundingBoxAscent;
    if (markColor) {
      ctx.fillStyle = markColor;
      ctx.fillText(letter, x, y);
      ctx.fillStyle = color;
      ctx.fillText(letter.replace(HARAKAT, ""), x, y);
    } else {
      ctx.fillStyle = color;
      ctx.fillText(letter, x, y);
    }
    return (glyphCache[key] = cv.toDataURL("image/png"));
  }

  // ---------------------------------------------------------------- scenes
  const CARD = { x: 610, y: 180, w: 700 };
  // just outside the card's right edge, leaning away: it taps toward the letter, never across it
  const SIDE = { x: CARD.x + CARD.w + 16, y: CARD.y + CARD.w / 2, a: 22 };

  function drawLetter(scene, t) {
    const pop = easeOutBack(prog(t, scene.start, scene.start + 0.6));
    const e = env("letter", t);
    const cx = CARD.x + CARD.w / 2, cy = CARD.y + CARD.w / 2;
    const url = glyphImage(scene.text, scene.color, CARD.w, 430, scene.mark);
    let html = rings(t, "letter", cx, cy, 330, scene.rim);
    html += `<div class="card ${scene.heavy ? "heavy" : ""}" style="--c:${scene.rim};left:${CARD.x}px;top:${CARD.y}px;
      transform:scale(${lerp(0.2, 1, pop) * (1 + 0.035 * e)});opacity:${clamp(pop * 1.5)}">
      <img src="${url}" style="position:absolute;inset:0;width:${CARD.w}px;height:${CARD.w}px;
        transform:scale(${1 + 0.09 * e});filter:drop-shadow(0 10px 0 rgba(0,0,0,.07))"></div>`;
    // the first letter: the pen lifts out of its inkwell; after that it stays beside the card
    const keys = scene.number === 0
      ? [[scene.start, HOME], [scene.start + 0.35, LIFT], [scene.start + 0.65, SIDE]]
      : [[scene.start, SIDE]];
    drawPen(t, track(t, keys), tap(t, "letter"));
    return html;
  }

  // ---------------------------------------------------------------- one letter, three harakat
  // A wide card with the three forms side by side, read right to left (fetha,
  // kesra, damme). The form being said is lifted and lit; the others step back.
  // The pen points at it from below — nib up, body below the card — and moves
  // right to left with the sounds.
  const TRI = { x: 210, y: 200, w: 1500, h: 570 };
  const formX = (k) => TRI.x + TRI.w * (5 - 2 * k) / 6;              // k=0 rightmost
  const below = (k) => ({ x: formX(k), y: TRI.y + TRI.h + 26, a: 180 });

  function tripleCard(forms, color, rim, heavy, x, y, w, h, look) {
    const box = Math.min(h - 40, w / 3);
    let html = `<div class="card ${heavy ? "heavy" : ""}" style="--c:${rim};left:${x}px;top:${y}px;width:${w}px;height:${h}px;
      border-radius:${h * 0.16}px">`;
    forms.forEach((f, k) => {
      const cx = w * (5 - 2 * k) / 6, cy = h / 2;
      const { op = 1, sc = 1, glow = 0 } = look ? look(k) : {};
      if (glow > 0) html += `<div style="position:absolute;left:${cx - box * 0.62}px;top:${cy - box * 0.62}px;width:${box * 1.24}px;height:${box * 1.24}px;
        border-radius:50%;background:radial-gradient(circle, rgba(255,214,120,${0.55 * glow}) 0%, rgba(255,214,120,0) 68%)"></div>`;
      html += `<img src="${glyphImage(f.text, color, Math.round(box), Math.round(box * 0.66), f.mark)}" style="position:absolute;
        left:${cx - box / 2}px;top:${cy - box / 2}px;width:${box}px;height:${box}px;opacity:${op};transform:scale(${sc});
        filter:drop-shadow(0 8px 0 rgba(0,0,0,.06))">`;
    });
    return html + "</div>";
  }

  function drawTriple(scene, t) {
    const pop = easeOutBack(prog(t, scene.start, scene.start + 0.6));
    const beats = [0, 1, 2].map((k) => (beatsByTarget[`form:${k}`] || []).find((b) => b.t >= scene.start && b.t < scene.end));
    const current = [2, 1, 0].find((k) => beats[k] && t >= beats[k].t);       // the last one said
    const settle = scene.end - 0.6;
    const look = (k) => {
      if (current === undefined || t >= settle) return {};
      const e = env(`form:${k}`, t);
      return k === current ? { op: 1, sc: 1.08 + 0.06 * e, glow: 1 } : { op: 0.4, sc: 0.94 };
    };
    let html = "";
    if (current !== undefined) html += rings(t, `form:${current}`, formX(current), TRI.y + TRI.h / 2, 180, scene.rim);
    html += `<div style="position:absolute;inset:0;transform-origin:960px ${TRI.y + TRI.h / 2}px;
      transform:scale(${lerp(0.2, 1, pop)});opacity:${clamp(pop * 1.5)}">
      ${tripleCard(scene.forms, scene.color, scene.rim, scene.heavy, TRI.x, TRI.y, TRI.w, TRI.h, look)}</div>`;
    // the pointer: lifts out of the inkwell for the first letter, then glides back to
    // the right for each new letter and follows the sounds right to left
    const keys = scene.number === 0
      ? [[scene.start, HOME], [scene.start + 0.35, LIFT], [scene.start + 0.7, below(0)]]
      : [[scene.start, below(2)], [scene.start + 0.6, below(0)]];
    beats.forEach((b, k) => { if (b && k > 0) keys.push([b.t - 0.3, below(k - 1)], [b.t, below(k)]); });
    const k = current === undefined ? -1 : current;
    drawPen(t, track(t, keys), k >= 0 ? tap(t, `form:${k}`) : 0);
    return html;
  }

  function drawTitle(scene, t) {
    const k = easeOutBack(prog(t, scene.start + 0.1, scene.start + 0.8));
    drawPen(t, HOME);
    return `<div class="big-title" style="top:300px;font-size:220px;transform:scale(${k});letter-spacing:4px">
      Elif<span style="color:#E4572E">-</span>Ba</div>
      <div class="big-title" style="top:590px;font-size:76px;font-weight:700;color:#6B6575;opacity:${prog(t, scene.start + 0.5, scene.start + 1.0)}">
      ${scene.subtitle}</div>`;
  }

  function drawBesmele(scene, t) {
    const k = easeInOut(prog(t, scene.start + 0.1, scene.start + 1.4));
    drawPen(t, HOME);
    return `<div class="frame" style="opacity:${k}"></div>
      <div class="besmele" style="opacity:${k};transform:translateY(${(1 - k) * 20}px)">بِسْمِ اللّٰهِ الرَّحْمٰنِ الرَّحِيمِ</div>
      <div class="big-title" style="top:650px;font-size:46px;font-weight:600;color:#8A6A3A;opacity:${k * 0.9}">
        Bismillâhirrahmânirrahîm</div>`;
  }

  /** All the lesson's cards together, in rows of seven, right to left. */
  function letterGrid(scene, t, y0, size, popFrom) {
    const cols = 7, gap = Math.round(size * 0.16);
    const w = cols * size + (cols - 1) * gap, x0 = (1920 - w) / 2;
    return scene.texts.map((l, i) => {
      const r = Math.floor(i / cols), c = i % cols;
      const at = popFrom + i * 0.05;
      const p = popFrom === null ? 1 : easeOutBack(prog(t, at, at + 0.4));
      const x = x0 + (cols - 1 - c) * (size + gap), y = y0 + r * (size + gap);
      return `<div class="chip" style="--c:${scene.rims[i]};left:${x}px;top:${y}px;width:${size}px;height:${size}px;
        border-radius:${size * 0.27}px;transform:scale(${lerp(0.1, 1, p)});opacity:${clamp(p * 1.5)}">
        <img src="${glyphImage(l, scene.colors[i], size, size * 0.64, scene.mark)}" style="width:${size}px;height:${size}px"></div>`;
    }).join("");
  }

  function drawOutro(scene, t) {
    const k = easeOutBack(prog(t, scene.start, scene.start + 0.7));
    let html = `<div class="big-title" style="top:60px;font-size:150px;color:#E4572E;transform:scale(${k})">Aferin!</div>`;
    html += letterGrid(scene, t, 290, 128, scene.start + 0.3);
    fxLayer.innerHTML += stars(t, scene.start + 1.8, 960, 160, 16, 700);
    drawPen(t, track(t, [[scene.start, SIDE], [scene.start + 0.5, LIFT], [scene.start + 0.9, HOME]]));
    return html;
  }

  /** The video's thumbnail: its name, and every card of the lesson. */
  function drawThumb(scene, t) {
    penOpacity = 0;            // the cards need the whole frame
    drawPen(t, HOME);
    const head = `<div class="big-title" style="top:26px;font-size:170px;letter-spacing:3px">
        Elif<span style="color:#E4572E">-</span>Ba <span style="color:#6B6575;font-size:120px">· ${scene.heading}</span></div>`;
    if (!scene.triples) return head + letterGrid(scene, t, 262, 176, null);
    // the harakat lesson: its first four letters, each with all three marks
    return head + scene.triples.map((forms, i) => {
      const col = 1 - (i % 2), row = Math.floor(i / 2);      // right to left, top to bottom
      return tripleCard(forms, scene.colors[i], scene.rims[i], false, 120 + col * 860, 270 + row * 400, 820, 360, null);
    }).join("");
  }

  const DRAW = { besmele: drawBesmele, title: drawTitle, letter: drawLetter, triple: drawTriple, outro: drawOutro, thumb: drawThumb };

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
      // the pen and inkwell are absent for the besmele, arrive with the title, leave with the outro
      penOpacity = scene.type === "besmele" ? 0
        : scene.type === "title" ? easeInOut(prog(t, scene.start, scene.start + 0.6))
        : scene.type === "outro" ? op : 1;
      html += `<div class="layer" style="opacity:${op}">${DRAW[scene.type](scene, t)}</div>`;
      fxLayer.style.opacity = op;
    }
    scenesLayer.innerHTML = html;
  };

  window.__duration = TL.duration;
  window.__ready = document.fonts.ready.then(() =>
    Promise.all([document.fonts.load('700 100px "Scheherazade"', "ب"), document.fonts.load('800 60px "Nunito"', "Şş")]));
})();
