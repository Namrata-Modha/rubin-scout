/**
 * CosmicAnimation – looping canvas animation matched to transient classification.
 * Pure HTML5 Canvas + requestAnimationFrame, no external deps.
 */
import { useRef, useEffect } from "react";

const TAU = Math.PI * 2;
const CH = 200;

// ─── utilities ────────────────────────────────────────────────────────────

function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }
function lerp(a, b, t) { return a + (b - a) * clamp(t, 0, 1); }
function easeOut(t) { const c = clamp(t, 0, 1); return 1 - (1 - c) * (1 - c); }
function hf(n) { const x = Math.sin(n + 1) * 43758.5453; return x - Math.floor(x); }
/** Alpha * 1.6, capped at 1. */
function ba(a) { return Math.min(1, a * 1.6); }

function glow(ctx, x, y, r, rgba) {
  if (r <= 0) return;
  const g = ctx.createRadialGradient(x, y, 0, x, y, r * 1.2);
  g.addColorStop(0, rgba);
  g.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = g;
  ctx.beginPath(); ctx.arc(x, y, r * 1.2, 0, TAU); ctx.fill();
}

function caption(ctx, W, text) {
  ctx.save();
  ctx.font = "10px ui-monospace,monospace";
  ctx.fillStyle = "rgba(255,255,255,0.6)";
  ctx.textAlign = "center";
  ctx.fillText(text, W / 2, CH - 14);
  ctx.restore();
}

// ─── Change 2: caption text per classification ────────────────────────────

function getCaptionText(cls) {
  switch (cls) {
    case "SNIa":             return "Watch a white dwarf steal matter until it explodes";
    case "SNII":             return "Watch what happens when a massive star runs out of fuel";
    case "SNIbc":            return "A stripped stellar core collapsing and exploding";
    case "SLSN":             return "An explosion 100 times brighter than a normal supernova";
    case "TDE":              return "Watch a star get torn apart by a black hole";
    case "KN":               return "Watch two neutron stars spiral together and collide";
    case "AGN":              return "Matter spiraling into a supermassive black hole";
    case "QSO":              return "Matter spiraling into a supermassive black hole";
    case "Nucleus":          return "The intensely bright core of an active galaxy";
    case "CV/Nova":          return "Two stars orbit each other, one feeding off the other";
    case "Blazar":           return "A jet of energy aimed directly at Earth";
    case "FRB":              return "A millisecond pulse crossing billions of light years";
    case "Black Hole Event": return "Matter and energy swirling around a black hole";
    default:                 return "Something new. We don’t know what it is yet.";
  }
}

// ─── Change 3: atmosphere background colors ───────────────────────────────

const BG_COLORS = {
  SNIa:              "rgba(80,45,0,1)",
  SNII:              "rgba(70,20,0,1)",
  SNIbc:             "rgba(0,15,60,1)",
  SLSN:              "rgba(15,25,70,1)",
  TDE:               "rgba(60,18,0,1)",
  KN:                "rgba(30,20,55,1)",
  AGN:               "rgba(15,5,55,1)",
  QSO:               "rgba(15,5,55,1)",
  Nucleus:           "rgba(15,5,55,1)",
  "CV/Nova":         "rgba(55,15,5,1)",
  Blazar:            "rgba(0,12,50,1)",
  FRB:               "rgba(0,25,30,1)",
  "Black Hole Event":"rgba(30,0,0,1)",
};
const DEFAULT_BG = "rgba(5,5,20,1)";

