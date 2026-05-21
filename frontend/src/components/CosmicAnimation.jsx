/**
 * CosmicAnimation – looping canvas animation matched to transient classification.
 *
 * Pure HTML5 Canvas + requestAnimationFrame, no external deps.
 * Each animation is a factory that returns a stateful tick(ctx, W, t) function
 * where t is elapsed seconds.  The canvas is cleared and redrawn every frame.
 */
import { useRef, useEffect } from "react";

const TAU = Math.PI * 2;
const CH = 200; // canvas height in px

// ─── tiny utilities ────────────────────────────────────────────────────────

function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }
function lerp(a, b, t) { return a + (b - a) * clamp(t, 0, 1); }
function easeOut(t) { const c = clamp(t, 0, 1); return 1 - (1 - c) * (1 - c); }

/** Deterministic pseudo-random from an integer seed (avoids Math.random in draw path). */
function hf(n) { const x = Math.sin(n + 1) * 43758.5453; return x - Math.floor(x); }

/** Radial gradient glow centred at (x,y) with outer radius r. */
function glow(ctx, x, y, r, rgba) {
  if (r <= 0) return;
  const g = ctx.createRadialGradient(x, y, 0, x, y, r);
  g.addColorStop(0, rgba);
  g.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, TAU);
  ctx.fill();
}

/** Small dim caption at bottom of canvas. */
function caption(ctx, W, text) {
  ctx.save();
  ctx.font = "10px ui-monospace,monospace";
  ctx.fillStyle = "rgba(255,255,255,0.17)";
  ctx.textAlign = "center";
  ctx.fillText(text, W / 2, CH - 14);
  ctx.restore();
}

// ─── SNIa ─────────────────────────────────────────────────────────────────
// White dwarf accretes from blue companion → detonation shockwave.  Cycle 4 s.
function drawSNIa(ctx, W, t) {
  const ph = t % 4;
  const cx = W / 2, cy = CH / 2;
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, CH);

  if (ph < 3) {
    // Blue companion star
    glow(ctx, cx + 70, cy, 20, "rgba(110,165,255,0.65)");
    // Accreting particles on inward spiral
    for (let i = 0; i < 50; i++) {
      const frac = ((i / 50) + (ph / 3)) % 1;
      const angle = frac * TAU * 5 + t * 1.3;
      const r = 74 * (1 - frac) + 3;
      ctx.beginPath();
      ctx.arc(cx + Math.cos(angle) * r, cy + Math.sin(angle) * r * 0.45, 1.8, 0, TAU);
      ctx.fillStyle = `rgba(110,165,255,${(0.2 + frac * 0.75).toFixed(2)})`;
      ctx.fill();
    }
    // White dwarf brightens as mass builds
    glow(ctx, cx, cy, 10 + ph * 2.5, `rgba(255,255,220,${(0.55 + ph * 0.1).toFixed(2)})`);
  } else if (ph < 3.5) {
    const p = (ph - 3) / 0.5;
    // White flash
    if (p < 0.28) {
      ctx.fillStyle = `rgba(255,255,200,${((1 - p / 0.28) * 0.8).toFixed(2)})`;
      ctx.fillRect(0, 0, W, CH);
    }
    // Golden-orange shockwave ring
    const sR = easeOut(p) * Math.min(W, CH) * 0.48;
    const rg = ctx.createRadialGradient(cx, cy, sR * 0.62, cx, cy, sR);
    rg.addColorStop(0, "rgba(255,200,45,0)");
    rg.addColorStop(0.45, `rgba(255,170,35,${(1 - p).toFixed(2)})`);
    rg.addColorStop(1, "rgba(255,65,10,0)");
    ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(cx, cy, sR, 0, TAU); ctx.fill();
    glow(ctx, cx, cy, 38 * (1 - p * 0.45), `rgba(255,255,200,${(1 - p * 0.6).toFixed(2)})`);
  } else {
    // Fade
    const p = (ph - 3.5) / 0.5;
    const sR = Math.min(W, CH) * 0.48 + p * 28;
    const rg = ctx.createRadialGradient(cx, cy, sR * 0.82, cx, cy, sR);
    rg.addColorStop(0, "rgba(255,120,20,0)");
    rg.addColorStop(0.5, `rgba(255,95,18,${((1 - p) * 0.35).toFixed(2)})`);
    rg.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(cx, cy, sR, 0, TAU); ctx.fill();
  }
}

