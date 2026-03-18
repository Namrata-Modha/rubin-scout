import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Search, Filter, RefreshCw } from "lucide-react";
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

export default function Dashboard() {
  const [alerts, setAlerts] = useState([]);
  const [stats, setStats] = useState(null);
  const [classifications, setClassifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Filters
  const [selectedClass, setSelectedClass] = useState("");
  const [hours, setHours] = useState(24);
  const [minProb, setMinProb] = useState(0.5);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [alertsRes, statsRes, classRes] = await Promise.all([
        getRecentAlerts({
          classification: selectedClass || null,
          minProbability: minProb,
          hours,
          limit: 100,
        }),
        getSummaryStats(hours),
        getClassifications(),
      ]);
      setAlerts(alertsRes.alerts || []);
      setStats(statsRes);
      setClassifications(classRes.classifications || []);
    } catch (err) {
      setError(err.message);
      console.error("Failed to fetch data:", err);
    } finally {
      setLoading(false);
    }
  }, [selectedClass, hours, minProb]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

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
            Transient Alerts
          </h1>
          <p className="text-sm text-white/40 mt-1">
            Real-time filtered alerts from ZTF and Rubin/LSST via ALeRCE
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

        {/* Classification filter */}
        <select
          value={selectedClass}
          onChange={(e) => setSelectedClass(e.target.value)}
          className="bg-white/[0.04] border border-white/[0.06] rounded-md px-3 py-1.5 text-xs font-mono text-white/70 appearance-none cursor-pointer"
        >
          <option value="">All classes</option>
          {classifications.map((cls) => (
            <option key={cls.name} value={cls.name}>
              {cls.name} ({cls.count})
            </option>
          ))}
        </select>

        {/* Probability slider */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-white/30">Min conf:</span>
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

      {/* Sky Map + Alert Table layout */}
      <SkyMap
        alerts={alerts}
        onSelectAlert={(alert) => {
          window.location.href = `/alert/${alert.oid}`;
        }}
      />

      {/* Alert table */}
      <AlertTable alerts={alerts} loading={loading} />

      {/* Footer info */}
      <p className="text-[10px] text-white/20 text-center py-4">
        Data sourced from ALeRCE (alerce.science) and Pitt-Google brokers.
        Rubin Scout is not affiliated with the Vera C. Rubin Observatory.
      </p>
    </div>
  );
}
