import CelestialGlobe from "../components/CelestialGlobe";

export default function GlobePage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Celestial Globe</h1>
        <p className="text-sm text-white/40 mt-1 max-w-xl">
          All astronomical transients, gravitational wave events, and fast radio bursts
          mapped on the celestial sphere. Drag to rotate, scroll to zoom.
        </p>
      </div>

      <div
        className="rounded-2xl overflow-hidden border border-white/[0.08]"
        style={{ height: "70vh" }}
      >
        <CelestialGlobe />
      </div>

      {/* Legend */}
      <div className="flex items-center gap-6 text-xs text-white/30">
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full inline-block" style={{ background: "#F59E0B" }} />
          Transients
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full inline-block" style={{ background: "#8B5CF6" }} />
          GW Events
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full inline-block" style={{ background: "#06B6D4" }} />
          FRBs
        </span>
      </div>
    </div>
  );
}