// ─── SNII ─────────────────────────────────────────────────────────────────
// Massive star pulses, collapses, explodes in red-orange shell.  Cycle 5 s.
function drawSNII(ctx, W, t) {
  const ph = t % 5;
  const cx = W / 2, cy = CH / 2;
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, CH);

  if (ph < 4) {
    const sR = 23 + Math.sin(ph * Math.PI * 1.8) * 5;
    glow(ctx, cx, cy, sR * 3.2, "rgba(140,185,255,0.18)");
    glow(ctx, cx, cy, sR, "rgba(200,220,255,0.95)");
  } else if (ph < 4.4) {
    const p = (ph - 4) / 0.4;
    const sR = 23 * (1 - easeOut(p));
    glow(ctx, cx, cy, Math.max(1, sR), "rgba(200,220,255,0.95)");
  } else {
    const p = (ph - 4.4) / 0.6;
    const shellR = easeOut(p) * W * 0.47;
    const rg = ctx.createRadialGradient(cx, cy, shellR * 0.65, cx, cy, shellR);
    rg.addColorStop(0, "rgba(255,125,20,0)");
    rg.addColorStop(0.38, `rgba(255,95,28,${(1 - p).toFixed(2)})`);
    rg.addColorStop(0.78, `rgba(200,52,8,${((1 - p) * 0.65).toFixed(2)})`);
    rg.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(cx, cy, shellR, 0, TAU); ctx.fill();
    glow(ctx, cx, cy, 8, `rgba(180,210,255,${(1 - p * 0.45).toFixed(2)})`);
  }
}

// ─── SNIbc ────────────────────────────────────────────────────────────────
// Faster collapse, narrow polar jets instead of spherical shell.  Cycle 3.5 s.
function drawSNIbc(ctx, W, t) {
  const ph = t % 3.5;
  const cx = W / 2, cy = CH / 2;
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, CH);

  if (ph < 2) {
    const sR = 16 + Math.sin(ph * Math.PI * 2.5) * 4;
    glow(ctx, cx, cy, sR * 2.4, "rgba(60,100,255,0.28)");
    glow(ctx, cx, cy, sR, "rgba(100,148,255,0.9)");
  } else if (ph < 2.22) {
    const p = (ph - 2) / 0.22;
    glow(ctx, cx, cy, Math.max(0.5, 16 * (1 - easeOut(p))), "rgba(100,148,255,0.9)");
  } else {
    const p = (ph - 2.22) / 1.28;
    const ja = 1 - p;
    // Compressed equatorial ring
    for (let i = 0; i < 36; i++) {
      const a = (i / 36) * TAU;
      const dR = easeOut(p) * W * 0.22;
      ctx.beginPath();
      ctx.arc(cx + Math.cos(a) * dR, cy + Math.sin(a) * dR * 0.28, 1.5, 0, TAU);
      ctx.fillStyle = `rgba(0,185,255,${(ja * 0.55).toFixed(2)})`; ctx.fill();
    }
    // Polar jets
    const jetLen = easeOut(p) * CH * 0.44;
    for (let i = 0; i < 20; i++) {
      const frac = i / 20;
      const spread = (hf(i * 3) - 0.5) * 9;
      [1, -1].forEach(dir => {
        ctx.beginPath();
        ctx.arc(cx + spread * frac, cy + dir * frac * jetLen, 2, 0, TAU);
        ctx.fillStyle = `rgba(0,220,255,${(ja * (1 - frac * 0.45) * 0.85).toFixed(2)})`; ctx.fill();
      });
    }
    glow(ctx, cx, cy, 9, `rgba(80,160,255,${(1 - p * 0.6).toFixed(2)})`);
  }
}

