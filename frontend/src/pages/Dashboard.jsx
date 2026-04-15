import { useState, useEffect, useCallback } from "react";
import { ChevronLeft, ChevronRight, Filter, RefreshCw } from "lucide-react";
import StatsBar from "../components/StatsBar";
import SkyMap from "../components/SkyMap";
import AlertTable from "../components/AlertTable";
import { getRecentAlerts, getSummaryStats, getClassifications } from "../lib/api";

const LOOKBACK_OPTIONS = [
  { label: "24h", value: 24 },
  { label: "7d", value: 168 },
  { label: "30d", value: 720 },
  { label: "90d", value: 2160 },
  { label: "1y", value: 8760 },
  { label: "All", value: 87600 },
];

const PER_PAGE = 12;

export default function Dashboard() {
  const [alerts, setAlerts] = useState([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState(null);
  const [classifications, setClassifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Filters
  const [selectedClass, setSelectedClass] = useState("");
  const [hours, setHours] = useState(87600);
  const [minProb, setMinProb] = useState(0.5);

  // Pagination
  const [page, setPage] = useState(1);

  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const offset = (page - 1) * PER_PAGE;
      const [alertsRes, statsRes, classRes] = await Promise.all([
      getRecentAlerts({
        ...(selectedClass && { classification: selectedClass }),  // Only include if not empty
        minProbability: minProb,
        hours,
        limit: PER_PAGE,
        offset,
      }),
        getSummaryStats(hours),
        getClassifications(),
      ]);
      setAlerts(alertsRes.alerts || []);
      setTotal(alertsRes.total || alertsRes.count || 0);
      setStats(statsRes);
      setClassifications(classRes.classifications || []);
    } catch (err) {
      setError(err.message);
      console.error("Failed to fetch data:", err);
    } finally {
      setLoading(false);
    }
  }, [selectedClass, hours, minProb, page]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [selectedClass, hours, minProb]);

  // Auto-refresh every 60 seconds
  useEffect(() => {
    const interval = setInterval(fetchData, 60000);
    return () => clearInterval(interval);
  }, [fetchData]);

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            What's happening in the universe
          </h1>
          <p className="text-sm text-white/40 mt-1">
            Exploding stars, feeding black holes, and cosmic collisions detected by telescopes around the world
          </p>
        </div>
        <button
          onClick={fetchData}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-white/[0.06] hover:bg-white/[0.1] text-sm text-white/60 hover:text-white transition-all disabled:opacity-30"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Stats */}
      <StatsBar stats={stats} loading={loading} />

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1.5">
          <Filter className="w-3.5 h-3.5 text-white/30" />
          <span className="text-xs text-white/30 uppercase tracking-wider">
            Filters
          </span>
        </div>

        {/* Time window */}
        <div className="flex bg-white/[0.04] rounded-md overflow-hidden border border-white/[0.06]">
          {LOOKBACK_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setHours(opt.value)}
              className={`px-3 py-1.5 text-xs font-mono transition-colors ${
                hours === opt.value
                  ? "bg-cosmos-600 text-white"
                  : "text-white/40 hover:text-white/60 hover:bg-white/[0.04]"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

      {/* Probability slider */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-white/30 flex items-center gap-1">
          Min conf:
          <span 
            className="cursor-help text-white/20 hover:text-white/50 transition-colors" 
            title="How confident we are about the object type (from spectroscopy or ML). Higher = more certain."
          >
            ⓘ
          </span>
        </span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.1"
          value={minProb}
          onChange={(e) => setMinProb(parseFloat(e.target.value))}
          className="w-20 accent-cosmos-500"
        />
        <span className="text-xs font-mono text-white/50 w-8">
          {(minProb * 100).toFixed(0)}%
        </span>
      </div>
      </div>

      {/* Quick category filters */}
      <div className="flex flex-wrap items-center gap-2">
        {[
          { label: "All", emoji: "🌌", value: "", color: "#748ffc" },
          { label: "Supernova Ia", emoji: "💥", value: "SNIa", color: "#ff6b6b" },
          { label: "Supernova II", emoji: "🌟", value: "SNII", color: "#ffa94d" },
          { label: "Nucleus", emoji: "🌀", value: "AGN", color: "#74c0fc" },
          { label: "Black Hole Event", emoji: "🕳️", value: "TDE", color: "#66d9e8" },
          { label: "Kilonova", emoji: "🔔", value: "KN", color: "#ffd43b" },
          { label: "Nova", emoji: "🔥", value: "CV/Nova", color: "#69db7c" },
          { label: "Blazar", emoji: "🔦", value: "Blazar", color: "#748ffc" },
        ].map((cat) => {
          const isActive = selectedClass === cat.value;
          return (
            <button
              key={cat.value}
              onClick={() => setSelectedClass(cat.value)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs transition-all border"
              style={{
                background: isActive ? cat.color + "25" : "rgba(255,255,255,0.025)",
                borderColor: isActive ? cat.color + "50" : "rgba(255,255,255,0.06)",
                color: isActive ? cat.color : "rgba(255,255,255,0.45)",
              }}
            >
              <span className="text-sm">{cat.emoji}</span>
              {cat.label}
            </button>
          );
        })}
      </div>

      {/* Error state */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-sm text-red-300">
          <p className="font-medium">Failed to load alerts</p>
          <p className="text-red-400/60 mt-1">{error}</p>
          <p className="text-red-400/40 mt-2 text-xs">
            Make sure the backend is running: uvicorn app.main:app --reload --port 8000
          </p>
        </div>
      )}

      {/* Sky Map */}
      <SkyMap
        alerts={alerts}
        onSelectAlert={(alert) => {
          window.location.href = `/alert/${alert.oid}`;
        }}
      />

      {/* Alert cards */}
      <AlertTable alerts={alerts} loading={loading} />

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 pt-2 pb-4">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.06] text-xs text-white/50 hover:text-white/80 hover:bg-white/[0.08] transition-all disabled:opacity-20 disabled:pointer-events-none"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
            Previous
          </button>

          <div className="flex items-center gap-1">
            {Array.from({ length: totalPages }, (_, i) => i + 1)
              .filter((p) => {
                // Show first, last, and pages near current
                if (p === 1 || p === totalPages) return true;
                if (Math.abs(p - page) <= 1) return true;
                return false;
              })
              .reduce((acc, p, i, arr) => {
                // Insert dots between non-consecutive pages
                if (i > 0 && p - arr[i - 1] > 1) {
                  acc.push(
                    <span key={`dot-${p}`} className="text-white/15 text-xs px-1">
                      ...
                    </span>
                  );
                }
                acc.push(
                  <button
                    key={p}
                    onClick={() => setPage(p)}
                    className={`w-8 h-8 rounded-lg text-xs font-mono transition-all ${
                      p === page
                        ? "bg-cosmos-600 text-white"
                        : "text-white/40 hover:text-white/70 hover:bg-white/[0.06]"
                    }`}
                  >
                    {p}
                  </button>
                );
                return acc;
              }, [])}
          </div>

          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.06] text-xs text-white/50 hover:text-white/80 hover:bg-white/[0.08] transition-all disabled:opacity-20 disabled:pointer-events-none"
          >
            Next
            <ChevronRight className="w-3.5 h-3.5" />
          </button>

          <span className="text-[10px] text-white/20 ml-3">
            {total} events total
          </span>
        </div>
      )}

      {/* Footer */}
      <p className="text-[10px] text-white/20 text-center py-4">
        Discoveries from the IAU Transient Name Server (wis-tns.org). Classifications and light curves from ALeRCE (alerce.science) and the Zwicky Transient Facility.
        Rubin Scout is not affiliated with the Vera C. Rubin Observatory.
      </p>
    </div>
  );
}
