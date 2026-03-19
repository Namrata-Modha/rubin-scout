import { useRef, useEffect, useState } from "react";
import { getClassInfo, getConstellation } from "../lib/cosmos";

function mollweideProject(raDeg, decDeg, w, h) {
  let lon = raDeg > 180 ? raDeg - 360 : raDeg;
  lon = -lon;
  const lonRad = (lon * Math.PI) / 180;
  const latRad = (decDeg * Math.PI) / 180;
  let theta = latRad;
  for (let i = 0; i < 10; i++) {
    const d = -(theta + Math.sin(theta) - Math.PI * Math.sin(latRad)) / (1 + Math.cos(theta));
    theta += d;
    if (Math.abs(d) < 1e-6) break;
  }
  theta /= 2;
  const x = ((2 * Math.SQRT2) / Math.PI) * lonRad * Math.cos(theta);
  const y = Math.SQRT2 * Math.sin(theta);
  const s = Math.min(w / (4 * Math.SQRT2), h / (2 * Math.SQRT2));
  return { x: w / 2 + x * s * 0.95, y: h / 2 - y * s * 0.95 };
}

function isInside(px, py, w, h) {
  const s = Math.min(w / (4 * Math.SQRT2), h / (2 * Math.SQRT2));
  const a = 2 * Math.SQRT2 * s * 0.95, b = Math.SQRT2 * s * 0.95;
  return ((px - w/2)**2)/(a**2) + ((py - h/2)**2)/(b**2) <= 1;
}

function rng(seed) {
  let s = seed;
  return () => { s = (s * 16807) % 2147483647; return s / 2147483647; };
}