// ─── SLSN ─────────────────────────────────────────────────────────────────
// Blindingly bright — fills canvas white before expanding blue-white corona.  Cycle 5 s.
function drawSLSN(ctx, W, t) {
  const ph = t % 5;
  const cx = W / 2, cy = CH / 2;
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, CH);

  if (ph < 3) {
    const frac = ph / 3;
    glow(ctx, cx, cy, 70 + frac * 30, `rgba(190,215,255,${(frac * 0.28).toFixed(2)})`);
    glow(ctx, cx, cy, 13 + frac * 7, `rgba(255,255,255,${(0.65 + frac * 0.35).toFixed(2)})`);
  } else if (ph < 3.55) {
    const p = (ph - 3) / 0.55;
    ctx.fillStyle = `rgba(255,255,255,${(1 - p * 0.55).toFixed(2)})`; ctx.fillRect(0, 0, W, CH);
    glow(ctx, cx, cy, easeOut(p) * W * 0.52, `rgba(175,210,255,${(1 - p).toFixed(2)})`);
  } else {
    const p = (ph - 3.55) / 1.45;
    const sR = W * 0.52 + p * 35;
    const rg = ctx.createRadialGradient(cx, cy, sR * 0.8, cx, cy, sR);
    rg.addColorStop(0, "rgba(215,230,255,0)");
    rg.addColorStop(0.5, `rgba(195,220,255,${((1 - p) * 0.45).toFixed(2)})`);
    rg.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(cx, cy, sR, 0, TAU); ctx.fill();
    caption(ctx, W, "100× brighter than a normal supernova");
  }
}

// ─── TDE ──────────────────────────────────────────────────────────────────
// Star shredded by black hole, streaming into an accretion tail.  Continuous.
function makeTDEAnim() {
  return function drawTDE(ctx, W, t) {
    const cx = W / 2, cy = CH / 2;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, CH);

    // Black hole
    ctx.beginPath(); ctx.arc(cx, cy, 18, 0, TAU); ctx.fillStyle = "#000"; ctx.fill();
    ctx.beginPath(); ctx.arc(cx, cy, 18, 0, TAU);
    ctx.strokeStyle = "rgba(255,135,18,0.55)"; ctx.lineWidth = 2; ctx.stroke();
    glow(ctx, cx, cy, 30, "rgba(255,95,15,0.22)");

    // Star (upper-right)
    const sX = cx + W * 0.35, sY = cy - 22;
    glow(ctx, sX, sY, 15, "rgba(195,225,255,0.6)");

    // 80 particles flowing along star→BH→tail path
    for (let i = 0; i < 80; i++) {
      const frac = ((i / 80) + t * 0.055) % 1;
      let px, py, heat, alpha;
      if (frac < 0.5) {
        const f = frac / 0.5;
        const ang = -0.18 * Math.PI + f * 0.58 * Math.PI;
        const r = lerp(W * 0.35, 20, easeOut(f));
        px = cx + Math.cos(ang) * r;
        py = cy + Math.sin(ang) * r * 0.44 - 22 * (1 - f);
        heat = f; alpha = 0.35 + f * 0.55;
      } else {
        const f = (frac - 0.5) / 0.5;
        const ang = 0.42 * Math.PI + f * 0.52 * Math.PI;
        const r = 20 + f * W * 0.3;
        px = cx + Math.cos(ang) * r; py = cy + Math.sin(ang) * r * 0.4;
        heat = 1 - f; alpha = (1 - f) * 0.58;
      }
      const rC = Math.floor(lerp(195, 255, heat));
      const gC = Math.floor(lerp(75, 165, heat));
      ctx.beginPath(); ctx.arc(px, py, 1.5 + heat * 0.8, 0, TAU);
      ctx.fillStyle = `rgba(${rC},${gC},18,${alpha.toFixed(2)})`; ctx.fill();
    }
  };
}