// ─── SNIa ─────────────────────────────────────────────────────────────────
function drawSNIa(ctx, W, t) {
  const ph = t % 4;
  const cx = W / 2, cy = CH / 2;

  if (ph < 3) {
    glow(ctx, cx + 70, cy, 20, "rgba(110,165,255,0.85)");
    for (let i = 0; i < 50; i++) {
      const frac = ((i / 50) + (ph / 3)) % 1;
      const angle = frac * TAU * 5 + t * 1.3;
      const r = 74 * (1 - frac) + 3;
      ctx.beginPath();
      ctx.arc(cx + Math.cos(angle) * r, cy + Math.sin(angle) * r * 0.45, 2.3, 0, TAU);
      ctx.fillStyle = `rgba(110,165,255,${ba(0.2 + frac * 0.75).toFixed(2)})`;
      ctx.fill();
    }
    glow(ctx, cx, cy, 10 + ph * 2.5, `rgba(255,255,220,${Math.min(1, 0.70 + ph * 0.1).toFixed(2)})`);
  } else if (ph < 3.5) {
    const p = (ph - 3) / 0.5;
    if (p < 0.28) {
      ctx.fillStyle = `rgba(255,255,200,${((1 - p / 0.28) * 0.95).toFixed(2)})`;
      ctx.fillRect(0, 0, W, CH);
    }
    const sR = easeOut(p) * Math.min(W, CH) * 0.52;
    const rg = ctx.createRadialGradient(cx, cy, sR * 0.55, cx, cy, sR);
    rg.addColorStop(0, "rgba(255,210,60,0)");
    rg.addColorStop(0.4, `rgba(255,175,40,${Math.min(1, (1 - p) * 1.6).toFixed(2)})`);
    rg.addColorStop(0.8, `rgba(255,80,12,${Math.min(1, (1 - p) * 1.1).toFixed(2)})`);
    rg.addColorStop(1, "rgba(255,40,5,0)");
    ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(cx, cy, sR, 0, TAU); ctx.fill();
    glow(ctx, cx, cy, 38 * (1 - p * 0.4), `rgba(255,255,210,${Math.min(1, 1 - p * 0.5).toFixed(2)})`);
  } else {
    const p = (ph - 3.5) / 0.5;
    const sR = Math.min(W, CH) * 0.52 + p * 30;
    const rg = ctx.createRadialGradient(cx, cy, sR * 0.8, cx, cy, sR);
    rg.addColorStop(0, "rgba(255,120,20,0)");
    rg.addColorStop(0.5, `rgba(255,100,20,${((1 - p) * 0.56).toFixed(2)})`);
    rg.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(cx, cy, sR, 0, TAU); ctx.fill();
  }
}

// ─── SNII ─────────────────────────────────────────────────────────────────
function drawSNII(ctx, W, t) {
  const ph = t % 5;
  const cx = W / 2, cy = CH / 2;

  if (ph < 4) {
    const sR = 23 + Math.sin(ph * Math.PI * 1.8) * 5;
    glow(ctx, cx, cy, sR * 3.2, "rgba(140,185,255,0.35)");
    glow(ctx, cx, cy, sR, "rgba(200,220,255,1.0)");
  } else if (ph < 4.4) {
    const p = (ph - 4) / 0.4;
    const sR = 23 * (1 - easeOut(p));
    glow(ctx, cx, cy, Math.max(1, sR), "rgba(200,220,255,1.0)");
  } else {
    const p = (ph - 4.4) / 0.6;
    // Wide, vivid shell
    const shellR = easeOut(p) * W * 0.55;
    const rg = ctx.createRadialGradient(cx, cy, shellR * 0.5, cx, cy, shellR);
    rg.addColorStop(0, "rgba(255,140,25,0)");
    rg.addColorStop(0.25, `rgba(255,110,35,${Math.min(1, (1 - p) * 1.6).toFixed(2)})`);
    rg.addColorStop(0.60, `rgba(230,65,12,${Math.min(1, (1 - p) * 1.4).toFixed(2)})`);
    rg.addColorStop(0.85, `rgba(180,30,5,${Math.min(1, (1 - p) * 0.9).toFixed(2)})`);
    rg.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(cx, cy, shellR, 0, TAU); ctx.fill();
    glow(ctx, cx, cy, 10, `rgba(180,215,255,${(1 - p * 0.5).toFixed(2)})`);
  }
}

