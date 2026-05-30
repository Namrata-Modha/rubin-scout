import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, Html } from "@react-three/drei";
import * as THREE from "three";
import { getRecentAlerts, getGWEvents, getFRBEvents } from "../lib/api";

// RA/Dec degrees → Cartesian on sphere of radius 2.0
const toXYZ = (ra, dec) => {
  const r = (ra * Math.PI) / 180;
  const d = (dec * Math.PI) / 180;
  return [
    2.0 * Math.cos(d) * Math.cos(r),
    2.0 * Math.sin(d),
    2.0 * Math.cos(d) * Math.sin(r),
  ];
};

// Build a Float32Array of XYZ positions from an array of [ra, dec] pairs.
function buildPositions(pairs) {
  const arr = new Float32Array(pairs.length * 3);
  pairs.forEach(([ra, dec], i) => {
    const [x, y, z] = toXYZ(ra, dec);
    arr[i * 3] = x;
    arr[i * 3 + 1] = y;
    arr[i * 3 + 2] = z;
  });
  return arr;
}

// ---------------------------------------------------------------------------
// PointCloud — one THREE.Points object for a single catalog
// ---------------------------------------------------------------------------
function PointCloud({ positions, color, eventData, onHover, onClickTransient }) {
  const { raycaster } = useThree();

  // Widen hit threshold so sparse points are easy to hover.
  useEffect(() => {
    raycaster.params.Points.threshold = 0.1;
  }, [raycaster]);

  const handlePointerMove = useCallback(
    (e) => {
      e.stopPropagation();
      const hit = e.intersections[0];
      if (!hit) return;
      const pt = eventData[hit.index];
      if (pt) onHover({ ...pt, position: hit.point.clone() });
    },
    [eventData, onHover]
  );

  const handlePointerLeave = useCallback(() => onHover(null), [onHover]);

  const handleClick = useCallback(
    (e) => {
      e.stopPropagation();
      const hit = e.intersections[0];
      if (!hit) return;
      const pt = eventData[hit.index];
      if (pt && onClickTransient) onClickTransient(pt.id, pt.type);
    },
    [eventData, onClickTransient]
  );

  if (!positions || positions.length === 0) return null;

  return (
    <points
      onPointerMove={handlePointerMove}
      onPointerLeave={handlePointerLeave}
      onClick={handleClick}
    >
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial
        color={color}
        size={0.04}
        sizeAttenuation
        transparent
        opacity={0.85}
        blending={THREE.AdditiveBlending}
        depthWrite={false}
      />
    </points>
  );
}