// ─── Kilonova ─────────────────────────────────────────────────────────────
// Two neutron stars spiral inward, merge, gold/platinum burst.  Cycle 4.5 s.
function makeKNAnim() {
  const N = 64;
  const angles  = Array.from({ length: N }, (_, i) => (i / N) * TAU);
  const speeds  = Array.from({ length: N }, (_, i) => 0.55 + hf(i * 7) * 0.85);
  const isGold  = Array.from({ length: N }, (_, i) => i % 3 !== 0);

  return function drawKN(ctx, W, t) {
    const ph = t % 4.5;
    const cx = W / 2, cy = CH / 2;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, CH);

    if (ph < 3) {
      const frac = ph / 3;
      const orbitR = lerp(58, 3, easeOut(frac));
      const omega  = 1 + frac * 5;
      const theta  = t * omega;
      const x1 = cx + Math.cos(theta) * orbitR, y1 = cy + Math.sin(theta) * orbitR;
      const x2 = cx - Math.cos(theta) * orbitR, y2 = cy - Math.sin(theta) * orbitR;
      // GW ripple rings (subtle)
      for (let i = 0; i < 3; i++) {
        const rPh = (frac * 3 + i * 0.6) % 1.8;
        ctx.beginPath(); ctx.arc(cx, cy, rPh * W * 0.38, 0, TAU);
        ctx.strokeStyle = `rgba(100,140,255,${((1 - rPh / 1.8) * 0.1).toFixed(2)})`;
        ctx.lineWidth = 1; ctx.stroke();
      }
      glow(ctx, x1, y1, 13, "rgba(145,188,255,0.92)");
      glow(ctx, x2, y2, 13, "rgba(145,188,255,0.92)");
    } else if (ph < 3.5) {
      const p = (ph - 3) / 0.5;
      if (p < 0.28) {
        ctx.fillStyle = `rgba(255,238,175,${((1 - p / 0.28) * 0.88).toFixed(2)})`;
        ctx.fillRect(0, 0, W, CH);
      }
      for (let i = 0; i < N; i++) {
        const r = easeOut(p) * speeds[i] * W * 0.36;
        const a = (1 - p * 0.7);
        ctx.beginPath();
        ctx.arc(cx + Math.cos(angles[i]) * r, cy + Math.sin(angles[i]) * r, 2, 0, TAU);
        ctx.fillStyle = isGold[i]
          ? `rgba(255,208,48,${a.toFixed(2)})`
          : `rgba(218,198,178,${(a * 0.78).toFixed(2)})`;
        ctx.fill();
      }
      glow(ctx, cx, cy, 22 + p * 12, `rgba(255,218,95,${(1 - p * 0.5).toFixed(2)})`);
    } else {
      const p = (ph - 3.5) / 1;
      for (let i = 0; i < N; i++) {
        const r = speeds[i] * W * 0.36 + p * 22;
        const a = (1 - p) * 0.55;
        ctx.beginPath();
        ctx.arc(cx + Math.cos(angles[i]) * r, cy + Math.sin(angles[i]) * r, 1.5, 0, TAU);
        ctx.fillStyle = `rgba(255,200,48,${a.toFixed(2)})`; ctx.fill();
      }
      caption(ctx, W, "Where gold and platinum come from");
    }
  };
}

// ─── AGN / QSO ────────────────────────────────────────────────────────────
// Accretion disk (elliptical orbits) + bipolar relativistic jets.  Continuous.
function makeAGNAnim() {
  const N = 120;
  const disk = Array.from({ length: N }, (_, i) => ({
    base: (i / N) * TAU,
    spd:  0.28 + hf(i * 3) * 0.38,
    r:    26 + hf(i * 5) * 24,
    a:    0.28 + hf(i * 7) * 0.5,
  }));
  const jetN = 22;
  const jetDat = Array.from({ length: jetN }, (_, i) => ({
    spread: (hf(i * 11) - 0.5) * 8,
    a:      0.35 + hf(i * 17) * 0.45,
  }));

  return function drawAGN(ctx, W, t) {
    const cx = W / 2, cy = CH / 2;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, CH);

    for (const d of disk) {
      const ang = d.base + t * d.spd;
      const x = cx + Math.cos(ang) * d.r;
      const y = cy + Math.sin(ang) * d.r * 0.28;
      const heat = clamp((Math.abs(Math.cos(ang)) - 0.1) / 0.9, 0, 1);
      ctx.beginPath(); ctx.arc(x, y, 1.6, 0, TAU);
      ctx.fillStyle = `rgba(${Math.floor(lerp(170, 255, heat))},${Math.floor(lerp(95, 195, heat))},255,${d.a.toFixed(2)})`;
      ctx.fill();
    }
    glow(ctx, cx, cy, 28, "rgba(225,232,255,0.95)");
    glow(ctx, cx, cy, 58, "rgba(155,178,255,0.32)");

    const pulse = 0.58 + 0.42 * Math.sin(t * 3.4);
    for (let i = 0; i < jetN; i++) {
      const frac = i / jetN;
      const jLen = frac * CH * 0.45;
      const ja = (1 - frac * 0.65) * pulse * jetDat[i].a;
      [1, -1].forEach(dir => {
        ctx.beginPath();
        ctx.arc(cx + jetDat[i].spread * frac, cy + dir * jLen, 2 - frac * 0.8, 0, TAU);
        ctx.fillStyle = `rgba(175,218,255,${ja.toFixed(2)})`; ctx.fill();
      });
    }
  };
}

