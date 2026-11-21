/* Elif-Ba video engine — "the tulip garden".
 *
 * Draws one frame of a lesson for any time t, on one canvas. Nothing runs on a
 * clock: every position, colour and opacity is computed from t and the
 * timeline, so render.py can step through frames one by one and get the same
 * video every time.
 *
 *   window.__TIMELINE   the storyboard (set before this script runs)
 *   window.seek(t)      draw the frame at t seconds
 *
 * The look: a soft 3D "clay" diorama at golden hour — an Ottoman tulip garden
 * with domes and minarets on the horizon, in layered parallax. The camera
 * glides right to left (the way Arabic is read) from tulip to tulip; each tulip
 * blooms and its letter rises out of it, bounces each time Diyanet says its
 * name, then flies up into a bunting that fills across the top as the lesson
 * goes on. At the end night falls, a crescent rises, lanterns float up.
 *
 * Rules carried over from the toddler reviews: no faces or creatures; nothing
 * small near a letter that could be read as part of it (no sparkles or
 * particles around letters: emphasis is light — a halo and a bounce).
 */
(() => {
  "use strict";

  const TL = window.__TIMELINE;
  const W = 1920, H = 1080;
  const cv = document.getElementById("world");
  const ctx = cv.getContext("2d");
  const overlay = document.getElementById("overlay");

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
  const hash = (n) => {
    const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
    return x - Math.floor(x);
  };

  // ---------------------------------------------------------------- colour
  const rgb = (hex) => { const v = parseInt(hex.slice(1), 16); return [(v >> 16) & 255, (v >> 8) & 255, v & 255]; };
  const mix = (a, b, k) => a.map((x, i) => x + (b[i] - x) * k);
  const css = (c, a = 1) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
  /** Lighten (amt > 0, toward white) or darken (amt < 0, toward black). */
  const shade = (hex, amt) => (amt >= 0 ? mix(rgb(hex), [255, 255, 255], amt) : mix(rgb(hex), [0, 0, 0], -amt));

  // ---------------------------------------------------------------- beats
  const beatsByTarget = {};
  for (const b of TL.beats) (beatsByTarget[b.target] ||= []).push(b);
  const beatsIn = (target, a, b) => (beatsByTarget[target] || []).filter((x) => x.t >= a && x.t < b);
  function beatAt(target, t, tail = 0.35) {
    for (const b of beatsByTarget[target] || []) if (t >= b.t && t < b.t + b.dur + tail) return b;
    return null;
  }
  /** Loudness-like envelope of the sound on ``target``. */
  function env(target, t) {
    const b = beatAt(target, t);
    if (!b) return 0;
    const k = t - b.t;
    const body = k < b.dur ? 1 - 0.45 * (k / b.dur) : 0.55 * (1 - (k - b.dur) / 0.35);
    return clamp(clamp(k / 0.07) * body);
  }
  /** A springy squash-and-stretch after each beat on ``target`` in [a, b). */
  function bounce(target, t, a, b) {
    let v = 0;
    for (const x of beatsIn(target, a, b)) {
      const k = t - x.t;
      if (k >= 0 && k < 1.4) v += Math.exp(-4.5 * k) * Math.sin(13 * k);
    }
    return v;
  }

  // ---------------------------------------------------------------- the lesson's stations
  const HARAKAT = /[ً-ْٰ۪ٖ]/g;
  const scenes = TL.scenes;
  const stations = scenes.filter((s) => ["letter", "triple", "words", "build"].includes(s.type));
  stations.forEach((s, i) => {
    s.idx = i;
    s.base = s.letter || (s.word || s.text || s.forms[0].text).replace(HARAKAT, "");
  });
  const N = stations.length;
  const STEP = 1700;
  const stationX = (i) => -i * STEP;          // the camera travels left: right to left, as Arabic reads
  const TITLE_X = STEP;
  const firstStart = stations.length ? stations[0].start : 0;
  const lastEnd = stations.length ? stations[N - 1].end : TL.duration;
  const sceneAt = (t) => scenes.find((s) => t >= s.start && t < s.end) || scenes[scenes.length - 1];

  const TULIPS = ["#E8505B", "#F28AA0", "#F6B93B", "#9D7BD8", "#F3722C", "#E56399", "#5FA8D3"];
  const CLAY_INK = "#3E4A7A";                  // letters that carry coloured harakat

  // ---------------------------------------------------------------- camera and time of day
  function camera(t) {
    const sc = sceneAt(t);
    let x = TITLE_X, y = 0;
    if (sc.type === "thumb") return { x: TITLE_X, y: 0 };
    if (sc.type === "besmele") y = -430;
    else if (sc.type === "title") y = lerp(-430, 0, easeInOut(prog(t, sc.start, sc.start + 1.8)));
    else if (sc.type === "outro") { x = stationX(N - 1); y = lerp(0, -430, easeInOut(prog(t, sc.start + 0.4, sc.start + 2.6))); }
    else {
      const prev = sc.idx === 0 ? TITLE_X : stationX(sc.idx - 1);
      x = lerp(prev, stationX(sc.idx), easeInOut(prog(t, sc.start, sc.start + 1.15)));
    }
    return { x, y };
  }

  /** 0 = soft morning, 0.75 = golden hour (the lesson runs between), 1 = night (the ending). */
  function dayK(t) {
    const sc = sceneAt(t);
    if (sc.type === "besmele" || sc.type === "title") return 0;
    if (sc.type === "outro") return lerp(0.75, 1, easeInOut(prog(t, sc.start, sc.start + 2.4)));
    return 0.75 * prog(t, firstStart, lastEnd);
  }

  const SKY = {
    morning: { top: "#8EC5F5", mid: "#CFE8FA", hor: "#FFE7CC" },
    golden: { top: "#7FA6E6", mid: "#FFD2A4", hor: "#FFAD84" },
    night: { top: "#121944", mid: "#33296A", hor: "#A6607A" },
  };
  function skyAt(k) {
    const [a, b, f] = k <= 0.75 ? [SKY.morning, SKY.golden, k / 0.75] : [SKY.golden, SKY.night, (k - 0.75) / 0.25];
    return { top: mix(rgb(a.top), rgb(b.top), f), mid: mix(rgb(a.mid), rgb(b.mid), f), hor: mix(rgb(a.hor), rgb(b.hor), f) };
  }
  const nightOf = (k) => clamp((k - 0.75) / 0.25);

  // ---------------------------------------------------------------- sky
  function drawSky(t, cam, k) {
    const s = skyAt(k);
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, css(s.top));
    g.addColorStop(0.5, css(s.mid));
    g.addColorStop(0.82, css(s.hor));
    g.addColorStop(1, css(s.hor));
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    const night = nightOf(k);
    // the sun, sinking slowly through the lesson
    // high in the top-right corner, clear of every place a letter can stand
    const sx = W - 170, sy = 175 + 120 * Math.min(1, k / 0.8) - cam.y * 0.05;
    if (night < 1) {
      ctx.save();
      ctx.globalAlpha = 1 - night;
      // light rays, turning very slowly
      ctx.translate(sx, sy);
      ctx.rotate(t * 0.02);
      for (let i = 0; i < 14; i++) {
        ctx.rotate((Math.PI * 2) / 14);
        const rg = ctx.createLinearGradient(0, 0, 900, 0);
        rg.addColorStop(0, "rgba(255,244,214,0.16)");
        rg.addColorStop(1, "rgba(255,244,214,0)");
        ctx.fillStyle = rg;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(900, -55);
        ctx.lineTo(900, 55);
        ctx.closePath();
        ctx.fill();
      }
      ctx.restore();
      ctx.save();
      ctx.globalAlpha = 1 - night;
      const glow = ctx.createRadialGradient(sx, sy, 40, sx, sy, 380);
      glow.addColorStop(0, "rgba(255,246,222,0.95)");
      glow.addColorStop(0.3, "rgba(255,226,170,0.45)");
      glow.addColorStop(1, "rgba(255,210,150,0)");
      ctx.fillStyle = glow;
      ctx.fillRect(sx - 380, sy - 380, 760, 760);
      ctx.fillStyle = "rgba(255,250,232,1)";
      ctx.beginPath();
      ctx.arc(sx, sy, 92, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    if (night > 0) drawMoon(t, night, cam);
  }

  const moonCanvas = (() => {
    const c = document.createElement("canvas");
    c.width = c.height = 360;
    const m = c.getContext("2d");
    const g = m.createRadialGradient(180, 180, 60, 180, 180, 180);
    g.addColorStop(0, "rgba(255,240,190,0.55)");
    g.addColorStop(1, "rgba(255,240,190,0)");
    m.fillStyle = g;
    m.fillRect(0, 0, 360, 360);
    const d = document.createElement("canvas");
    d.width = d.height = 360;
    const dm = d.getContext("2d");
    dm.fillStyle = "#FFF1C2";
    dm.beginPath();
    dm.arc(180, 180, 88, 0, Math.PI * 2);
    dm.fill();
    dm.globalCompositeOperation = "destination-out";
    dm.beginPath();
    dm.arc(214, 160, 80, 0, Math.PI * 2);
    dm.fill();
    m.drawImage(d, 0, 0);
    return c;
  })();
  function drawMoon(t, night, cam) {
    ctx.save();
    ctx.globalAlpha = night;
    const y = 250 + 120 * (1 - night) - (cam.y + 430) * 0.05;
    ctx.translate(W * 0.76, y);
    ctx.rotate(-0.35);
    ctx.drawImage(moonCanvas, -180, -180);
    ctx.restore();
  }

  // ---------------------------------------------------------------- clouds (pre-drawn, soft clay)
  const cloudSprites = [0, 1, 2, 3].map((v) => {
    const c = document.createElement("canvas");
    c.width = 520;
    c.height = 240;
    const m = c.getContext("2d");
    const puffs = 6 + v;
    for (let i = 0; i < puffs; i++) {
      const px = 90 + (340 * i) / (puffs - 1) + (hash(v * 31 + i) - 0.5) * 30;
      const r = 50 + 55 * Math.sin((Math.PI * i) / (puffs - 1)) + hash(v * 7 + i) * 18;
      const py = 150 - r * 0.35;
      const g = m.createLinearGradient(0, py - r, 0, py + r);
      g.addColorStop(0, "#FFFFFF");
      g.addColorStop(0.65, "#FFF4EC");
      g.addColorStop(1, "#F2D5C9");
      m.fillStyle = g;
      m.beginPath();
      m.arc(px, py, r, 0, Math.PI * 2);
      m.fill();
    }
    return c;
  });
  // Clouds keep to two bands — high up behind the bunting, and low along the
  // horizon — so the sky behind the letters stays clear: no edges near a letter.
  function drawClouds(t, cam, k) {
    const span = W + 900, night = nightOf(k);
    for (let i = 0; i < 8; i++) {
      const base = i * 520 + hash(i) * 300;
      let x = (base - cam.x * 0.08 + t * 9) % span;
      if (x < 0) x += span;
      x -= 450;
      const high = i % 2 === 0;
      const s = high ? 0.45 + hash(i + 3) * 0.3 : 0.55 + hash(i + 3) * 0.35;
      const y = (high ? -40 + hash(i + 9) * 50 : 520 + hash(i + 9) * 70) - cam.y * 0.18;
      ctx.save();
      ctx.globalAlpha = (0.9 - 0.25 * hash(i + 5)) * (1 - 0.55 * night);
      ctx.drawImage(cloudSprites[i % 4], x, y, 520 * s, 240 * s);
      ctx.restore();
    }
  }

  // ---------------------------------------------------------------- the horizon: domes and minarets
  function mosque(x, base, s, fill) {
    ctx.fillStyle = fill;
    ctx.beginPath();
    // body
    ctx.rect(x - 150 * s, base - 70 * s, 300 * s, 70 * s);
    // semi-domes
    ctx.ellipse(x - 105 * s, base - 70 * s, 55 * s, 48 * s, 0, Math.PI, 0);
    ctx.ellipse(x + 105 * s, base - 70 * s, 55 * s, 48 * s, 0, Math.PI, 0);
    // drum and great dome
    ctx.rect(x - 92 * s, base - 110 * s, 184 * s, 42 * s);
    ctx.ellipse(x, base - 110 * s, 96 * s, 92 * s, 0, Math.PI, 0);
    ctx.fill();
    // finial
    ctx.fillRect(x - 3 * s, base - 232 * s, 6 * s, 32 * s);
    // minarets
    for (const mx of [-215, 215]) {
      const cx = x + mx * s;
      ctx.fillRect(cx - 11 * s, base - 300 * s, 22 * s, 300 * s);
      ctx.fillRect(cx - 17 * s, base - 215 * s, 34 * s, 8 * s);
      ctx.fillRect(cx - 15 * s, base - 270 * s, 30 * s, 7 * s);
      ctx.beginPath();
      ctx.moveTo(cx - 12 * s, base - 300 * s);
      ctx.lineTo(cx, base - 372 * s);
      ctx.lineTo(cx + 12 * s, base - 300 * s);
      ctx.fill();
    }
  }
  function drawHorizon(t, cam, k) {
    const par = 0.22, s = skyAt(k);
    const haze = css(mix(mix(rgb("#B6A6DA"), s.hor, 0.35), [40, 30, 80], nightOf(k) * 0.55));
    const baseY = 760 - cam.y * par * 0.9;
    // far ridge
    ctx.fillStyle = haze;
    ctx.beginPath();
    ctx.moveTo(0, H);
    for (let x = 0; x <= W; x += 40) {
      const wx = cam.x + (x - W / 2) / par;
      ctx.lineTo(x, baseY + 10 * Math.sin(wx * 0.0011) + 6 * Math.sin(wx * 0.0031));
    }
    ctx.lineTo(W, H);
    ctx.fill();
    const from = Math.floor((cam.x - W / 2 / par) / 2600) - 1, to = Math.ceil((cam.x + W / 2 / par) / 2600) + 1;
    for (let m = from; m <= to; m++) {
      const wx = m * 2600 + hash(m) * 900;
      const x = (wx - cam.x) * par + W / 2;
      if (x < -500 || x > W + 500) continue;
      mosque(x, baseY + 6, 0.36 + hash(m + 40) * 0.14, haze);   // minaret tops stay below y ~ 620
    }
  }

  // ---------------------------------------------------------------- hills, cypresses, meadow
  function cypress(x, base, h, dark, light) {
    ctx.fillStyle = dark;
    ctx.beginPath();
    ctx.moveTo(x, base - h);
    ctx.bezierCurveTo(x + h * 0.2, base - h * 0.7, x + h * 0.17, base - h * 0.1, x, base);
    ctx.bezierCurveTo(x - h * 0.17, base - h * 0.1, x - h * 0.2, base - h * 0.7, x, base - h);
    ctx.fill();
    ctx.fillStyle = light;
    ctx.beginPath();
    ctx.moveTo(x, base - h);
    ctx.bezierCurveTo(x - h * 0.2, base - h * 0.7, x - h * 0.17, base - h * 0.1, x, base);
    ctx.bezierCurveTo(x - h * 0.06, base - h * 0.3, x - h * 0.08, base - h * 0.7, x, base - h);
    ctx.fill();
  }

  function hillLayer(cam, par, baseY, amp1, f1, amp2, f2, top, bottom) {
    const yAt = (wx) => baseY - cam.y * par + amp1 * Math.sin(wx * f1 + 1) + amp2 * Math.sin(wx * f2 + 2);
    ctx.beginPath();
    ctx.moveTo(0, H);
    for (let x = 0; x <= W + 20; x += 20) ctx.lineTo(x, yAt(cam.x + (x - W / 2) / par));
    ctx.lineTo(W, H);
    const g = ctx.createLinearGradient(0, baseY - 60 - cam.y * par, 0, H);
    g.addColorStop(0, top);
    g.addColorStop(1, bottom);
    ctx.fillStyle = g;
    ctx.fill();
    return yAt;
  }

  function drawHills(t, cam, k, trees = true) {
    const night = nightOf(k), dim = (hex, a) => css(mix(shade(hex, a || 0), [30, 30, 70], night * 0.6));
    // middle hills with cypress rows
    const par = 0.45;
    const midY = hillLayer(cam, par, 800, 38, 0.0021, 22, 0.0049, dim("#A9D795"), dim("#8CC27F"));
    const from = Math.floor((cam.x - W / par) / 430), to = Math.ceil((cam.x + W / par) / 430);
    for (let c = from; c <= to; c++) {
      if (!trees || hash(c + 77) < 0.35) continue;
      const wx = c * 430 + hash(c) * 200;
      const x = (wx - cam.x) * par + W / 2;
      if (x < -100 || x > W + 100) continue;
      const h = 95 + hash(c + 3) * 70;
      cypress(x, midY(wx) + 12, h, dim("#3F7D52"), dim("#5E9E6B"));
    }
    // the near meadow, with small tulips far below the letters
    const par2 = 0.75;
    const nearY = hillLayer(cam, par2, 905, 24, 0.0031, 13, 0.0077, dim("#A4DA8C"), dim("#7DBF69"));
    const f2 = Math.floor((cam.x - W / par2) / 70), t2 = Math.ceil((cam.x + W / par2) / 70);
    for (let c = f2; c <= t2; c++) {
      if (hash(c + 5) < 0.55) continue;
      const wx = c * 70 + hash(c + 1) * 50;
      const x = (wx - cam.x) * par2 + W / 2;
      if (x < -20 || x > W + 20) continue;
      const y = nearY(wx) + 28 + hash(c + 2) * 70;
      const col = TULIPS[Math.floor(hash(c + 8) * TULIPS.length)];
      ctx.strokeStyle = dim("#5C9E52");
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(x, y + 22);
      ctx.lineTo(x, y);
      ctx.stroke();
      ctx.fillStyle = css(mix(rgb(col), [40, 30, 70], night * 0.5));
      ctx.beginPath();
      ctx.ellipse(x, y - 4, 7, 10, 0, 0, Math.PI * 2);
      ctx.fill();
    }
    // the ground the big tulips stand in
    hillLayer(cam, 1.0, 985, 10, 0.004, 6, 0.011, dim("#83C46E"), dim("#5FA552"));
  }

  function drawGrass(t, cam, k) {
    const night = nightOf(k), par = 1.3;
    ctx.fillStyle = css(mix(rgb("#4E8F45"), [20, 25, 50], night * 0.6));
    const from = Math.floor((cam.x - W / par) / 26), to = Math.ceil((cam.x + W / par) / 26);
    for (let c = from; c <= to; c++) {
      const wx = c * 26;
      const x = (wx - cam.x) * par + W / 2;
      if (x < -40 || x > W + 40) continue;
      const h = 40 + hash(c) * 60, lean = Math.sin(t * 1.2 + c * 0.7) * 6 + (hash(c + 1) - 0.5) * 16;
      const base = H + 8 - cam.y * par;
      ctx.beginPath();
      ctx.moveTo(x - 7, base);
      ctx.quadraticCurveTo(x + lean * 0.4, base - h * 0.6, x + lean, base - h);
      ctx.quadraticCurveTo(x + lean * 0.3 + 2, base - h * 0.5, x + 7, base);
      ctx.fill();
    }
  }

  // ---------------------------------------------------------------- tulips
  function petal(w, h) {
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.bezierCurveTo(-w * 0.62, -h * 0.08, -w * 0.56, -h * 0.82, 0, -h);
    ctx.bezierCurveTo(w * 0.56, -h * 0.82, w * 0.62, -h * 0.08, 0, 0);
  }

  /** A big tulip. Returns where its cup opens, for the letter to rise from. */
  function tulip(x, ground, height, cupW, color, open, sway, night) {
    const dim = (c, a = 1) => css(mix(c, [30, 30, 70], night * 0.55), a);
    const top = { x: x + sway, y: ground - height };
    // stem and leaves
    ctx.strokeStyle = dim(rgb("#4F9A4A"));
    ctx.lineWidth = 14;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(x, ground);
    ctx.quadraticCurveTo(x - sway * 0.3, ground - height * 0.5, top.x, top.y);
    ctx.stroke();
    for (const side of [-1, 1]) {
      const lg = ctx.createLinearGradient(x, ground, x + side * 120, ground - 160);
      lg.addColorStop(0, dim(rgb("#4E9A47")));
      lg.addColorStop(1, dim(rgb("#7CC46E")));
      ctx.fillStyle = lg;
      ctx.beginPath();
      ctx.moveTo(x, ground);
      ctx.bezierCurveTo(x + side * 110, ground - 40, x + side * 120, ground - 140, x + side * (40 + sway * 0.2), ground - height * 0.62);
      ctx.bezierCurveTo(x + side * 40, ground - 100, x + side * 20, ground - 30, x, ground);
      ctx.fill();
    }
    // the cup: back petal, then two side petals that open
    const ph = cupW * 1.1, pw = cupW * 0.82, c = rgb(color);
    const angle = 0.08 + 0.42 * open;
    ctx.save();
    ctx.translate(top.x, top.y + 10);
    const grad = (a) => {
      const g = ctx.createLinearGradient(0, -ph, 0, 0);
      g.addColorStop(0, dim(mix(c, [255, 255, 255], 0.28 + a)));
      g.addColorStop(1, dim(mix(c, [0, 0, 0], 0.18)));
      return g;
    };
    ctx.fillStyle = grad(-0.1);
    petal(pw * 0.9, ph * 0.98);
    ctx.fill();
    for (const side of [-1, 1]) {
      ctx.save();
      ctx.rotate(side * angle);
      ctx.translate(side * pw * 0.12, 0);
      ctx.fillStyle = grad(0.05);
      petal(pw, ph);
      ctx.fill();
      ctx.fillStyle = "rgba(255,255,255,0.22)";
      ctx.beginPath();
      ctx.ellipse(-side * pw * 0.12, -ph * 0.62, pw * 0.1, ph * 0.22, side * 0.15, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    ctx.restore();
    return { x: top.x, y: top.y - ph * 0.55 };
  }

  // ---------------------------------------------------------------- clay letters
  // Each letter is modelled once on its own canvas: an extruded body, a rim of
  // light along the top-left edges, a gradient face and a soft gloss. Harakat,
  // where taught, are their own colour: the whole text is drawn in the mark's
  // colour, then the same text over it in a copy of the font whose marks are
  // blank — what stays visible in that colour is the mark. (Stripping the marks
  // from the string instead shifts the letters: the font spaces letters apart
  // to keep marks from colliding, so سَاَلَ is narrower without them.)
  // A word whose harakat differ takes ``mark`` as {fetha, kesra, damme}: each
  // hareke is drawn from a font that holds only that mark, in its own colour.
  const spriteCache = {};
  const HAREKE_FONT = { fetha: '"Marks Fetha"', kesra: '"Marks Kesra"', damme: '"Marks Damme"' };
  function clay(text, { font = "700 400px Scheherazade", color = "#E4572E", mark = null, depth = 16 } = {}) {
    const key = `${text}|${font}|${color}|${JSON.stringify(mark)}|${depth}`;
    if (spriteCache[key]) return spriteCache[key];
    const probe = document.createElement("canvas").getContext("2d");
    probe.font = font;
    probe.direction = /[؀-ۿ]/.test(text) ? "rtl" : "ltr";
    probe.textAlign = "left";
    const m = probe.measureText(text);
    const inkW = Math.ceil(m.actualBoundingBoxLeft + m.actualBoundingBoxRight);
    const inkH = Math.ceil(m.actualBoundingBoxAscent + m.actualBoundingBoxDescent);
    const pad = 28;
    const c = document.createElement("canvas");
    c.width = inkW + pad * 2 + depth;
    c.height = inkH + pad * 2 + depth;
    const g = c.getContext("2d");
    g.font = font;
    g.direction = probe.direction;
    g.textAlign = "left";
    g.textBaseline = "alphabetic";
    const ox = pad + m.actualBoundingBoxLeft, oy = pad + m.actualBoundingBoxAscent;
    const markless = font.replace("Scheherazade", '"Markless Arabic"');
    // [font, colour] per mark pass: one colour over the full text, or one pass per hareke
    const marks = !mark ? [] : typeof mark === "string" ? [[font, mark]]
      : Object.entries(mark).map(([h, col]) => [font.replace("Scheherazade", HAREKE_FONT[h]), col]);
    const fills = (fn) => marks.map(([, col]) => fn(col));
    const paint = (dx, dy, baseFill, markFills) => {
      if (marks.length) {
        marks.forEach(([f], i) => {
          g.font = f;
          g.fillStyle = markFills[i];
          g.fillText(text, ox + dx, oy + dy);
        });
        g.fillStyle = baseFill;
        g.font = markless;
        g.fillText(text, ox + dx, oy + dy);
        g.font = font;
      } else {
        g.fillStyle = baseFill;
        g.fillText(text, ox + dx, oy + dy);
      }
    };
    for (let i = depth; i >= 1; i--) {
      const k = i / depth;
      paint(i * 0.45, i, css(mix(shade(color, -0.42), shade(color, -0.25), 1 - k)),
        fills((col) => css(mix(shade(col, -0.4), shade(col, -0.2), 1 - k))));
    }
    paint(-2.5, -2.5, css(shade(color, 0.6)), fills((col) => css(shade(col, 0.6))));       // rim light
    const face = (hex) => {
      const lg = g.createLinearGradient(0, pad, 0, pad + inkH);
      lg.addColorStop(0, css(shade(hex, 0.3)));
      lg.addColorStop(0.55, css(shade(hex, 0.05)));
      lg.addColorStop(1, css(shade(hex, -0.08)));
      return lg;
    };
    paint(0, 0, face(color), fills(face));
    // gloss, clipped to the face
    const gl = document.createElement("canvas");
    gl.width = c.width;
    gl.height = c.height;
    const gg = gl.getContext("2d");
    gg.font = font;
    gg.direction = probe.direction;
    gg.textAlign = "left";
    gg.fillStyle = "#fff";
    gg.fillText(text, ox, oy);
    gg.globalCompositeOperation = "source-in";
    const rg = gg.createRadialGradient(pad + inkW * 0.3, pad + inkH * 0.18, 0, pad + inkW * 0.3, pad + inkH * 0.18, Math.max(inkW, inkH) * 0.65);
    rg.addColorStop(0, "rgba(255,255,255,0.55)");
    rg.addColorStop(1, "rgba(255,255,255,0)");
    gg.fillStyle = rg;
    gg.fillRect(0, 0, gl.width, gl.height);
    g.drawImage(gl, 0, 0);
    return (spriteCache[key] = { c, ax: pad + inkW / 2, ay: pad + inkH / 2, inkW, inkH });
  }

  function drawSprite(sp, x, y, sx = 1, sy = sx, rot = 0, alpha = 1) {
    if (alpha <= 0.001 || sx <= 0.001) return;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.translate(x, y);
    ctx.rotate(rot);
    ctx.scale(sx, sy);
    ctx.drawImage(sp.c, -sp.ax, -sp.ay);
    ctx.restore();
  }

  function halo(x, y, r, strength, warm = "255,238,196") {
    if (strength <= 0.001) return;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, `rgba(${warm},${0.6 * strength})`);
    g.addColorStop(0.5, `rgba(${warm},${0.22 * strength})`);
    g.addColorStop(1, `rgba(${warm},0)`);
    ctx.fillStyle = g;
    ctx.fillRect(x - r, y - r, 2 * r, 2 * r);
  }

  function beam(x0, y0, x1, y1, w0, w1, a) {
    if (a <= 0.001) return;
    const g = ctx.createLinearGradient(0, y0, 0, y1);
    g.addColorStop(0, `rgba(255,236,190,${a})`);
    g.addColorStop(1, "rgba(255,236,190,0)");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.moveTo(x0 - w0 / 2, y0);
    ctx.lineTo(x0 + w0 / 2, y0);
    ctx.lineTo(x1 + w1 / 2, y1);
    ctx.lineTo(x1 - w1 / 2, y1);
    ctx.closePath();
    ctx.fill();
  }

  // ---------------------------------------------------------------- the bunting
  const BUNT = { x0: 70, x1: W - 70, y: 40, sag: 46 };
  const flagSize = () => Math.min(66, ((BUNT.x1 - BUNT.x0) / Math.max(N, 1)) * 0.86);
  function flagPos(i) {
    const n = Math.max(N, 1), u = (i + 0.5) / n;      // the first letter hangs at the right
    const x = BUNT.x1 - u * (BUNT.x1 - BUNT.x0);
    const y = BUNT.y + 4 * BUNT.sag * u * (1 - u);
    return { x, y };
  }
  function drawBunting(t, alpha) {
    if (alpha <= 0.001 || !N) return;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = "rgba(120,80,50,0.7)";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(BUNT.x0 - 40, BUNT.y - 10);
    ctx.quadraticCurveTo(W / 2, BUNT.y + BUNT.sag * 2, BUNT.x1 + 40, BUNT.y - 10);
    ctx.stroke();
    const fs = flagSize();
    stations.forEach((s, i) => {
      const { x, y } = flagPos(i);
      const got = t >= s.end - 0.05;
      const pop = got ? Math.exp(-5 * (t - s.end)) * Math.sin(14 * (t - s.end)) : 0;
      const swing = Math.sin(t * 1.7 + i * 0.9) * 0.05 + pop * 0.15;
      const col = TULIPS[i % TULIPS.length];
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(swing);
      ctx.scale(1 + 0.18 * pop, 1 + 0.18 * pop);
      ctx.fillStyle = css(shade(col, -0.25));
      ctx.beginPath();
      ctx.moveTo(-fs / 2 + 3, 3);
      ctx.lineTo(fs / 2 + 3, 3);
      ctx.lineTo(3, fs * 1.18 + 3);
      ctx.fill();
      const fg = ctx.createLinearGradient(0, 0, 0, fs * 1.15);
      fg.addColorStop(0, css(shade(col, got ? 0.15 : 0.45)));
      fg.addColorStop(1, css(shade(col, got ? -0.05 : 0.3)));
      ctx.fillStyle = fg;
      ctx.beginPath();
      ctx.moveTo(-fs / 2, 0);
      ctx.lineTo(fs / 2, 0);
      ctx.lineTo(0, fs * 1.15);
      ctx.fill();
      if (got) {
        ctx.fillStyle = "#FFFFFF";
        ctx.textAlign = "center";
        ctx.direction = "rtl";
        let size = Math.round(fs * 0.62);
        ctx.font = `700 ${size}px Scheherazade`;
        const wdt = ctx.measureText(s.base).width;
        if (wdt > fs * 0.78) size = Math.floor((size * fs * 0.78) / wdt);   // a whole word must fit its flag
        ctx.font = `700 ${size}px Scheherazade`;
        ctx.fillText(s.base, 0, fs * 0.62);
      }
      ctx.restore();
    });
    ctx.restore();
  }

  // ---------------------------------------------------------------- stations
  const GROUND = 985;
  /** Where a letter leaving for its flag is, at progress f (an arc up and to the bunting). */
  function flight(x0, y0, i, f, size0) {
    const { x, y } = flagPos(i), fs = flagSize();
    const e = easeInOut(f);
    return { x: lerp(x0, x, e), y: lerp(y0, y + fs * 0.42, e) - 220 * Math.sin(Math.PI * e), s: lerp(1, (fs * 0.62) / size0, e) };
  }

  function letterStation(s, t, cam, night) {
    const sx = stationX(s.idx) - cam.x + W / 2;
    if (sx < -700 || sx > W + 700) return;
    const ground = GROUND - cam.y;
    const open = t < s.start ? 0 : easeOutCubic(prog(t, s.start + 0.45, s.start + 1.05));
    const sway = Math.sin(t * 1.1 + s.idx) * 8;
    const col = TULIPS[s.idx % TULIPS.length];
    const cup = tulip(sx, ground, 175, 150, col, open, sway, night);
    if (t < s.start || t >= s.end) return;

    const sp = clay(s.text, { font: "700 470px Scheherazade", color: s.mark ? CLAY_INK : s.rim, mark: s.mark, depth: 20 });
    const hover = { x: cup.x, y: 360 - cam.y };
    const rise = easeOutBack(prog(t, s.start + 0.7, s.start + 1.3));
    const e = env("letter", t), b = bounce("letter", t, s.start, s.end);
    const fly = prog(t, s.end - 0.75, s.end - 0.05);
    let x = cup.x, y = lerp(cup.y, hover.y, rise) + Math.sin(t * 1.6 + s.idx) * 9 * rise;
    let scale = lerp(0.15, 1, rise);
    beam(cup.x, cup.y, x, y, 70, 300, 0.22 * rise * (1 - fly));
    halo(x, y, 420, (0.7 + 0.3 * e) * rise * (1 - fly));
    if (fly > 0) {
      const p = flight(x, y, s.idx, fly, 470);
      x = p.x; y = p.y; scale = p.s;
    }
    drawSprite(sp, x, y, scale * (1 + 0.1 * b), scale * (1 - 0.12 * b), Math.sin(t * 1.1 + s.idx) * 0.025 * (1 - fly), clamp(rise * 2));
  }

  function tripleStation(s, t, cam, night) {
    const sx0 = stationX(s.idx) - cam.x + W / 2;
    if (sx0 < -1300 || sx0 > W + 1300) return;
    const ground = GROUND - cam.y;
    const offs = [470, 0, -470];                      // fetha on the right, then kesra, then damme
    const open = t < s.start ? 0 : easeOutCubic(prog(t, s.start + 0.45, s.start + 1.05));
    const cups = offs.map((o, k) => tulip(sx0 + o, ground, 150, 120, TULIPS[(s.idx + k) % TULIPS.length], open,
      Math.sin(t * 1.1 + s.idx + k) * 7, night));
    if (t < s.start || t >= s.end) return;
    const beats = [0, 1, 2].map((k) => beatsIn(`form:${k}`, s.start, s.end)[0]);
    const current = [2, 1, 0].find((k) => beats[k] && t >= beats[k].t);
    const settle = t >= s.end - 1.0;
    const fly = prog(t, s.end - 0.75, s.end - 0.05);
    s.forms.forEach((f, k) => {
      const sp = clay(f.text, { font: "700 300px Scheherazade", color: CLAY_INK, mark: f.mark, depth: 14 });
      const rise = easeOutBack(prog(t, s.start + 0.7 + k * 0.12, s.start + 1.3 + k * 0.12));
      const active = current === k && !settle;
      const focus = current === undefined || settle ? 1 : active ? 1 : 0;
      const lift = active ? 34 : 0;
      const e = env(`form:${k}`, t), b = active ? bounce(`form:${k}`, t, s.start, s.end) : 0;
      let x = cups[k].x, y = lerp(cups[k].y, 420 - cam.y - lift, rise) + Math.sin(t * 1.6 + k) * 7 * rise;
      let scale = lerp(0.15, 1, rise) * (active ? 1.12 : focus ? 1 : 0.86);
      const alpha = clamp(rise * 2) * (focus ? 1 : 0.45);
      halo(x, y, 300, (active ? 0.6 + 0.4 * e : 0.25) * rise * (1 - fly));
      if (fly > 0) {
        const p = flight(x, y, s.idx, fly, 300);
        x = p.x; y = p.y; scale = p.s;
      }
      drawSprite(sp, x, y, scale * (1 + 0.1 * b), scale * (1 - 0.12 * b), 0, alpha);
    });
  }

  // reading words: three syllables on three tulips, said one by one; they slide
  // together and become the joined word (the child sees the isolated letters
  // change shape as they connect), which is then read whole
  function buildStation(s, t, cam, night) {
    const sx0 = stationX(s.idx) - cam.x + W / 2;
    if (sx0 < -1300 || sx0 > W + 1300) return;
    const ground = GROUND - cam.y;
    const n = s.syllables.length;
    const gap = n > 3 ? 440 : 470;
    const offs = s.syllables.map((_, k) => ((n - 1) / 2 - k) * gap);   // the first syllable on the right
    const open = t < s.start ? 0 : easeOutCubic(prog(t, s.start + 0.45, s.start + 1.05));
    const cups = offs.map((o, k) => tulip(sx0 + o, ground, 150, 120, TULIPS[(s.idx + k) % TULIPS.length], open,
      Math.sin(t * 1.1 + s.idx + k) * 7, night));
    if (t < s.start || t >= s.end) return;
    const sylBeats = s.syllables.map((_, k) => beatsIn(`syl:${k}`, s.start, s.end)[0]);
    const current = [...sylBeats.keys()].reverse().find((k) => sylBeats[k] && t >= sylBeats[k].t);
    const jb = beatsIn("join", s.start, s.end)[0];
    // first the syllables slide together into the word's places (move), and only
    // once they sit there does the joined word take over (fade): no double image
    const move = jb ? easeInOut(prog(t, jb.t, jb.t + 0.7)) : 0;
    // an instant swap at the flash's peak: never a frame with both drawn
    const jk = jb && t >= jb.t + 0.72 ? 1 : 0;
    // a short warm "pop" as the letters click together hides the swap
    const pop = jb ? Math.max(0, 1 - Math.abs(t - (jb.t + 0.72)) / 0.18) : 0;
    const allSaid = sylBeats[n - 1] && t >= sylBeats[n - 1].t + sylBeats[n - 1].dur;
    const fly = prog(t, s.end - 0.75, s.end - 0.05);

    const word = clay(s.word, { font: "700 340px Scheherazade", color: CLAY_INK, mark: s.mark, depth: 18 });
    // a tall word (a damme over the stem of ط, a kesra under ب) is made smaller to
    // fit between the bunting and the tulips, rather than touch either
    const fit = Math.min(1, 450 / word.inkH);
    const wordX = sx0, wordY = Math.max(330, 205 + (word.inkH * fit) / 2) - cam.y;
    // where each syllable lands inside the word, right to left
    const slot = (k) => wordX + (word.inkW * fit) / 2 - ((k + 0.5) * word.inkW * fit) / n;

    if (jk < 1) {
      s.syllables.forEach((txt, k) => {
        const sp = clay(txt, { font: `700 ${n > 3 ? 270 : 300}px Scheherazade`, color: CLAY_INK, mark: s.mark, depth: 14 });
        const rise = easeOutBack(prog(t, s.start + 0.7 + k * 0.12, s.start + 1.3 + k * 0.12));
        const active = current === k && !allSaid;
        const focus = current === undefined || allSaid || active;
        const e = env(`syl:${k}`, t), b = active ? bounce(`syl:${k}`, t, s.start, s.end) : 0;
        const hx = cups[k].x, hy = lerp(cups[k].y, 430 - cam.y - (active ? 34 : 0), rise) + Math.sin(t * 1.6 + k) * 7 * rise;
        const x = lerp(hx, slot(k), move), y = lerp(hy, wordY, move);
        const scale = lerp(0.15, 1, rise) * (active ? 1.12 : focus ? 1 : 0.86) * lerp(1, 0.75, move);
        if (move === 0) halo(x, y, 280, (active ? 0.6 + 0.4 * e : 0.25) * rise);
        drawSprite(sp, x, y, scale * (1 + 0.1 * b), scale * (1 - 0.12 * b), 0, clamp(rise * 2) * (focus ? 1 : 0.45) * (1 - jk));
      });
    }
    if (pop > 0) halo(wordX, wordY, 420, 1.6 * pop, "255,246,214");
    if (jk > 0) {
      const e = env("word", t), b = bounce("word", t, s.start, s.end);
      let x = wordX, y = wordY + Math.sin(t * 1.5) * 8 * jk, scale = lerp(0.9, 1, jk) * fit;
      halo(x, y, 460, (0.55 + 0.45 * e) * jk * (1 - fly));
      if (fly > 0) {
        const p = flight(x, y, s.idx, fly, 340);
        x = p.x; y = p.y; scale = p.s;
      }
      drawSprite(word, x, y, scale * (1 + 0.08 * b), scale * (1 - 0.1 * b), 0, jk);
    }
  }

  // the letters-in-words lesson: the letter above, three clay plaques below (DOM, so a
  // letter inside a word can be coloured while the browser keeps the word joined)
  const WORD_LABELS = ["Başta", "Ortada", "Sonda"];
  const PLAQUE = { y: 600, w: 500, h: 300, xs: [1300, 710, 120] };
  let overlayHTML = "";
  function fitWord(text, width, max) {
    const p = document.createElement("canvas").getContext("2d");
    p.font = `700 ${max}px Scheherazade`;
    const w = p.measureText(text.replace(/[{}]/g, "")).width;
    return Math.min(max, Math.floor((max * width) / w));
  }
  const fitCache = {};

  /** The word as spans, one per letter, so the letter being said lights up while
   *  Diyanet spells it — a child has to see *which* letter is being read. Only
   *  colour and glow change (no underline: under a letter it reads as a kasra; no
   *  scaling: it would break the joining). The lesson's own letter keeps its colour. */
  const ARABIC = /[ء-ي]/;
  function spelledWord(word, spell, t, rim, rimShade) {
    let li = 0, inTarget = false, out = "";
    for (const ch of word) {
      if (ch === "{") { inTarget = true; continue; }
      if (ch === "}") { inTarget = false; continue; }
      if (!ARABIC.test(ch)) { out += ch; continue; }
      const seg = spell && spell[li];
      li += 1;
      const now = seg && t >= seg[0] && t < seg[1];
      if (now) {
        out += `<span style="color:#FF7A00;text-shadow:0 2px 0 #B04A00,0 4px 0 #B04A00,0 6px 0 #B04A00,0 0 26px rgba(255,170,40,.95),0 0 60px rgba(255,190,80,.8)">${ch}</span>`;
      } else if (inTarget) {
        out += `<span style="color:${rim};text-shadow:0 2px 0 ${rimShade},0 4px 0 ${rimShade},0 6px 0 ${rimShade},0 10px 14px rgba(0,0,0,.18)">${ch}</span>`;
      } else {
        out += `<span>${ch}</span>`;
      }
    }
    return out;
  }
  function wordsStation(s, t, cam, night) {
    const sx = stationX(s.idx) - cam.x + W / 2;
    if (sx < -700 || sx > W + 700) return;
    const ground = GROUND - cam.y;
    const open = t < s.start ? 0 : easeOutCubic(prog(t, s.start + 0.45, s.start + 1.05));
    const cup = tulip(sx, ground, 175, 150, TULIPS[s.idx % TULIPS.length], open, Math.sin(t * 1.1 + s.idx) * 8, night);
    if (t < s.start || t >= s.end) return;
    const sp = clay(s.letter, { color: s.rim, depth: 16 });
    const rise = easeOutBack(prog(t, s.start + 0.7, s.start + 1.3));
    const e = env("iso", t), b = bounce("iso", t, s.start, s.end);
    const fly = prog(t, s.end - 0.75, s.end - 0.05);
    let x = cup.x, y = lerp(cup.y, 290 - cam.y, rise) + Math.sin(t * 1.6) * 7 * rise;
    let scale = lerp(0.12, 0.62, rise);
    halo(x, y, 260, (0.5 + 0.5 * e) * rise * (1 - fly));
    if (fly > 0) {
      const p = flight(x, y, s.idx, fly, 400 * 0.62);
      x = p.x; y = p.y; scale = p.s * 0.62;
    }
    drawSprite(sp, x, y, scale * (1 + 0.1 * b), scale * (1 - 0.12 * b), 0, clamp(rise * 2));

    // plaques
    const wb = [0, 1, 2].map((k) => beatsIn(`word:${k}`, s.start, s.end)[0]);
    const current = [2, 1, 0].find((k) => wb[k] && t >= wb[k].t);
    const settle = t >= s.end - 0.9;
    const shift = sx - W / 2;                         // the plaques ride with the camera glide
    const fade = 1 - prog(t, s.end - 0.5, s.end);
    s.words.forEach((w, k) => {
      const pop = easeOutBack(prog(t, s.start + 1.0 + k * 0.12, s.start + 1.5 + k * 0.12));
      const active = current === k && !settle;
      const dim = current !== undefined && !settle && !active;
      const lift = active ? -18 : 0, sc = pop * (active ? 1.06 : dim ? 0.95 : 1);
      const bob = Math.sin(t * 1.4 + k * 1.3) * 5;
      const glow = active ? ", 0 0 0 14px rgba(255,214,120,.55), 0 0 60px 10px rgba(255,214,120,.45)" : "";
      const size = (fitCache[w] ||= fitWord(w, PLAQUE.w - 70, 150));
      const shadeCol = css(shade(s.rim, -0.45)), inkShade = "rgba(30,36,70,.55)";
      const html = spelledWord(w, s.spell && s.spell[k], t, s.rim, shadeCol);
      overlayHTML += `<div class="plaque" style="left:${PLAQUE.xs[k] + shift}px;top:${PLAQUE.y + lift + bob}px;
        width:${PLAQUE.w}px;height:${PLAQUE.h}px;transform:scale(${sc});opacity:${clamp(pop * 1.4) * fade};
        filter:${dim ? "saturate(.35) brightness(.93) contrast(.8)" : "none"};
        box-shadow:0 10px 0 #E2CDB4, 0 26px 40px rgba(80,50,20,.28), inset 0 3px 0 rgba(255,255,255,.9)${glow}">
        <div class="label">${WORD_LABELS[k]}</div>
        <div class="word" style="font-size:${size}px;color:${CLAY_INK};text-shadow:0 2px 0 ${inkShade},0 4px 0 ${inkShade},0 6px 0 ${inkShade},0 10px 14px rgba(0,0,0,.18)">${html}</div></div>`;
    });
  }

  // ---------------------------------------------------------------- besmele, title, outro
  function cartouche(cx, cy, w, h, alpha) {
    ctx.save();
    ctx.globalAlpha = alpha;
    const r = h / 2;
    const path = (inset) => {
      ctx.beginPath();
      ctx.moveTo(cx - w / 2 + r, cy - h / 2 + inset);
      ctx.lineTo(cx + w / 2 - r, cy - h / 2 + inset);
      ctx.arc(cx + w / 2 - r, cy, r - inset, -Math.PI / 2, Math.PI / 2);
      ctx.lineTo(cx - w / 2 + r, cy + h / 2 - inset);
      ctx.arc(cx - w / 2 + r, cy, r - inset, Math.PI / 2, (3 * Math.PI) / 2);
      ctx.closePath();
    };
    ctx.shadowColor = "rgba(90,60,20,0.35)";
    ctx.shadowBlur = 40;
    ctx.shadowOffsetY = 18;
    path(0);
    const fill = ctx.createLinearGradient(0, cy - h / 2, 0, cy + h / 2);
    fill.addColorStop(0, "rgba(255,252,240,0.94)");
    fill.addColorStop(1, "rgba(250,236,212,0.94)");
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.shadowColor = "transparent";
    const gold = ctx.createLinearGradient(cx - w / 2, 0, cx + w / 2, 0);
    gold.addColorStop(0, "#B8862B");
    gold.addColorStop(0.5, "#F2D27A");
    gold.addColorStop(1, "#B8862B");
    ctx.strokeStyle = gold;
    ctx.lineWidth = 9;
    path(10);
    ctx.stroke();
    ctx.lineWidth = 3;
    path(26);
    ctx.stroke();
    // eight-pointed stars at both ends
    for (const sx of [cx - w / 2 + r * 0.55, cx + w / 2 - r * 0.55]) {
      ctx.save();
      ctx.translate(sx, cy);
      ctx.fillStyle = gold;
      for (let i = 0; i < 2; i++) {
        ctx.rotate(Math.PI / 4);
        ctx.fillRect(-26, -26, 52, 52);
      }
      ctx.fillStyle = "#FFF6DD";
      ctx.beginPath();
      ctx.arc(0, 0, 14, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    ctx.restore();
  }

  const BESMELE = "بِسْمِ اللّٰهِ الرَّحْمٰنِ الرَّحِيمِ";   // plain kasra: U+06EA reads as a dot in this font
  function drawBesmele(sc, t, cam) {
    const k = easeInOut(prog(t, sc.start + 0.2, sc.start + 1.6));
    const y = 470 + (1 - k) * 30;
    cartouche(W / 2, y, 1560, 330, k);
    const sp = clay(BESMELE, { font: "700 122px Scheherazade", color: "#D9A441", depth: 7 });
    drawSprite(sp, W / 2, y - 6, 1, 1, 0, k);
    // a slow band of light across the gold
    const band = prog(t, sc.start + 1.4, sc.start + 4.2);
    if (band > 0 && band < 1) {
      const tmp = shimmerCanvas(sp, band);
      ctx.save();
      ctx.globalAlpha = k;
      ctx.drawImage(tmp, W / 2 - sp.ax, y - 6 - sp.ay);
      ctx.restore();
    }
    ctx.save();
    ctx.globalAlpha = k * 0.9;
    ctx.fillStyle = "#8A6A3A";
    ctx.font = "600 44px Nunito";
    ctx.textAlign = "center";
    ctx.fillText("Bismillâhirrahmânirrahîm", W / 2, y + 225);
    ctx.restore();
  }
  let shimmer = null;
  function shimmerCanvas(sp, f) {
    shimmer ||= document.createElement("canvas");
    shimmer.width = sp.c.width;
    shimmer.height = sp.c.height;
    const g = shimmer.getContext("2d");
    g.clearRect(0, 0, shimmer.width, shimmer.height);
    g.drawImage(sp.c, 0, 0);
    g.globalCompositeOperation = "source-atop";
    const x = lerp(-300, shimmer.width + 300, f);
    const lg = g.createLinearGradient(x - 160, 0, x + 160, 0);
    lg.addColorStop(0, "rgba(255,255,240,0)");
    lg.addColorStop(0.5, "rgba(255,255,240,0.75)");
    lg.addColorStop(1, "rgba(255,255,240,0)");
    g.fillStyle = lg;
    g.fillRect(0, 0, shimmer.width, shimmer.height);
    g.globalCompositeOperation = "source-over";
    return shimmer;
  }

  const TITLE_COLORS = ["#F3722C", "#F6B93B", "#E8505B", "#9D7BD8", "#5FA8D3", "#43AA8B", "#F28AA0"];
  function drawTitle(sc, t, cam) {
    const word = "Elif-Ba";
    const font = "900 230px Nunito";
    const p = document.createElement("canvas").getContext("2d");
    p.font = font;
    const widths = [...word].map((ch) => p.measureText(ch).width);
    const total = widths.reduce((a, b) => a + b, 0);
    let x = W / 2 - total / 2;
    const baseY = 330 - cam.y * 0.0;
    [...word].forEach((ch, i) => {
      const at = sc.start + 0.9 + i * 0.09;
      const drop = prog(t, at, at + 0.55);
      const land = t - (at + 0.55);
      const sq = land > 0 ? Math.exp(-5 * land) * Math.sin(14 * land) : 0;
      const y = lerp(-250, baseY, easeOutCubic(drop)) + Math.sin(t * 1.5 + i * 0.7) * 6 * clamp(land);
      const sp = clay(ch, { font, color: ch === "-" ? "#E4572E" : TITLE_COLORS[i % TITLE_COLORS.length], depth: 14 });
      drawSprite(sp, x + widths[i] / 2, y, 1 + 0.12 * sq, 1 - 0.14 * sq, 0, clamp(drop * 3));
      x += widths[i];
    });
    const sub = clay(sc.subtitle, { font: "800 90px Nunito", color: "#7A5CC8", depth: 8 });
    const k = easeOutBack(prog(t, sc.start + 1.7, sc.start + 2.3));
    drawSprite(sub, W / 2, 545, k, k, 0, clamp(k * 2));
  }

  function lantern(x, y, s, glowK) {
    halo(x, y + 10 * s, 150 * s, 0.9 * glowK, "255,196,110");
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(s, s);
    ctx.strokeStyle = "rgba(120,80,40,0.8)";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(0, -78);
    ctx.lineTo(0, -60);
    ctx.stroke();
    ctx.fillStyle = "#C2873A";
    ctx.beginPath();
    ctx.moveTo(-26, -40);
    ctx.lineTo(0, -64);
    ctx.lineTo(26, -40);
    ctx.fill();
    const body = ctx.createLinearGradient(0, -40, 0, 40);
    body.addColorStop(0, "#FFE9A8");
    body.addColorStop(1, "#FFB347");
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.moveTo(-30, -40);
    ctx.lineTo(30, -40);
    ctx.lineTo(36, 0);
    ctx.lineTo(24, 40);
    ctx.lineTo(-24, 40);
    ctx.lineTo(-36, 0);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = "#B5762C";
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(-12, 40);
    ctx.lineTo(-12, -40);
    ctx.moveTo(12, 40);
    ctx.lineTo(12, -40);
    ctx.stroke();
    ctx.fillStyle = "#C2873A";
    ctx.fillRect(-20, 40, 40, 10);
    ctx.restore();
  }

  function drawOutro(sc, t, cam) {
    // lanterns float up into the night
    const night = nightOf(dayK(t));
    for (let i = 0; i < 11; i++) {
      const at = sc.start + 0.6 + hash(i + 50) * 1.6;
      const f = t - at;
      if (f < 0) continue;
      const x = 120 + hash(i + 60) * (W - 240) + Math.sin(t * 0.9 + i) * 26;
      const y = H + 120 - f * (90 + hash(i + 70) * 60);
      lantern(x, y, 0.75 + hash(i + 80) * 0.5, night);
    }
    const k = easeOutBack(prog(t, sc.start + 1.4, sc.start + 2.1));
    const land = t - (sc.start + 2.1);
    const sq = land > 0 ? Math.exp(-5 * land) * Math.sin(14 * land) : 0;
    const sp = clay("Aferin!", { font: "900 210px Nunito", color: "#FFC145", depth: 16 });
    halo(W / 2, 470, 560, 0.5 * k, "255,214,140");
    drawSprite(sp, W / 2, 470 + Math.sin(t * 1.4) * 8 * clamp(land), k * (1 + 0.1 * sq), k * (1 - 0.12 * sq), 0, clamp(k * 2));
  }

  // ---------------------------------------------------------------- the thumbnail
  // The lesson's name in clay over the garden, and what the lesson teaches:
  // three letters on their tulips, the three harakat, or a letter in its words.
  function drawThumb(sc, t, cam) {
    const head = clay("Elif-Ba", { font: "900 210px Nunito", color: "#F3722C", depth: 16 });
    drawSprite(head, W / 2, 150);
    const sub = clay(sc.heading, { font: "800 104px Nunito", color: "#7A5CC8", depth: 10 });
    drawSprite(sub, W / 2, 318);
    const ground = GROUND - cam.y, xs = [1430, 960, 490];        // right to left
    if (sc.example) {
      // a letter and the three words it sits in, the letter lit in one of them
      const ex = sc.example;
      drawSprite(clay(ex.letter, { color: ex.rim, depth: 16 }), W / 2, 520, 0.55);
      ex.words.forEach((w, k) => {
        const html = spelledWord(w, null, 0, ex.rim, css(shade(ex.rim, -0.45)));
        overlayHTML += `<div class="plaque" style="left:${[1300, 710, 120][k]}px;top:660px;width:500px;height:300px">
          <div class="label">${WORD_LABELS[k]}</div>
          <div class="word" style="font-size:${fitWord(w, 430, 160)}px;color:${CLAY_INK};text-shadow:0 2px 0 rgba(30,36,70,.55),0 4px 0 rgba(30,36,70,.55),0 6px 0 rgba(30,36,70,.55)">${html}</div></div>`;
      });
      return;
    }
    if (sc.build) {
      // the reading lesson: a word, and the syllables it is built from
      halo(W / 2, 520, 420, 0.8);
      const bm = sc.build.mark || sc.mark, n = sc.build.syllables.length;
      // a tall word shrinks and keeps below the title (a damme over و reached the heading)
      const ws = clay(sc.build.word, { font: "700 300px Scheherazade", color: CLAY_INK, mark: bm, depth: 18 });
      const wfit = Math.min(1, 340 / ws.inkH);              // the band between title and syllables
      drawSprite(ws, W / 2, Math.max(530, 395 + (ws.inkH * wfit) / 2), wfit);
      sc.build.syllables.forEach((syl, k) => {
        const cup = tulip(W / 2 + ((n - 1) / 2 - k) * (n > 3 ? 380 : 470), ground + 60, 70, 90, TULIPS[(k + 1) % TULIPS.length], 1, 0, 0);
        drawSprite(clay(syl, { font: "700 170px Scheherazade", color: CLAY_INK, mark: bm, depth: 10 }), cup.x, cup.y - 70);
      });
      return;
    }
    const picks = sc.triples ? sc.triples[1].map((f) => ({ text: f.text, color: CLAY_INK, mark: f.mark }))
      : sc.texts.slice(0, 3).map((x, i) => ({ text: x, color: sc.rims[i], mark: null }));
    picks.forEach((p, k) => {
      const cup = tulip(xs[k], ground + 30, 105, 120, TULIPS[(k + 1) % TULIPS.length], 1, 0, 0);
      halo(cup.x, 560, 300, 0.7);
      drawSprite(clay(p.text, { font: "700 340px Scheherazade", color: p.color, mark: p.mark, depth: 16 }), cup.x, 560);
    });
  }

  // ---------------------------------------------------------------- frame
  function vignette() {
    const g = ctx.createRadialGradient(W / 2, H / 2, H * 0.45, W / 2, H / 2, H * 1.05);
    g.addColorStop(0, "rgba(40,20,50,0)");
    g.addColorStop(1, "rgba(40,20,50,0.32)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
  }

  window.seek = function seek(t) {
    const cam = camera(t), k = dayK(t), night = nightOf(k);
    const sc = sceneAt(t);
    overlayHTML = "";
    drawSky(t, cam, k);
    drawClouds(t, cam, k);
    drawHorizon(t, cam, k);
    // no cypresses on a thumbnail: its letters sit low, right in the tree line,
    // where an upright trunk reads as an alif
    drawHills(t, cam, k, sc.type !== "thumb");
    for (const s of stations) {
      if (s.type === "letter") letterStation(s, t, cam, night);
      else if (s.type === "triple") tripleStation(s, t, cam, night);
      else if (s.type === "build") buildStation(s, t, cam, night);
      else wordsStation(s, t, cam, night);
    }
    drawGrass(t, cam, k);
    if (sc.type === "besmele") drawBesmele(sc, t, cam);
    if (sc.type === "title") drawTitle(sc, t, cam);
    if (sc.type === "outro") drawOutro(sc, t, cam);
    if (sc.type === "thumb") drawThumb(sc, t, cam);
    drawBunting(t, clamp((t - firstStart) / 0.8) * (sc.type === "besmele" || sc.type === "title" ? 0 : 1));
    vignette();
    // fade in from dark at the start, out to night at the end
    const fadeIn = 1 - prog(t, 0, 0.8), fadeOut = prog(t, TL.duration - 1.2, TL.duration);
    const dark = sc.type === "thumb" ? 0 : Math.max(fadeIn, fadeOut);   // a thumbnail is one still: no fades
    if (dark > 0) {
      ctx.fillStyle = `rgba(14,16,40,${dark})`;
      ctx.fillRect(0, 0, W, H);
    }
    overlay.innerHTML = overlayHTML;
  };

  window.__duration = TL.duration;
  window.__ready = document.fonts.ready.then(() =>
    Promise.all([
      document.fonts.load('700 100px "Scheherazade"', "ب"),
      document.fonts.load('700 100px "Markless Arabic"', "ب"),
      document.fonts.load('700 100px "Marks Fetha"', "بَ"),
      document.fonts.load('700 100px "Marks Kesra"', "بِ"),
      document.fonts.load('700 100px "Marks Damme"', "بُ"),
      document.fonts.load('900 100px "Nunito"', "EŞ"),
      document.fonts.load('800 100px "Nunito"', "Hş"),
      document.fonts.load('600 40px "Nunito"', "â"),
    ]));
})();