function drawBackground(ctx, w, h) {
  const rand = rng(42);

  // Deep space gradient inside ellipse
  const cx = w/2, cy = h/2;
  const s = Math.min(w / (4 * Math.SQRT2), h / (2 * Math.SQRT2));
  const a = 2 * Math.SQRT2 * s * 0.95;
  const bg = ctx.createRadialGradient(cx, cy, 0, cx, cy, a);
  bg.addColorStop(0, "#080e1e");
  bg.addColorStop(0.7, "#060b18");
  bg.addColorStop(1, "#040814");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);

  // Stars
  for (let i = 0; i < 3000; i++) {
    const x = rand() * w, y = rand() * h;
    if (!isInside(x, y, w, h)) continue;
    const b = rand();
    const size = b < 0.92 ? 0.3 : b < 0.98 ? 0.6 : 1.0;
    const alpha = 0.05 + b * 0.4;
    // Slight color variation: blue-white, warm-white, cool-blue
    const temp = rand();
    const r = temp < 0.3 ? 180 : temp < 0.6 ? 210 : 200;
    const g = temp < 0.3 ? 195 : temp < 0.6 ? 215 : 200;
    const bl = 255;
    ctx.beginPath();
    ctx.arc(x, y, size, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(${r},${g},${bl},${alpha})`;
    ctx.fill();
  }

  // Bright stars with soft glow
  for (let i = 0; i < 25; i++) {
    const x = rand() * w, y = rand() * h;
    if (!isInside(x, y, w, h)) continue;
    const g = ctx.createRadialGradient(x, y, 0, x, y, 4 + rand() * 3);
    g.addColorStop(0, `rgba(200,220,255,${0.3 + rand()*0.3})`);
    g.addColorStop(0.3, "rgba(180,200,255,0.08)");
    g.addColorStop(1, "transparent");
    ctx.fillStyle = g;
    ctx.fillRect(x - 8, y - 8, 16, 16);
  }
}

function drawMilkyWay(ctx, w, h) {
  // Wide, diffuse galactic band — multiple offset passes for natural width
  const offsets = [-20, -12, -6, 0, 6, 12, 20];
  const alphas = [0.008, 0.012, 0.018, 0.025, 0.018, 0.012, 0.008];

  for (let pass = 0; pass < offsets.length; pass++) {
    for (let ra = 0; ra <= 360; ra += 0.6) {
      // Galactic plane in equatorial coordinates (sinusoidal approximation)
      const baseDec = 28 * Math.sin((ra - 280) * (Math.PI / 180));
      const dec = baseDec + offsets[pass];
      const { x, y } = mollweideProject(ra, dec, w, h);
      if (!isInside(x, y, w, h)) continue;

      const r = 25 + Math.abs(offsets[pass]) * 1.5;
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, `rgba(160, 140, 200, ${alphas[pass]})`);
      g.addColorStop(0.5, `rgba(130, 110, 180, ${alphas[pass] * 0.4})`);
      g.addColorStop(1, "transparent");
      ctx.fillStyle = g;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
    }
  }

  // Galactic center — brighter, warmer glow
  const gc = mollweideProject(266, -29, w, h);
  if (isInside(gc.x, gc.y, w, h)) {
    for (const [radius, alpha] of [[100, 0.04], [60, 0.05], [30, 0.06]]) {
      const g = ctx.createRadialGradient(gc.x, gc.y, 0, gc.x, gc.y, radius);
      g.addColorStop(0, `rgba(200, 170, 230, ${alpha})`);
      g.addColorStop(0.5, `rgba(160, 130, 200, ${alpha * 0.3})`);
      g.addColorStop(1, "transparent");
      ctx.fillStyle = g;
      ctx.fillRect(gc.x - radius, gc.y - radius, radius * 2, radius * 2);
    }
  }

  // Anti-center region (RA ~76) — dimmer section
  const ac = mollweideProject(76, 24, w, h);
  if (isInside(ac.x, ac.y, w, h)) {
    const g = ctx.createRadialGradient(ac.x, ac.y, 0, ac.x, ac.y, 50);
    g.addColorStop(0, "rgba(140, 120, 180, 0.03)");
    g.addColorStop(1, "transparent");
    ctx.fillStyle = g;
    ctx.fillRect(ac.x - 50, ac.y - 50, 100, 100);
  }
}

function drawNebulae(ctx, w, h) {
  const rand = rng(888);
  const palettes = [
    [255, 70, 90],   // emission red
    [70, 130, 255],  // reflection blue
    [180, 70, 255],  // planetary purple
    [70, 200, 180],  // teal
    [255, 200, 80],  // warm gold
  ];
  for (let i = 0; i < 10; i++) {
    const x = rand() * w, y = rand() * h;
    if (!isInside(x, y, w, h)) continue;
    const c = palettes[Math.floor(rand() * palettes.length)];
    const r = 25 + rand() * 55;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, `rgba(${c[0]},${c[1]},${c[2]},0.018)`);
    g.addColorStop(0.4, `rgba(${c[0]},${c[1]},${c[2]},0.006)`);
    g.addColorStop(1, "transparent");
    ctx.fillStyle = g;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }
}

function drawGrid(ctx, w, h) {
  ctx.strokeStyle = "rgba(80, 120, 200, 0.055)";
  ctx.lineWidth = 0.5;
  for (let lon = -180; lon <= 180; lon += 30) {
    ctx.beginPath();
    for (let lat = -90; lat <= 90; lat++) {
      const ra = lon < 0 ? lon + 360 : lon;
      const { x, y } = mollweideProject(ra, lat, w, h);
      lat === -90 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    ctx.beginPath();
    for (let ra = 0; ra <= 360; ra++) {
      const { x, y } = mollweideProject(ra, lat, w, h);
      ra === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  // Boundary
  ctx.strokeStyle = "rgba(80, 130, 220, 0.15)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let lat = -90; lat <= 90; lat += 0.5) {
    const { x, y } = mollweideProject(0, lat, w, h);
    lat === -90 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  for (let lat = 90; lat >= -90; lat -= 0.5) {
    const { x, y } = mollweideProject(359.99, lat, w, h);
    ctx.lineTo(x, y);
  }
  ctx.closePath();
  ctx.stroke();
}

function drawAlerts(ctx, alerts, w, h, selectedOid, time) {
  if (!alerts?.length) return;
  for (const a of alerts) {
    if (a.ra == null || a.dec == null) continue;
    const { x, y } = mollweideProject(a.ra, a.dec, w, h);
    const info = getClassInfo(a.classification);
    const color = info.color;
    const sel = a.oid === selectedOid;
    const phase = (time * 0.0012 + a.ra * 0.08) % (Math.PI * 2);
    const ps = 16 + Math.sin(phase) * 5;

    // Outer pulse
    const og = ctx.createRadialGradient(x, y, 0, x, y, ps);
    og.addColorStop(0, color + "28");
    og.addColorStop(0.4, color + "0c");
    og.addColorStop(1, "transparent");
    ctx.fillStyle = og;
    ctx.fillRect(x - ps, y - ps, ps * 2, ps * 2);

    // Inner glow
    const ig = ctx.createRadialGradient(x, y, 0, x, y, 6);
    ig.addColorStop(0, color + "aa");
    ig.addColorStop(0.5, color + "30");
    ig.addColorStop(1, "transparent");
    ctx.fillStyle = ig;
    ctx.fillRect(x - 6, y - 6, 12, 12);

    // Core
    ctx.beginPath();
    ctx.arc(x, y, sel ? 3.5 : 2.2, 0, Math.PI * 2);
    ctx.fillStyle = sel ? "#fff" : color;
    ctx.fill();

    if (sel) {
      ctx.strokeStyle = color + "80";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(x, y, 10, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = color + "25";
      ctx.beginPath(); ctx.arc(x, y, 17, 0, Math.PI * 2); ctx.stroke();
    }
  }
}

export default function SkyMap({ alerts, selectedOid, onSelectAlert }) {
  const canvasRef = useRef(null);
  const animRef = useRef(null);
  const bgRef = useRef(null);
  const [hovered, setHovered] = useState(null);
  const [tip, setTip] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    const w = rect.width, h = rect.height;

    // Cache static layers
    if (!bgRef.current || bgRef.current.w !== w) {
      const off = document.createElement("canvas");
      off.width = w * dpr; off.height = h * dpr;
      const oc = off.getContext("2d");
      oc.scale(dpr, dpr);
      drawBackground(oc, w, h);
      drawMilkyWay(oc, w, h);
      drawNebulae(oc, w, h);
      drawGrid(oc, w, h);
      bgRef.current = { canvas: off, w };
    }

    function frame(t) {
      ctx.clearRect(0, 0, w, h);
      ctx.drawImage(bgRef.current.canvas, 0, 0, w, h);
      drawAlerts(ctx, alerts, w, h, selectedOid, t);
      animRef.current = requestAnimationFrame(frame);
    }
    animRef.current = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(animRef.current);
  }, [alerts, selectedOid]);

  const onMove = (e) => {
    if (!alerts?.length) return;
    const r = canvasRef.current.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    let best = null, bd = 22;
    for (const a of alerts) {
      if (a.ra == null) continue;
      const p = mollweideProject(a.ra, a.dec, r.width, r.height);
      const d = Math.hypot(p.x - mx, p.y - my);
      if (d < bd) { bd = d; best = a; }
    }
    setHovered(best);
    setTip({ x: mx, y: my });
  };

  return (
    <div className="relative rounded-2xl overflow-hidden border border-white/[0.08]">
      {/* Header */}
      <div className="absolute top-0 left-0 right-0 z-10 flex items-center justify-between px-5 py-3 bg-gradient-to-b from-[#060b18] via-[#060b18dd] to-transparent">
        <h3 className="text-xs text-white/35 tracking-wider uppercase">
          All-Sky View
          <span className="normal-case tracking-normal ml-2 text-white/20">
            {alerts?.length || 0} cosmic events
          </span>
        </h3>
        <div className="flex items-center gap-3 text-[9px]">
          {["SNIa", "SNII", "AGN", "TDE", "KN"].map((cls) => {
            const info = getClassInfo(cls);
            return (
              <span key={cls} className="flex items-center gap-1 text-white/25">
                <span className="text-[8px]">{info.emoji}</span>
                {info.name.split(" ").slice(-1)[0]}
              </span>
            );
          })}
        </div>
      </div>

      <canvas
        ref={canvasRef}
        className="w-full aspect-[2.2/1] cursor-crosshair"
        onMouseMove={onMove}
        onMouseLeave={() => setHovered(null)}
        onClick={() => hovered && onSelectAlert?.(hovered)}
      />

      <div className="absolute bottom-0 left-0 right-0 h-10 bg-gradient-to-t from-[#060b18] to-transparent pointer-events-none" />

      {/* Tooltip */}
      {hovered && (
        <div className="absolute pointer-events-none z-20" style={{
          left: tip.x + 16, top: tip.y - 12,
          transform: tip.x > 500 ? "translateX(-110%)" : "none",
        }}>
          <div className="bg-[#070e1f]/95 backdrop-blur-md border border-white/[0.08] rounded-xl px-3.5 py-2.5 shadow-2xl shadow-black/50 min-w-[180px]">
            <div className="flex items-center gap-2 mb-1">
              <span>{getClassInfo(hovered.classification).emoji}</span>
              <span className="text-xs font-medium text-white/80">
                {getClassInfo(hovered.classification).name}
              </span>
            </div>
            <p className="text-[10px] text-white/35 leading-relaxed">
              {getClassInfo(hovered.classification).short} in{" "}
              {getConstellation(hovered.ra, hovered.dec)}
            </p>
            <p className="text-[10px] text-white/20 mt-1 font-mono">
              {hovered.n_detections} observations · {hovered.oid}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