// ─── CV / Nova ────────────────────────────────────────────────────────────
// Binary orbit; white dwarf accretes; nova flash every 5 s.  Continuous.
function makeCVAnim() {
  return function drawCV(ctx, W, t) {
    const cx = W / 2, cy = CH / 2;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, CH);

    const theta = t * 0.75;
    const ph = t % 5;

    // Red giant
    const gX = cx + Math.cos(theta) * 38, gY = cy + Math.sin(theta) * 38 * 0.5;
    glow(ctx, gX, gY, 22, "rgba(255,95,28,0.72)");

    // White dwarf (opposite)
    const dX = cx - Math.cos(theta) * 22, dY = cy - Math.sin(theta) * 22 * 0.5;

    // Accretion stream
    for (let i = 0; i < 32; i++) {
      const s = (i / 32 + t * 0.28) % 1;
      const mid = Math.sin(s * Math.PI) * 10;
      ctx.beginPath();
      ctx.arc(
        lerp(gX, dX, s) + mid * Math.sin(theta),
        lerp(gY, dY, s) - mid * Math.cos(theta),
        1.6, 0, TAU
      );
      ctx.fillStyle = `rgba(255,155,75,${(0.25 + s * 0.45).toFixed(2)})`; ctx.fill();
    }

    // Nova flash
    const novaA = ph < 0.6 ? easeOut(1 - ph / 0.6) : 0;
    if (novaA > 0.05) glow(ctx, dX, dY, novaA * 55, `rgba(255,220,148,${(novaA * 0.48).toFixed(2)})`);
    glow(ctx, dX, dY, 7 + novaA * 28, `rgba(255,255,235,${(0.82 + novaA * 0.18).toFixed(2)})`);
  };
}

// ─── Blazar ───────────────────────────────────────────────────────────────
// Jet aimed at viewer — radial electric-blue streaks pulse outward.  Continuous.
function makeBlazarAnim() {
  const N = 55;
  const streaks = Array.from({ length: N }, (_, i) => ({
    angle: (i / N) * TAU,
    freq:  1.4 + hf(i * 13) * 2.2,
    phase: hf(i * 17) * TAU,
    len:   0.45 + hf(i * 19) * 0.5,
    base:  0.15 + hf(i * 23) * 0.3,
  }));

  return function drawBlazar(ctx, W, t) {
    const cx = W / 2, cy = CH / 2;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, CH);

    const maxR = Math.max(W, CH) * 0.7;
    const globalPulse = 0.55 + 0.45 * Math.sin(t * 2.6);

    for (const s of streaks) {
      const brightness = Math.max(0, Math.sin(t * s.freq + s.phase));
      const a = (s.base + brightness * 0.55) * globalPulse;
      if (a < 0.04) continue;
      const r = s.len * maxR;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(s.angle) * 9, cy + Math.sin(s.angle) * 9);
      ctx.lineTo(cx + Math.cos(s.angle) * r, cy + Math.sin(s.angle) * r);
      ctx.strokeStyle = `rgba(35,168,255,${a.toFixed(2)})`;
      ctx.lineWidth = 1.5; ctx.stroke();
    }
    glow(ctx, cx, cy, 22 * globalPulse, `rgba(255,255,255,${(0.88 * globalPulse).toFixed(2)})`);
    glow(ctx, cx, cy, 55 * globalPulse, `rgba(55,165,255,${(0.42 * globalPulse).toFixed(2)})`);
  };
}

