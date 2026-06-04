import { useRef, useEffect, useState, useMemo } from "react";
import * as THREE from "three";
import { getClassInfo, getConstellation } from "../lib/cosmos";

// ── Helpers ──────────────────────────────────────────────────────────────────

function toXYZ(ra, dec, r) {
  const rr = (ra * Math.PI) / 180;
  const dr = (dec * Math.PI) / 180;
  return {
    x: r * Math.cos(dr) * Math.cos(rr),
    y: r * Math.sin(dr),
    z: r * Math.cos(dr) * Math.sin(rr),
  };
}

function rand(min, max) {
  return min + Math.random() * (max - min);
}

function makeTex() {
  const c = document.createElement("canvas");
  c.width = c.height = 32;
  const ctx2 = c.getContext("2d");
  const g = ctx2.createRadialGradient(16, 16, 0, 16, 16, 16);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.5, "rgba(255,255,255,0.6)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx2.fillStyle = g;
  ctx2.fillRect(0, 0, 32, 32);
  return new THREE.CanvasTexture(c);
}

// ── Component ────────────────────────────────────────────────────────────────

export default function SkyMap({ alerts, onSelectAlert, page = 1, totalPages = 1, total }) {
  const mountRef    = useRef(null);
  const emojiRef    = useRef(null);
  const interactRef = useRef(null);

  // Shared between the two effects via refs — no re-render on update
  const eventsRef = useRef([]); // current 3D event objects
  const spansRef  = useRef([]); // current emoji span elements

  const [hovered, setHovered] = useState(null);
  const [tipPos,  setTipPos]  = useState({ x: 0, y: 0 });

  // Build 3D event objects from alerts
  const events = useMemo(() => {
    if (!alerts?.length) return [];
    return alerts
      .filter((a) => a.ra != null && a.dec != null)
      .map((a, i) => ({ ...toXYZ(a.ra, a.dec, rand(80, 400)), index: i, alert: a }));
  }, [alerts]);

  // Legend: only classifications present in current alerts
  const legendItems = useMemo(() => {
    if (!alerts?.length) return [];
    const seen = new Map();
    for (const a of alerts) {
      const info = getClassInfo(a.classification);
      if (!seen.has(info.emoji)) seen.set(info.emoji, info);
    }
    return [...seen.values()];
  }, [alerts]);

  // ── Effect 1: THREE.js scene — runs ONCE on mount ────────────────────────
  // Star field, renderer, camera, controls, animation loop.
  // Never rebuilds. Reads eventsRef / spansRef as live refs each frame.
  useEffect(() => {
    const mount    = mountRef.current;
    const interact = interactRef.current;
    if (!mount || !interact) return;

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.setClearColor(0x020208);
    mount.appendChild(renderer.domElement);

    // Scene + Camera
    const scene  = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(
      75, mount.clientWidth / mount.clientHeight, 0.1, 5000
    );
    camera.position.set(0, 0, 0);

    const tex = makeTex();

    // ── Star layer 1: 40,000 pts, vertex colors ──────────────────────────
    {
      const count = 40000;
      const pos = new Float32Array(count * 3);
      const col = new Float32Array(count * 3);
      const palettes = [
        [0.85, 0.9,  1.0],
        [1.0,  1.0,  1.0],
        [1.0,  0.95, 0.8],
        [0.7,  0.8,  1.0],
      ];
      for (let i = 0; i < count; i++) {
        const theta = Math.random() * Math.PI * 2;
        const phi   = Math.acos(2 * Math.random() - 1);
        const r     = rand(120, 1500);
        pos[i * 3]     = r * Math.sin(phi) * Math.cos(theta);
        pos[i * 3 + 1] = r * Math.cos(phi);
        pos[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
        const p = palettes[Math.floor(Math.random() * palettes.length)];
        col[i * 3] = p[0]; col[i * 3 + 1] = p[1]; col[i * 3 + 2] = p[2];
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      geo.setAttribute("color",    new THREE.BufferAttribute(col, 3));
      scene.add(new THREE.Points(geo, new THREE.PointsMaterial({
        size: 0.7, map: tex, vertexColors: true,
        transparent: true, alphaTest: 0.005, depthWrite: false,
      })));
    }

    // ── Star layer 2: 1,000 pts, bright additive ─────────────────────────
    {
      const count = 1000;
      const pos = new Float32Array(count * 3);
      for (let i = 0; i < count; i++) {
        const theta = Math.random() * Math.PI * 2;
        const phi   = Math.acos(2 * Math.random() - 1);
        const r     = rand(80, 600);
        pos[i * 3]     = r * Math.sin(phi) * Math.cos(theta);
        pos[i * 3 + 1] = r * Math.cos(phi);
        pos[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      scene.add(new THREE.Points(geo, new THREE.PointsMaterial({
        size: 1.4, map: tex, color: 0xffffff,
        transparent: true, alphaTest: 0.005,
        blending: THREE.AdditiveBlending, depthWrite: false,
      })));
    }

    // ── Star layer 3: 12,000 pts, Milky Way band ─────────────────────────
    {
      const count = 12000;
      const pos = new Float32Array(count * 3);
      for (let i = 0; i < count; i++) {
        const theta = Math.random() * Math.PI * 2;
        const phi   = Math.PI / 2 + (Math.random() - 0.5) * 0.32;
        const r     = rand(400, 1300);
        pos[i * 3]     = r * Math.sin(phi) * Math.cos(theta);
        pos[i * 3 + 1] = r * Math.cos(phi);
        pos[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
      }
      // Tilt ~63° around X axis — diagonal streak, not flat disc
      const tilt = 63 * Math.PI / 180;
      const cosT = Math.cos(tilt), sinT = Math.sin(tilt);
      for (let i = 0; i < count; i++) {
        const y = pos[i * 3 + 1], z = pos[i * 3 + 2];
        pos[i * 3 + 1] =  y * cosT - z * sinT;
        pos[i * 3 + 2] =  y * sinT + z * cosT;
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      scene.add(new THREE.Points(geo, new THREE.PointsMaterial({
        size: 0.5, map: tex, color: 0xaac4ff,
        transparent: true, opacity: 0.18, alphaTest: 0.005,
        blending: THREE.AdditiveBlending, depthWrite: false,
      })));
    }

    // ── Camera controls ───────────────────────────────────────────────────
    let yaw = 0, pitch = 0, flyV = 0;
    let isDragging = false, didDrag = false;
    let lastX = 0, lastY = 0;
    const euler  = new THREE.Euler(0, 0, 0, "YXZ");
    const dir    = new THREE.Vector3();
    const projV  = new THREE.Vector3();
    const hprojV = new THREE.Vector3();

    const onMouseDown = (e) => {
      isDragging = true; didDrag = false;
      lastX = e.clientX; lastY = e.clientY;
    };
    const onMouseMove = (e) => {
      if (!isDragging) return;
      const dx = e.clientX - lastX, dy = e.clientY - lastY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) didDrag = true;
      yaw -= dx * 0.003;
      pitch = Math.max(-Math.PI / 2 + 0.01, Math.min(Math.PI / 2 - 0.01, pitch - dy * 0.003));
      lastX = e.clientX; lastY = e.clientY;
    };
    const onMouseUp = () => { isDragging = false; };
    const onWheel = (e) => {
      flyV = Math.max(-30, Math.min(30, flyV - e.deltaY * 0.04));
    };

    let ltx = 0, lty = 0;
    const onTouchStart = (e) => {
      if (e.touches.length !== 1) return;
      isDragging = true; didDrag = false;
      ltx = e.touches[0].clientX; lty = e.touches[0].clientY;
    };
    const onTouchMove = (e) => {
      if (!isDragging || e.touches.length !== 1) return;
      const dx = e.touches[0].clientX - ltx, dy = e.touches[0].clientY - lty;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) didDrag = true;
      yaw -= dx * 0.003;
      pitch = Math.max(-Math.PI / 2 + 0.01, Math.min(Math.PI / 2 - 0.01, pitch - dy * 0.003));
      ltx = e.touches[0].clientX; lty = e.touches[0].clientY;
    };
    const onTouchEnd = () => { isDragging = false; };

    // Hover — reads eventsRef.current (live, set by Effect 2)
    const onPointerMove = (e) => {
      const rect = interact.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const W = interact.offsetWidth, H = interact.offsetHeight;
      let nearest = null, nd = 20;
      for (const ev of eventsRef.current) {
        hprojV.set(ev.x, ev.y, ev.z).project(camera);
        if (hprojV.z > 1) continue;
        const d = Math.hypot((hprojV.x * 0.5 + 0.5) * W - mx, (-hprojV.y * 0.5 + 0.5) * H - my);
        if (d < nd) { nd = d; nearest = ev; }
      }
      setHovered(nearest ? nearest.alert : null);
      setTipPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
    };
    const onPointerLeave = () => setHovered(null);

    // Click — reads eventsRef.current (live)
    const onClick = (e) => {
      if (didDrag) return;
      const rect = interact.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const W = interact.offsetWidth, H = interact.offsetHeight;
      let nearest = null, nd = 36;
      for (const ev of eventsRef.current) {
        hprojV.set(ev.x, ev.y, ev.z).project(camera);
        if (hprojV.z > 1) continue;
        const d = Math.hypot((hprojV.x * 0.5 + 0.5) * W - mx, (-hprojV.y * 0.5 + 0.5) * H - my);
        if (d < nd) { nd = d; nearest = ev; }
      }
      if (nearest) onSelectAlert?.(nearest.alert);
    };

    interact.addEventListener("mousedown",  onMouseDown);
    window.addEventListener("mousemove",    onMouseMove);
    window.addEventListener("mouseup",      onMouseUp);
    interact.addEventListener("wheel",      onWheel, { passive: true });
    interact.addEventListener("mousemove",  onPointerMove);
    interact.addEventListener("mouseleave", onPointerLeave);
    interact.addEventListener("click",      onClick);
    interact.addEventListener("touchstart", onTouchStart, { passive: true });
    interact.addEventListener("touchmove",  onTouchMove,  { passive: true });
    interact.addEventListener("touchend",   onTouchEnd);

    const ro = new ResizeObserver(() => {
      const w = mount.clientWidth, h = mount.clientHeight;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    });
    ro.observe(mount);

    // Animation loop — reads eventsRef / spansRef as live refs each frame
    let rafId;
    const animate = () => {
      rafId = requestAnimationFrame(animate);

      if (!isDragging) yaw += 0.00035;
      euler.set(pitch, yaw, 0);
      camera.quaternion.setFromEuler(euler);

      if (Math.abs(flyV) > 0.05) {
        dir.set(0, 0, -1).applyQuaternion(camera.quaternion);
        camera.position.addScaledVector(dir, flyV * 0.35);
        flyV *= 0.9;
      }

      renderer.render(scene, camera);

      const cw = mount.clientWidth, ch = mount.clientHeight;
      const curEvents = eventsRef.current;
      const curSpans  = spansRef.current;
      for (let i = 0; i < curEvents.length; i++) {
        const ev   = curEvents[i];
        const span = curSpans[i];
        if (!span) continue;
        projV.set(ev.x, ev.y, ev.z).project(camera);
        if (projV.z > 1 || Math.abs(projV.x) > 1.2 || Math.abs(projV.y) > 1.2) {
          span.style.visibility = "hidden";
          continue;
        }
        const sx    = (projV.x * 0.5 + 0.5) * cw;
        const sy    = (-projV.y * 0.5 + 0.5) * ch;
        const depth = Math.max(0, Math.min(1, 1 - (projV.z + 1) / 2));
        span.style.visibility = "visible";
        span.style.transform  = `translate(calc(${sx}px - 50%), calc(${sy}px - 50%))`;
        span.style.fontSize   = Math.round(20 + depth * 18) + "px";
        span.style.opacity    = String(0.4 + depth * 0.6);
      }
    };
    animate();

    return () => {
      cancelAnimationFrame(rafId);
      interact.removeEventListener("mousedown",  onMouseDown);
      window.removeEventListener("mousemove",    onMouseMove);
      window.removeEventListener("mouseup",      onMouseUp);
      interact.removeEventListener("wheel",      onWheel);
      interact.removeEventListener("mousemove",  onPointerMove);
      interact.removeEventListener("mouseleave", onPointerLeave);
      interact.removeEventListener("click",      onClick);
      interact.removeEventListener("touchstart", onTouchStart);
      interact.removeEventListener("touchmove",  onTouchMove);
      interact.removeEventListener("touchend",   onTouchEnd);
      ro.disconnect();
      renderer.dispose();
      tex.dispose();
      if (mount.contains(renderer.domElement)) mount.removeChild(renderer.domElement);
    };
  }, []); // ← EMPTY: star field + controls built once, never rebuilt

  // ── Effect 2: sync emoji spans when alerts change ────────────────────────
  // Only swaps spans. Star field untouched. No visual flash on filter change.
  useEffect(() => {
    const emojiDiv = emojiRef.current;
    if (!emojiDiv) return;

    spansRef.current.forEach((s) => s.remove());

    const newSpans = events.map((ev) => {
      const span = document.createElement("span");
      span.textContent = getClassInfo(ev.alert.classification).emoji;
      span.style.cssText = [
        "position:absolute", "top:0", "left:0",
        "pointer-events:none", "user-select:none",
        "will-change:transform,opacity", "line-height:1",
        "visibility:hidden",
      ].join(";");
      emojiDiv.appendChild(span);
      return span;
    });

    eventsRef.current = events;
    spansRef.current  = newSpans;

    return () => { newSpans.forEach((s) => s.remove()); };
  }, [events]); // ← only event layer rebuilds on filter change

  // ── Tooltip position ──────────────────────────────────────────────────────
  const getTipStyle = () => {
    const W = mountRef.current?.clientWidth  ?? 800;
    const H = mountRef.current?.clientHeight ?? 400;
    const cardW = 200, cardH = 90;
    let left = tipPos.x + 14;
    let top  = tipPos.y - cardH / 2;
    if (left + cardW > W - 8) left = tipPos.x - cardW - 14;
    if (top < 8)              top  = 8;
    if (top + cardH > H - 8)  top  = H - cardH - 8;
    return { position: "absolute", left, top, zIndex: 20, pointerEvents: "none" };
  };

  return (
    <div
      className="relative rounded-2xl overflow-hidden border border-white/[0.08]"
      style={{ height: "clamp(420px, 55vw, 650px)" }}
    >
      <div ref={mountRef} style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }} />
      <div ref={emojiRef} style={{ position: "absolute", inset: 0, pointerEvents: "none" }} />

      {/* Header */}
      <div
        style={{ position: "absolute", top: 0, left: 0, right: 0, pointerEvents: "none" }}
        className="z-10 flex items-center justify-between px-5 py-3 bg-gradient-to-b from-[#060b18] via-[#060b18dd] to-transparent"
      >
        <h3 className="text-xs text-white/35 tracking-wider uppercase">
          All-Sky View
          <span className="normal-case tracking-normal ml-2 text-white/20">
            Page {page} of {totalPages} · {alerts?.length ?? 0} events shown
          </span>
        </h3>
      </div>

      {/* Bottom fade */}
      <div
        style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 40, pointerEvents: "none" }}
        className="bg-gradient-to-t from-[#060b18] to-transparent"
      />

      {/* Legend */}
      {legendItems.length > 0 && (
        <div
          style={{ position: "absolute", bottom: 10, left: 14, pointerEvents: "none", zIndex: 10 }}
          className="flex flex-wrap gap-x-3 gap-y-0.5"
        >
          {legendItems.map((info) => (
            <span key={info.emoji} className="text-[9px] text-white/25 whitespace-nowrap">
              {info.emoji} {info.name.split(" ").slice(-1)[0]}
            </span>
          ))}
        </div>
      )}

      {/* Interaction layer */}
      <div ref={interactRef} style={{ position: "absolute", inset: 0, cursor: "grab", zIndex: 15 }} />

      {/* Tooltip */}
      {hovered && (
        <div style={getTipStyle()}>
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