// ─── SNIbc ────────────────────────────────────────────────────────────────
function drawSNIbc(ctx, W, t) {
  const ph = t % 3.5;
  const cx = W / 2, cy = CH / 2;

  if (ph < 2) {
    const sR = 16 + Math.sin(ph * Math.PI * 2.5) * 4;
    glow(ctx, cx, cy, sR * 2.4, "rgba(60,100,255,0.45)");
    glow(ctx, cx, cy, sR, "rgba(100,148,255,1.0)");
  } else if (ph < 2.22) {
    const p = (ph - 2) / 0.22;
    glow(ctx, cx, cy, Math.max(0.5, 16 * (1 - easeOut(p))), "rgba(100,148,255,1.0)");
  } else {
    const p = (ph - 2.22) / 1.28;
    const ja = 1 - p;
    for (let i = 0; i < 36; i++) {
      const a = (i / 36) * TAU;
      const dR = easeOut(p) * W * 0.24;
      ctx.beginPath();
      ctx.arc(cx + Math.cos(a) * dR, cy + Math.sin(a) * dR * 0.28, 2.0, 0, TAU);
      ctx.fillStyle = `rgba(0,195,255,${ba(ja * 0.7).toFixed(2)})`; ctx.fill();
    }
    const jetLen = easeOut(p) * CH * 0.44;
    for (let i = 0; i < 20; i++) {
      const frac = i / 20;
      const spread = (hf(i * 3) - 0.5) * 9;
      [1, -1].forEach(dir => {
        ctx.beginPath();
        ctx.arc(cx + spread * frac, cy + dir * frac * jetLen, 2.6, 0, TAU);
        ctx.fillStyle = `rgba(0,228,255,${ba(ja * (1 - frac * 0.4) * 0.9).toFixed(2)})`; ctx.fill();
      });
    }
    glow(ctx, cx, cy, 9, `rgba(80,165,255,${(1 - p * 0.55).toFixed(2)})`);
  }
}

// ─── SLSN ─────────────────────────────────────────────────────────────────
function drawSLSN(ctx, W, t) {
  const ph = t % 5;
  const cx = W / 2, cy = CH / 2;

  if (ph < 3) {
    const frac = ph / 3;
    glow(ctx, cx, cy, 70 + frac * 30, `rgba(195,218,255,${ba(frac * 0.35).toFixed(2)})`);
    glow(ctx, cx, cy, 13 + frac * 7, `rgba(255,255,255,${Math.min(1, 0.80 + frac * 0.2).toFixed(2)})`);
  } else if (ph < 3.55) {
    const p = (ph - 3) / 0.55;
    ctx.fillStyle = `rgba(255,255,255,${(1 - p * 0.5).toFixed(2)})`; ctx.fillRect(0, 0, W, CH);
    glow(ctx, cx, cy, easeOut(p) * W * 0.55, `rgba(180,215,255,${(1 - p).toFixed(2)})`);
  } else {
    const p = (ph - 3.55) / 1.45;
    const sR = W * 0.55 + p * 40;
    const rg = ctx.createRadialGradient(cx, cy, sR * 0.78, cx, cy, sR);
    rg.addColorStop(0, "rgba(220,235,255,0)");
    rg.addColorStop(0.5, `rgba(200,225,255,${((1 - p) * 0.72).toFixed(2)})`);
    rg.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(cx, cy, sR, 0, TAU); ctx.fill();
    caption(ctx, W, "100× brighter than a normal supernova");
  }
}