// ─── FRB ──────────────────────────────────────────────────────────────────
// Pulse rings expanding from a point source.  Continuous (ring every 1.5 s).
function makeFRBAnim() {
  const stars = Array.from({ length: 85 }, (_, i) => ({
    x: hf(i * 7), y: hf(i * 11), r: 0.5 + hf(i * 13) * 0.8, a: 0.08 + hf(i * 17) * 0.13,
  }));

  return function drawFRB(ctx, W, t) {
    const cx = W / 2, cy = CH / 2;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, CH);

    for (const s of stars) {
      ctx.beginPath(); ctx.arc(s.x * W, s.y * CH, s.r, 0, TAU);
      ctx.fillStyle = `rgba(255,255,255,${s.a})`; ctx.fill();
    }

    const maxR = Math.min(W, CH) * 0.47;
    // 3 rings staggered 1.5 s apart; each ring lives for 3 s
    for (let i = 0; i < 3; i++) {
      const ph = (t + i * 1.5) % 4.5;
      if (ph >= 3) continue;
      const p = ph / 3;
      ctx.beginPath(); ctx.arc(cx, cy, p * maxR, 0, TAU);
      ctx.strokeStyle = `rgba(0,238,255,${((1 - p) * 0.85).toFixed(2)})`;
      ctx.lineWidth = Math.max(0.5, 2.5 * (1 - p)); ctx.stroke();
    }
    glow(ctx, cx, cy, 5, "rgba(175,255,255,0.9)");
  };
}

// ─── Default / unclassified ────────────────────────────────────────────────
// Faint drifting particles, occasional flicker — something unknown is out there.
function makeDefaultAnim() {
  const pts = Array.from({ length: 58 }, (_, i) => ({
    x: hf(i * 7), y: hf(i * 11),
    vx: (hf(i * 13) - 0.5) * 0.014,
    vy: (hf(i * 17) - 0.5) * 0.014,
    r:  0.8 + hf(i * 31) * 1.1,
    ba: 0.05 + hf(i * 19) * 0.09,
    ff: 0.28 + hf(i * 23) * 0.65,
    fp: hf(i * 29) * TAU,
  }));

  return function drawDefault(ctx, W, t) {
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, CH);
    for (const p of pts) {
      const px = ((p.x + p.vx * t) % 1 + 1) % 1;
      const py = ((p.y + p.vy * t) % 1 + 1) % 1;
      const flicker = Math.sin(t * p.ff + p.fp);
      const extra = flicker > 0.86 ? (flicker - 0.86) / 0.14 : 0;
      ctx.beginPath(); ctx.arc(px * W, py * CH, p.r + extra * 1.6, 0, TAU);
      ctx.fillStyle = `rgba(255,255,255,${(p.ba + extra * 0.58).toFixed(2)})`; ctx.fill();
    }
  };
}

// ─── registry ─────────────────────────────────────────────────────────────

function getAnimFn(cls) {
  switch (cls) {
    case "SNIa":    return drawSNIa;
    case "SNII":    return drawSNII;
    case "SNIbc":   return drawSNIbc;
    case "SLSN":    return drawSLSN;
    case "TDE":     return makeTDEAnim();
    case "KN":      return makeKNAnim();
    case "AGN":
    case "QSO":     return makeAGNAnim();
    case "CV/Nova": return makeCVAnim();
    case "Blazar":  return makeBlazarAnim();
    case "FRB":     return makeFRBAnim();
    default:        return makeDefaultAnim();
  }
}

// ─── component ────────────────────────────────────────────────────────────

export default function CosmicAnimation({ classification }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");

    const setSize = () => {
      canvas.width  = canvas.offsetWidth  || 640;
      canvas.height = CH;
    };
    setSize();

    const ro = new ResizeObserver(setSize);
    ro.observe(canvas.parentElement || canvas);

    const animFn = getAnimFn(classification);
    let rafId, startTs = null;

    const loop = (ts) => {
      if (!startTs) startTs = ts;
      animFn(ctx, canvas.width, (ts - startTs) / 1000);
      rafId = requestAnimationFrame(loop);
    };
    rafId = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(rafId);
      ro.disconnect();
    };
  }, [classification]);

  return (
    <div className="relative overflow-hidden rounded-xl" style={{ height: CH }}>
      <canvas
        ref={canvasRef}
        style={{ width: "100%", height: CH, display: "block" }}
      />
      {/* Fade bottom edge into page background */}
      <div
        style={{
          position: "absolute", bottom: 0, left: 0, right: 0, height: 72,
          background: "linear-gradient(to bottom, transparent, #0a0f1a)",
          pointerEvents: "none",
        }}
      />
    </div>
  );
}