// ---------------------------------------------------------------------------
// GlobeScene — everything rendered inside <Canvas>
// ---------------------------------------------------------------------------
function GlobeScene({
  tPos, tData,
  gPos, gData,
  fPos, fData,
  hovered, onHover, onClickTransient,
}) {
  const controlsRef = useRef();

  return (
    <>
      {/* Decorative sphere */}
      <mesh>
        <sphereGeometry args={[1.98, 64, 64]} />
        <meshBasicMaterial color="#0a0a1a" />
      </mesh>

      {/* Three point clouds — one draw call each */}
      <PointCloud
        positions={tPos}
        color="#F59E0B"
        eventData={tData}
        onHover={onHover}
        onClickTransient={onClickTransient}
      />
      <PointCloud
        positions={gPos}
        color="#8B5CF6"
        eventData={gData}
        onHover={onHover}
        onClickTransient={null}
      />
      <PointCloud
        positions={fPos}
        color="#06B6D4"
        eventData={fData}
        onHover={onHover}
        onClickTransient={null}
      />

      {/* Hover tooltip pinned to the hovered point's world position */}
      {hovered && (
        <Html
          position={[hovered.position.x, hovered.position.y, hovered.position.z]}
          style={{ pointerEvents: "none" }}
        >
          <div className="bg-[#070e1f]/95 backdrop-blur border border-white/[0.08] rounded-xl px-3 py-2 whitespace-nowrap shadow-2xl shadow-black/50">
            <p className="text-xs font-medium text-white/80">{hovered.id}</p>
            <p className="text-[10px] text-white/40">{hovered.type}</p>
            <p className="text-[10px] text-white/25 font-mono">
              RA {hovered.ra.toFixed(2)}° Dec {hovered.dec.toFixed(2)}°
            </p>
          </div>
        </Html>
      )}

      <OrbitControls
        ref={controlsRef}
        autoRotate
        autoRotateSpeed={0.3}
        minDistance={2.5}
        maxDistance={8.0}
        enableDamping
        dampingFactor={0.05}
        onStart={() => {
          if (controlsRef.current) controlsRef.current.autoRotate = false;
        }}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// CelestialGlobe — outer component; owns data fetching and hover state
// ---------------------------------------------------------------------------
export default function CelestialGlobe() {
  const navigate = useNavigate();
  const [tPos, setTPos] = useState(new Float32Array(0));
  const [tData, setTData] = useState([]);
  const [gPos, setGPos] = useState(new Float32Array(0));
  const [gData, setGData] = useState([]);
  const [fPos, setFPos] = useState(new Float32Array(0));
  const [fData, setFData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [hovered, setHovered] = useState(null);

  useEffect(() => {
    async function load() {
      const [alertsRes, gwRes, frbRes] = await Promise.all([
        getRecentAlerts({ limit: 500, hours: 87600, minProbability: 0 }).catch((e) => {
          console.warn("Transient fetch failed", e);
          return { alerts: [] };
        }),
        getGWEvents({ limit: 265 }).catch((e) => {
          console.warn("GW fetch failed", e);
          return { events: [] };
        }),
        getFRBEvents({ limit: 600 }).catch((e) => {
          console.warn("FRB fetch failed", e);
          return { alerts: [] };
        }),
      ]);

      // Transients
      const alerts = alertsRes.alerts ?? [];
      setTPos(buildPositions(alerts.map((a) => [a.ra, a.dec])));
      setTData(alerts.map((a) => ({ id: a.oid, type: "Transient", ra: a.ra, dec: a.dec })));

      // GW events — skip any without a sky position
      const gwEvts = (gwRes.events ?? []).filter(
        (e) => e.ra_center != null && e.dec_center != null
      );
      setGPos(buildPositions(gwEvts.map((e) => [e.ra_center, e.dec_center])));
      setGData(gwEvts.map((e) => ({ id: e.superevent_id, type: "GW Event", ra: e.ra_center, dec: e.dec_center })));

      // FRBs
      const frbs = frbRes.alerts ?? [];
      setFPos(buildPositions(frbs.map((a) => [a.ra, a.dec])));
      setFData(frbs.map((a) => ({ id: a.oid, type: "FRB", ra: a.ra, dec: a.dec })));

      setLoading(false);
    }
    load();
  }, []);

  const handleClickTransient = useCallback(
    (id, type) => {
      if (type === "Transient") navigate(`/alert/${id}`);
    },
    [navigate]
  );

  return (
    <div style={{ width: "100%", height: "100%" }}>
      {loading && (
        <div
          style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%" }}
        >
          <span className="text-white/40 text-sm">Loading sky map…</span>
        </div>
      )}
      {!loading && (
        <Canvas
          camera={{ position: [0, 0, 5], fov: 60 }}
          style={{ width: "100%", height: "100%" }}
          gl={{ antialias: true }}
          onCreated={({ scene }) => {
            scene.background = new THREE.Color("#050510");
          }}
        >
          <GlobeScene
            tPos={tPos}
            tData={tData}
            gPos={gPos}
            gData={gData}
            fPos={fPos}
            fData={fData}
            hovered={hovered}
            onHover={setHovered}
            onClickTransient={handleClickTransient}
          />
        </Canvas>
      )}
    </div>
  );
}