// ─── TDE ──────────────────────────────────────────────────────────────────
function makeTDEAnim() {
  return function drawTDE(ctx, W, t) {
    const cx = W / 2, cy = CH / 2;
    ctx.beginPath(); ctx.arc(cx, cy, 18, 0, TAU); ctx.fillStyle = "#000"; ctx.fill();
    ctx.beginPath(); ctx.arc(cx, cy, 18, 0, TAU);
    ctx.strokeStyle = "rgba(255,140,20,0.80)"; ctx.lineWidth = 2.5; ctx.stroke();
    glow(ctx, cx, cy, 30, "rgba(255,100,18,0.42)");

    const sX = cx + W * 0.35, sY = cy - 22;
    glow(ctx, sX, sY, 15, "rgba(200,228,255,0.82)");

    for (let i = 0; i < 80; i++) {
      const frac = ((i / 80) + t * 0.055) % 1;
      let px, py, heat, alpha;
      if (frac < 0.5) {
        const f = frac / 0.5;
        const ang = -0.18 * Math.PI + f * 0.58 * Math.PI;
        const r = lerp(W * 0.35, 20, easeOut(f));
        px = cx + Math.cos(ang) * r; py = cy + Math.sin(ang) * r * 0.44 - 22 * (1 - f);
        heat = f; alpha = ba(0.35 + f * 0.55);
      } else {
        const f = (frac - 0.5) / 0.5;
        const ang = 0.42 * Math.PI + f * 0.52 * Math.PI;
        const r = 20 + f * W * 0.3;
        px = cx + Math.cos(ang) * r; py = cy + Math.sin(ang) * r * 0.4;
        heat = 1 - f; alpha = ba((1 - f) * 0.58);
      }
      const rC = Math.floor(lerp(200, 255, heat));
      const gC = Math.floor(lerp(80, 170, heat));
      ctx.beginPath(); ctx.arc(px, py, (1.5 + heat * 0.8) * 1.3, 0, TAU);
      ctx.fillStyle = `rgba(${rC},${gC},20,${Math.min(1, alpha).toFixed(2)})`; ctx.fill();
    }
  };
}

// ─── Kilonova ─────────────────────────────────────────────────────────────
function makeKNAnim() {
  const N = 64;
  const angles = Array.from({ length: N }, (_, i) => (i / N) * TAU);
  const speeds  = Array.from({ length: N }, (_, i) => 0.55 + hf(i * 7) * 0.85);
  const isGold  = Array.from({ length: N }, (_, i) => i % 3 !== 0);

  return function drawKN(ctx, W, t) {
    const ph = t % 4.5;
    const cx = W / 2, cy = CH / 2;

    if (ph < 3) {
      const frac = ph / 3;
      const orbitR = lerp(58, 3, easeOut(frac));
      const omega  = 1 + frac * 5;
      const theta  = t * omega;
      const x1 = cx + Math.cos(theta) * orbitR, y1 = cy + Math.sin(theta) * orbitR;
      const x2 = cx - Math.cos(theta) * orbitR, y2 = cy - Math.sin(theta) * orbitR;
      for (let i = 0; i < 3; i++) {
        const rPh = (frac * 3 + i * 0.6) % 1.8;
        ctx.beginPath(); ctx.arc(cx, cy, rPh * W * 0.38, 0, TAU);
        ctx.strokeStyle = `rgba(100,140,255,${((1 - rPh / 1.8) * 0.2).toFixed(2)})`;
        ctx.lineWidth = 1; ctx.stroke();
      }
      glow(ctx, x1, y1, 13, "rgba(145,190,255,1.0)");
      glow(ctx, x2, y2, 13, "rgba(145,190,255,1.0)");
    } else if (ph < 3.5) {
      const p = (ph - 3) / 0.5;
      if (p < 0.28) {
        ctx.fillStyle = `rgba(255,240,178,${((1 - p / 0.28) * 0.95).toFixed(2)})`;
        ctx.fillRect(0, 0, W, CH);
      }
      for (let i = 0; i < N; i++) {
        const r = easeOut(p) * speeds[i] * W * 0.38;
        // Minimum alpha 0.4 during burst
        const a = Math.max(0.4, Math.min(1, (1 - p * 0.6) * 1.6));
        ctx.beginPath();
        ctx.arc(cx + Math.cos(angles[i]) * r, cy + Math.sin(angles[i]) * r, 2.6, 0, TAU);
        ctx.fillStyle = isGold[i]
          ? `rgba(255,212,50,${a.toFixed(2)})`
          : `rgba(222,202,182,${(a * 0.88).toFixed(2)})`;
        ctx.fill();
      }
      glow(ctx, cx, cy, 22 + p * 12, `rgba(255,222,98,${(1 - p * 0.45).toFixed(2)})`);
    } else {
      const p = (ph - 3.5) / 1;
      for (let i = 0; i < N; i++) {
        const r = speeds[i] * W * 0.38 + p * 25;
        const a = Math.max(0, (1 - p) * 0.88);
        ctx.beginPath();
        ctx.arc(cx + Math.cos(angles[i]) * r, cy + Math.sin(angles[i]) * r, 2.0, 0, TAU);
        ctx.fillStyle = `rgba(255,204,50,${a.toFixed(2)})`; ctx.fill();
      }
      caption(ctx, W, "Where gold and platinum come from");
    }
  };
}

