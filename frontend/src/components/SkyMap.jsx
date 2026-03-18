import { useRef, useEffect, useState } from "react";

const CLASS_COLORS = {
  SNIa: "#ff6b6b",
  SNII: "#ffa94d",
  SNIbc: "#ff8787",
  SLSN: "#e599f7",
  TDE: "#66d9e8",
  AGN: "#74c0fc",
  Blazar: "#748ffc",
  QSO: "#91a7ff",
  KN: "#ffd43b",
  "CV/Nova": "#69db7c",
};

function mollweideProject(raDeg, decDeg, width, height) {
  let lon = raDeg;
  if (lon > 180) lon -= 360;
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
  const scale = Math.min(width / (4 * Math.SQRT2), height / (2 * Math.SQRT2));
  return { x: width / 2 + x * scale * 0.95, y: height / 2 - y * scale * 0.95 };
}

function isInsideEllipse(px, py, w, h) {
  const scale = Math.min(w / (4 * Math.SQRT2), h / (2 * Math.SQRT2));
  const a = 2 * Math.SQRT2 * scale * 0.95;
  const b = Math.SQRT2 * scale * 0.95;
  return ((px - w / 2) ** 2) / (a ** 2) + ((py - h / 2) ** 2) / (b ** 2) <= 1;
}

function seededRand(seed) {
  let s = seed;
  return () => { s = (s * 16807) % 2147483647; return s / 2147483647; };
}