// ─── AGN / QSO / Nucleus / Black Hole Event ───────────────────────────────
function makeAGNAnim() {
  const N = 120;
  const disk = Array.from({ length: N }, (_, i) => ({
    base: (i / N) * TAU,
    spd:  0.28 + hf(i * 3) * 0.38,
    r:    26 + hf(i * 5) * 24,
    a:    0.55 + hf(i * 7) * 0.45,  // boosted base alpha
  }));
  const jetN = 22;
  const jetDat = Array.from({ length: jetN }, (_, i) => ({
    spread: (hf(i * 11) - 0.5) * 8,
    a:      0.70 + hf(i * 17) * 0.30, // jets clearly visible
  }));

  return function drawAGN(ctx, W, t) {
    const cx = W / 2, cy = CH / 2;

    for (const d of disk) {
      const ang = d.base + t * d.spd;
      const x = cx + Math.cos(ang) * d.r;
      const y = cy + Math.sin(ang) * d.r * 0.28;
      const heat = clamp((Math.abs(Math.cos(ang)) - 0.1) / 0.9, 0, 1);
      ctx.beginPath(); ctx.arc(x, y, 2.1, 0, TAU);
      ctx.fillStyle = `rgba(${Math.floor(lerp(170, 255, heat))},${Math.floor(lerp(100, 200, heat))},255,${Math.min(1, d.a).toFixed(2)})`;
      ctx.fill();
    }
    glow(ctx, cx, cy, 28, "rgba(228,235,255,1.0)");
    glow(ctx, cx, cy, 58, "rgba(158,182,255,0.52)");

    const pulse = 0.60 + 0.40 * Math.sin(t * 3.4);
    for (let i = 0; i < jetN; i++) {
      const frac = i / jetN;
      const jLen = frac * CH * 0.46;
      // Jets are clearly visible
      const ja = Math.min(1, (1 - frac * 0.55) * pulse * jetDat[i].a * 1.8);
      [1, -1].forEach(dir => {
        ctx.beginPath();
        ctx.arc(cx + jetDat[i].spread * frac, cy + dir * jLen, (2 - frac * 0.8) * 1.3, 0, TAU);
        ctx.fillStyle = `rgba(180,225,255,${ja.toFixed(2)})`; ctx.fill();
      });
    }
  };
}

// ─── CV / Nova ────────────────────────────────────────────────────────────
function makeCVAnim() {
  return function drawCV(ctx, W, t) {
    const cx = W / 2, cy = CH / 2;
    const theta = t * 0.75;
    const ph = t % 5;

    const gX = cx + Math.cos(theta) * 38, gY = cy + Math.sin(theta) * 38 * 0.5;
    glow(ctx, gX, gY, 22, "rgba(255,100,30,0.92)");

    const dX = cx - Math.cos(theta) * 22, dY = cy - Math.sin(theta) * 22 * 0.5;

    for (let i = 0; i < 32; i++) {
      const s = (i / 32 + t * 0.28) % 1;
      const mid = Math.sin(s * Math.PI) * 10;
      ctx.beginPath();
      ctx.arc(
        lerp(gX, dX, s) + mid * Math.sin(theta),
        lerp(gY, dY, s) - mid * Math.cos(theta),
        2.1, 0, TAU
      );
      ctx.fillStyle = `rgba(255,158,78,${ba(0.25 + s * 0.5).toFixed(2)})`; ctx.fill();
    }

    const novaA = ph < 0.6 ? easeOut(1 - ph / 0.6) : 0;
    if (novaA > 0.05) glow(ctx, dX, dY, novaA * 60, `rgba(255,225,152,${Math.min(1, novaA * 0.70).toFixed(2)})`);
    glow(ctx, dX, dY, 7 + novaA * 30, `rgba(255,255,238,${Math.min(1, 0.90 + novaA * 0.10).toFixed(2)})`);
  };
}

// ─── Blazar ───────────────────────────────────────────────────────────────
function makeBlazarAnim() {
  const N = 55;
  const streaks = Array.from({ length: N }, (_, i) => ({
    angle: (i / N) * TAU,
    freq:  1.4 + hf(i * 13) * 2.2,
    phase: hf(i * 17) * TAU,
    len:   0.45 + hf(i * 19) * 0.5,
    base:  0.28 + hf(i * 23) * 0.35, // boosted base
  }));

  return function drawBlazar(ctx, W, t) {
    const cx = W / 2, cy = CH / 2;
    const maxR = Math.max(W, CH) * 0.72;
    const globalPulse = 0.58 + 0.42 * Math.sin(t * 2.6);

    for (const s of streaks) {
      const brightness = Math.max(0, Math.sin(t * s.freq + s.phase));
      // alpha +0.2 over original, lineWidth 2.5
      const a = Math.min(1, (s.base + brightness * 0.65 + 0.2) * globalPulse);
      if (a < 0.05) continue;
      const r = s.len * maxR;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(s.angle) * 9, cy + Math.sin(s.angle) * 9);
      ctx.lineTo(cx + Math.cos(s.angle) * r, cy + Math.sin(s.angle) * r);
      ctx.strokeStyle = `rgba(35,172,255,${a.toFixed(2)})`;
      ctx.lineWidth = 2.5; ctx.stroke();
    }
    glow(ctx, cx, cy, 22 * globalPulse, `rgba(255,255,255,${Math.min(1, 0.95 * globalPulse).toFixed(2)})`);
    glow(ctx, cx, cy, 58 * globalPulse, `rgba(55,168,255,${(0.58 * globalPulse).toFixed(2)})`);
  };
}

// ─── FRB ──────────────────────────────────────────────────────────────────
function makeFRBAnim() {
  const stars = Array.from({ length: 85 }, (_, i) => ({
    x: hf(i * 7), y: hf(i * 11), r: 0.6 + hf(i * 13) * 1.0, a: 0.12 + hf(i * 17) * 0.18,
  }));

  return function drawFRB(ctx, W, t) {
    const cx = W / 2, cy = CH / 2;
    for (const s of stars) {
      ctx.beginPath(); ctx.arc(s.x * W, s.y * CH, s.r, 0, TAU);
      ctx.fillStyle = `rgba(255,255,255,${s.a})`; ctx.fill();
    }
    const maxR = Math.min(W, CH) * 0.47;
    // Rings: 3px stroke, full cyan
    for (let i = 0; i < 3; i++) {
      const ph = (t + i * 1.5) % 4.5;
      if (ph >= 3) continue;
      const p = ph / 3;
      ctx.beginPath(); ctx.arc(cx, cy, p * maxR, 0, TAU);
      ctx.strokeStyle = `rgba(0,255,255,${((1 - p) * 0.92).toFixed(2)})`;
      ctx.lineWidth = 3 * (1 - p * 0.5); ctx.stroke();
    }
    glow(ctx, cx, cy, 6, "rgba(180,255,255,1.0)");
  };
}