function drawStarfield(ctx, w, h) {
  const rand = seededRand(42);
  for (let i = 0; i < 2500; i++) {
    const x = rand() * w, y = rand() * h;
    if (!isInsideEllipse(x, y, w, h)) continue;
    const b = rand();
    ctx.beginPath();
    ctx.arc(x, y, b < 0.95 ? 0.4 : b < 0.99 ? 0.8 : 1.2, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(190, 210, 255, ${0.08 + b * 0.45})`;
    ctx.fill();
  }
  // Bright stars with cross-hairs
  for (let i = 0; i < 20; i++) {
    const x = rand() * w, y = rand() * h;
    if (!isInsideEllipse(x, y, w, h)) continue;
    const g = ctx.createRadialGradient(x, y, 0, x, y, 5);
    g.addColorStop(0, "rgba(200, 220, 255, 0.5)");
    g.addColorStop(0.4, "rgba(180, 200, 255, 0.1)");
    g.addColorStop(1, "transparent");
    ctx.fillStyle = g;
    ctx.fillRect(x - 5, y - 5, 10, 10);
    ctx.beginPath();
    ctx.arc(x, y, 0.7, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(230, 240, 255, 0.8)";
    ctx.fill();
  }
}

function drawMilkyWay(ctx, w, h) {
  for (let ra = 0; ra <= 360; ra += 0.4) {
    const dec = 28 * Math.sin((ra - 280) * (Math.PI / 180));
    const { x, y } = mollweideProject(ra, dec, w, h);
    if (!isInsideEllipse(x, y, w, h)) continue;
    const g = ctx.createRadialGradient(x, y, 0, x, y, 40);
    g.addColorStop(0, "rgba(130, 110, 180, 0.035)");
    g.addColorStop(0.5, "rgba(110, 90, 160, 0.015)");
    g.addColorStop(1, "transparent");
    ctx.fillStyle = g;
    ctx.fillRect(x - 40, y - 40, 80, 80);
  }
  // Galactic center glow
  const gc = mollweideProject(266, -29, w, h);
  if (isInsideEllipse(gc.x, gc.y, w, h)) {
    const g = ctx.createRadialGradient(gc.x, gc.y, 0, gc.x, gc.y, 90);
    g.addColorStop(0, "rgba(170, 130, 210, 0.07)");
    g.addColorStop(0.4, "rgba(140, 100, 190, 0.03)");
    g.addColorStop(1, "transparent");
    ctx.fillStyle = g;
    ctx.fillRect(gc.x - 90, gc.y - 90, 180, 180);
  }
}

function drawNebulae(ctx, w, h) {
  const rand = seededRand(777);
  const colors = [[255,80,100],[80,140,255],[180,80,255],[80,200,190],[255,180,80]];
  for (let i = 0; i < 12; i++) {
    const x = rand() * w, y = rand() * h;
    if (!isInsideEllipse(x, y, w, h)) continue;
    const c = colors[Math.floor(rand() * colors.length)];
    const r = 30 + rand() * 70;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, `rgba(${c[0]},${c[1]},${c[2]},0.02)`);
    g.addColorStop(0.5, `rgba(${c[0]},${c[1]},${c[2]},0.008)`);
    g.addColorStop(1, "transparent");
    ctx.fillStyle = g;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }
}

function drawGrid(ctx, w, h) {
  ctx.strokeStyle = "rgba(80, 120, 200, 0.07)";
  ctx.lineWidth = 0.5;
  for (let lon = -180; lon <= 180; lon += 30) {
    ctx.beginPath();
    for (let lat = -90; lat <= 90; lat += 1) {
      const ra = lon < 0 ? lon + 360 : lon;
      const { x, y } = mollweideProject(ra, lat, w, h);
      lat === -90 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    ctx.beginPath();
    for (let ra = 0; ra <= 360; ra += 1) {
      const { x, y } = mollweideProject(ra, lat, w, h);
      ra === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  // Equator brighter
  ctx.strokeStyle = "rgba(80, 130, 220, 0.12)";
  ctx.beginPath();
  for (let ra = 0; ra <= 360; ra += 1) {
    const { x, y } = mollweideProject(ra, 0, w, h);
    ra === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();
  // Boundary
  ctx.strokeStyle = "rgba(80, 130, 220, 0.18)";
  ctx.lineWidth = 1.5;
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
    const color = CLASS_COLORS[a.classification] || "#888";
    const sel = a.oid === selectedOid;

    // Animated pulse
    const phase = (time * 0.0015 + a.ra * 0.1) % (Math.PI * 2);
    const ps = 14 + Math.sin(phase) * 5;

    // Outer glow
    const og = ctx.createRadialGradient(x, y, 0, x, y, ps);
    og.addColorStop(0, color + "35");
    og.addColorStop(0.5, color + "12");
    og.addColorStop(1, "transparent");
    ctx.fillStyle = og;
    ctx.fillRect(x - ps, y - ps, ps * 2, ps * 2);

    // Inner glow
    const ig = ctx.createRadialGradient(x, y, 0, x, y, 7);
    ig.addColorStop(0, color + "bb");
    ig.addColorStop(0.5, color + "33");
    ig.addColorStop(1, "transparent");
    ctx.fillStyle = ig;
    ctx.fillRect(x - 7, y - 7, 14, 14);

    // Core
    ctx.beginPath();
    ctx.arc(x, y, sel ? 3.5 : 2, 0, Math.PI * 2);
    ctx.fillStyle = sel ? "#fff" : color;
    ctx.fill();

    if (sel) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(x, y, 11, 0, Math.PI * 2);
      ctx.stroke();
      ctx.strokeStyle = color + "30";
      ctx.beginPath();
      ctx.arc(x, y, 18, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
}

export default function SkyMap({ alerts, selectedOid, onSelectAlert }) {
  const canvasRef = useRef(null);
  const animRef = useRef(null);
  const bgRef = useRef(null); // cached background
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

    // Pre-render static layers to offscreen canvas (performance)
    if (!bgRef.current || bgRef.current.w !== w || bgRef.current.h !== h) {
      const offscreen = document.createElement("canvas");
      offscreen.width = w * dpr;
      offscreen.height = h * dpr;
      const octx = offscreen.getContext("2d");
      octx.scale(dpr, dpr);
      drawStarfield(octx, w, h);
      drawMilkyWay(octx, w, h);
      drawNebulae(octx, w, h);
      drawGrid(octx, w, h);
      bgRef.current = { canvas: offscreen, w, h };
    }

    function frame(time) {
      ctx.clearRect(0, 0, w, h);
      // Draw cached background
      ctx.drawImage(bgRef.current.canvas, 0, 0, w, h);
      // Draw animated alerts
      drawAlerts(ctx, alerts, w, h, selectedOid, time);
      animRef.current = requestAnimationFrame(frame);
    }
    animRef.current = requestAnimationFrame(frame);
    return () => { if (animRef.current) cancelAnimationFrame(animRef.current); };
  }, [alerts, selectedOid]);

  const onMove = (e) => {
    if (!alerts?.length) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    let best = null, bestD = 22;
    for (const a of alerts) {
      if (a.ra == null) continue;
      const { x, y } = mollweideProject(a.ra, a.dec, rect.width, rect.height);
      const d = Math.hypot(x - mx, y - my);
      if (d < bestD) { bestD = d; best = a; }
    }
    setHovered(best);
    setTip({ x: mx, y: my });
  };

  return (
    <div className="relative rounded-xl overflow-hidden border border-white/[0.08]" style={{ background: "#050c1a" }}>
      {/* Top overlay */}
      <div className="absolute top-0 left-0 right-0 z-10 flex items-center justify-between px-4 py-2.5 bg-gradient-to-b from-[#050c1a] via-[#050c1aee] to-transparent">
        <h3 className="text-xs font-medium text-white/40 tracking-wider uppercase">
          All-Sky View <span className="text-white/25 normal-case tracking-normal ml-1">{alerts?.length || 0} transients</span>
        </h3>
        <div className="flex items-center gap-3 text-[9px]">
          {Object.entries(CLASS_COLORS).slice(0, 6).map(([cls, color]) => (
            <span key={cls} className="flex items-center gap-1 text-white/30">
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: color, boxShadow: `0 0 6px ${color}80` }} />
              {cls}
            </span>
          ))}
        </div>
      </div>

      <canvas
        ref={canvasRef}
        className="w-full aspect-[2.2/1] cursor-crosshair"
        onMouseMove={onMove}
        onMouseLeave={() => setHovered(null)}
        onClick={() => hovered && onSelectAlert?.(hovered)}
      />

      <div className="absolute bottom-0 left-0 right-0 h-8 bg-gradient-to-t from-[#050c1a] to-transparent pointer-events-none" />

      {hovered && (
        <div className="absolute pointer-events-none z-20" style={{
          left: tip.x + 14, top: tip.y - 10,
          transform: tip.x > 500 ? "translateX(-110%)" : "none",
        }}>
          <div className="bg-[#080f22]/95 backdrop-blur-md border border-white/10 rounded-lg px-3 py-2 text-[11px] shadow-xl shadow-black/50">
            <p className="font-mono text-white/90 font-medium">{hovered.oid}</p>
            <div className="flex items-center gap-2 mt-1">
              <span className="w-1.5 h-1.5 rounded-full" style={{
                background: CLASS_COLORS[hovered.classification] || "#888",
                boxShadow: `0 0 6px ${CLASS_COLORS[hovered.classification] || "#888"}`,
              }} />
              <span className="text-white/50">{hovered.classification}</span>
              <span className="text-white/20">|</span>
              <span className="text-white/35">{hovered.n_detections} det</span>
            </div>
            <p className="text-white/25 mt-0.5 font-mono text-[10px]">
              {hovered.ra?.toFixed(3)}° {hovered.dec?.toFixed(3)}°
            </p>
          </div>
        </div>
      )}
    </div>
  );
}