// ─── Default / unclassified ────────────────────────────────────────────────
function makeDefaultAnim() {
  const pts = Array.from({ length: 58 }, (_, i) => ({
    x: hf(i * 7), y: hf(i * 11),
    vx: (hf(i * 13) - 0.5) * 0.014,
    vy: (hf(i * 17) - 0.5) * 0.014,
    r:  1.0 + hf(i * 31) * 1.4,
    ba: 0.08 + hf(i * 19) * 0.12,
    ff: 0.28 + hf(i * 23) * 0.65,
    fp: hf(i * 29) * TAU,
  }));

  return function drawDefault(ctx, W, t) {
    for (const p of pts) {
      const px = ((p.x + p.vx * t) % 1 + 1) % 1;
      const py = ((p.y + p.vy * t) % 1 + 1) % 1;
      const flicker = Math.sin(t * p.ff + p.fp);
      const extra = flicker > 0.86 ? (flicker - 0.86) / 0.14 : 0;
      ctx.beginPath(); ctx.arc(px * W, py * CH, p.r + extra * 2.0, 0, TAU);
      ctx.fillStyle = `rgba(255,255,255,${Math.min(1, p.ba + extra * 0.75).toFixed(2)})`; ctx.fill();
    }
  };
}

// ─── Change 1: registry with new cases ────────────────────────────────────

function getAnimFn(cls) {
  switch (cls) {
    case "SNIa":             return drawSNIa;
    case "SNII":             return drawSNII;
    case "SNIbc":            return drawSNIbc;
    case "SLSN":             return drawSLSN;
    case "TDE":              return makeTDEAnim();
    case "KN":               return makeKNAnim();
    case "AGN":
    case "QSO":
    case "Nucleus":
    case "Black Hole Event": return makeAGNAnim();
    case "CV/Nova":          return makeCVAnim();
    case "Blazar":           return makeBlazarAnim();
    case "FRB":              return makeFRBAnim();
    default:                 return makeDefaultAnim();
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
      canvas.width  = canvas.offsetWidth || 640;
      canvas.height = CH;
    };
    setSize();
    const ro = new ResizeObserver(setSize);
    ro.observe(canvas.parentElement || canvas);

    const animFn   = getAnimFn(classification);
    const bgColor  = BG_COLORS[classification] || DEFAULT_BG;
    let rafId, startTs = null;

    const loop = (ts) => {
      if (!startTs) startTs = ts;
      const t  = (ts - startTs) / 1000;
      const W  = canvas.width;
      const cx = W / 2, cy = CH / 2;

      // Change 3: atmosphere background drawn here, before each animation tick
      const bg = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(W, CH) * 0.7);
      bg.addColorStop(0, bgColor);
      bg.addColorStop(1, "#000000");
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, W, CH);

      animFn(ctx, W, t);
      rafId = requestAnimationFrame(loop);
    };
    rafId = requestAnimationFrame(loop);

    return () => { cancelAnimationFrame(rafId); ro.disconnect(); };
  }, [classification]);

  return (
    <div>
      {/* Change 2: caption text above the canvas */}
      <p className="text-xs text-white/30 italic mb-2">
        {getCaptionText(classification)}
      </p>
      <div className="relative overflow-hidden rounded-xl" style={{ height: CH }}>
        <canvas
          ref={canvasRef}
          style={{ width: "100%", height: CH, display: "block" }}
        />
        {/* Change 4: fixed fade overlay */}
        <div
          style={{
            position: "absolute", bottom: 0, left: 0, right: 0, height: 72,
            background: "linear-gradient(to bottom, transparent 0%, rgba(0,0,0,0.85) 100%)",
            pointerEvents: "none",
          }}
        />
      </div>
    </div>
  );
}
