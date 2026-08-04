import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft, ChevronRight, Filter, RefreshCw, Activity, Star, Clock } from "lucide-react";
import SkyMap from "../components/SkyMap";
import SourceBadge, { getSourceInfo } from "../components/SourceBadge";
import { getLiveAlerts, getLiveClassifications } from "../lib/api";
import { getClassInfo, getConstellation, formatTimeSince } from "../lib/cosmos";

const PER_PAGE = 50;

// Defaults to "" (All surveys) — never hide either survey by default.
const SURVEY_FILTERS = [
  { label: "All surveys", value: "" },
  { label: "ZTF", value: "ztf_fink" },
  { label: "Rubin / LSST", value: "lsst_fink" },
];

// Survey-aware empty-state copy. A genuinely empty Rubin/LSST result reads
// as ambiguous/broken without context: LSST ingestion isn't on the
// automatic scheduler yet (manual trigger only, see lsst_service.py), so
// "zero alerts" there is an expected, current state, not a failure.
function emptyStateCopy(selectedSurvey) {
  if (selectedSurvey === "lsst_fink") {
    return {
      title: "No Rubin/LSST alerts found.",
      detail:
        "Nothing's broken — Rubin/LSST ingestion isn't on the automatic schedule yet, it only runs when manually triggered, so there may simply be no run since the last one. Switch to \"All surveys\" to see ZTF alerts in the meantime.",
    };
  }
  if (selectedSurvey === "ztf_fink") {
    return {
      title: "No ZTF alerts found.",
      detail: "Try clearing the classification filter, or wait for the next 10:00 UTC ingestion run.",
    };
  }
  return {
    title: "No live alerts found.",
    detail: "Try clearing the survey or classification filter, or wait for the next ingestion run.",
  };
}

// ── Stats bar ─────────────────────────────────────────────────────────────────

function LiveStatsBar({ total, classifications, lastIngested, loading }) {
  if (loading) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="bg-white/5 rounded-lg p-4 animate-pulse h-20" />
        ))}
      </div>
    );
  }

  const top = classifications[0];

  const cards = [
    {
      label: "Live Alerts",
      value: total?.toLocaleString() ?? "—",
      icon: Activity,
      color: "text-cosmos-400",
    },
    {
      label: top ? getClassInfo(top.classification).name : "Top Class",
      value: top ? top.count.toLocaleString() : "—",
      icon: Star,
      color: "text-yellow-400",
    },
    {
      label: "Last Ingested",
      value: lastIngested ? formatTimeSince(lastIngested) : "—",
      icon: Clock,
      color: "text-green-400",
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
      {cards.map((card) => (
        <div
          key={card.label}
          className="bg-white/[0.03] border border-white/[0.06] rounded-lg p-4 flex items-center gap-3"
        >
          <card.icon className={`w-5 h-5 ${card.color} shrink-0`} />
          <div>
            <p className="text-xl font-semibold font-mono">{card.value}</p>
            <p className="text-xs text-white/40">{card.label}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Alert card ────────────────────────────────────────────────────────────────

function LiveAlertCard({ alert }) {
  const info = getClassInfo(alert.classification);
  const constellation = getConstellation(alert.ra, alert.dec);
  const score = alert.classification_score;

  return (
    <Link
      to={`/live-sky/${alert.external_id}`}
      className="group block bg-white/[0.025] hover:bg-white/[0.045] border border-white/[0.06] hover:border-white/[0.12] rounded-xl p-4 transition-all duration-200"
    >
      <div className="flex items-start gap-3 mb-3">
        {/* Emoji badge */}
        <span
          className="text-lg w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
          style={{ background: info.color + "18" }}
        >
          {info.emoji}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-white/85">{info.name}</p>
          <p className="text-[11px] text-white/35 truncate">{info.short}</p>
        </div>
        <SourceBadge alertType={alert.alert_type} />
      </div>

      {/* Coordinates + constellation */}
      <div className="flex items-center gap-4 mb-3">
        <div>
          <p className="text-[10px] text-white/25 uppercase tracking-wider mb-0.5">RA / Dec</p>
          <p className="text-xs font-mono text-white/60">
            {alert.ra?.toFixed(4)}° / {alert.dec?.toFixed(4)}°
          </p>
        </div>
        <div>
          <p className="text-[10px] text-white/25 uppercase tracking-wider mb-0.5">Direction</p>
          <p className="text-xs text-white/50">{constellation}</p>
        </div>
      </div>

      {/* Confidence bar */}
      {score != null && (
        <div className="mb-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-white/25 uppercase tracking-wider">Confidence</span>
            <span className="text-[11px] font-mono text-white/50">
              {(score * 100).toFixed(1)}%
            </span>
          </div>
          <div className="h-1 bg-white/[0.06] rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${Math.min(score * 100, 100)}%`,
                background: info.color,
                opacity: 0.7,
              }}
            />
          </div>
        </div>
      )}

      {/* Detection time + external ID */}
      <div className="flex items-center justify-between">
        <p className="text-[11px] text-white/30">
          {alert.detected_at ? formatTimeSince(alert.detected_at) : "—"}
        </p>
        <p className="text-[10px] font-mono text-white/20 truncate max-w-[120px]">
          {alert.external_id}
        </p>
      </div>
    </Link>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function LiveSky() {
  const [alerts, setAlerts] = useState([]);
  const [total, setTotal] = useState(0);
  const [classifications, setClassifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedClass, setSelectedClass] = useState("");
  const [selectedSurvey, setSelectedSurvey] = useState("");
  const [page, setPage] = useState(1);

  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));

  // Derive last-ingested from the first (most-recent) alert
  const lastIngested = alerts[0]?.ingested_at ?? null;

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const offset = (page - 1) * PER_PAGE;
      const [alertsRes, classRes] = await Promise.all([
        getLiveAlerts({
          limit: PER_PAGE,
          offset,
          ...(selectedClass && { classification: selectedClass }),
          ...(selectedSurvey && { alertType: selectedSurvey }),
        }),
        getLiveClassifications(),
      ]);
      setAlerts(alertsRes.alerts ?? []);
      setTotal(alertsRes.total ?? 0);
      setClassifications(classRes.classifications ?? []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [page, selectedClass, selectedSurvey]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Reset to page 1 when either filter changes
  useEffect(() => {
    setPage(1);
  }, [selectedClass, selectedSurvey]);

  // Changing survey invalidates whatever classification was selected --
  // ZTF's "SN candidate" isn't a valid filter once LSST-only is selected,
  // and vice versa. Server-side filtering only ANDs the two, it can't
  // reconcile a mismatched pair on its own.
  const handleSurveyChange = (value) => {
    setSelectedSurvey(value);
    setSelectedClass("");
  };

  // Auto-refresh every 60 seconds
  useEffect(() => {
    const id = setInterval(fetchData, 60_000);
    return () => clearInterval(id);
  }, [fetchData]);

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Live Alert Stream</h1>
          <p className="text-sm text-white/40 mt-1">
            Real-time transient alerts from Fink's ZTF and Rubin/LSST alert streams. ZTF updates
            daily at 10:00 UTC; Rubin/LSST is ingested on demand ahead of scheduled ingestion.
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
      <LiveStatsBar
        total={total}
        classifications={classifications}
        lastIngested={lastIngested}
        loading={loading}
      />

      {/* Survey filter buttons — server-side via alert_type, same pattern as
          GW's significance filter. Selecting a survey resets the
          classification filter (see handleSurveyChange). */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5">
          <Filter className="w-3.5 h-3.5 text-white/30" />
          <span className="text-xs text-white/30 uppercase tracking-wider">Survey</span>
        </div>

        {SURVEY_FILTERS.map((f) => {
          const isActive = selectedSurvey === f.value;
          const color = f.value ? getSourceInfo(f.value).color : "#748ffc";
          return (
            <button
              key={f.value}
              onClick={() => handleSurveyChange(f.value)}
              className="px-3 py-1.5 rounded-full text-xs transition-all border"
              style={{
                background: isActive ? color + "25" : "rgba(255,255,255,0.025)",
                borderColor: isActive ? color + "50" : "rgba(255,255,255,0.06)",
                color: isActive ? color : "rgba(255,255,255,0.45)",
              }}
            >
              {f.label}
            </button>
          );
        })}
      </div>

      {/* Classification filter buttons — options narrow to the selected
          survey; in "All surveys" each pill gets a small source-colored dot
          so ZTF and LSST/Rubin labels stay visibly grouped, not silently
          mixed (the two use incompatible classification vocabularies). */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5">
          <Filter className="w-3.5 h-3.5 text-white/30" />
          <span className="text-xs text-white/30 uppercase tracking-wider">Classification</span>
        </div>

        {[
          { label: "All", value: "", emoji: "🌌", color: "#748ffc", dot: null },
          ...classifications
            .filter((c) => !selectedSurvey || c.alert_type === selectedSurvey)
            .map((c) => {
              const info = getClassInfo(c.classification);
              return {
                label: info.name,
                value: c.classification,
                emoji: info.emoji,
                color: info.color,
                count: c.count,
                dot: !selectedSurvey ? getSourceInfo(c.alert_type).color : null,
              };
            }),
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
              {cat.dot && (
                <span
                  className="w-1.5 h-1.5 rounded-full shrink-0"
                  style={{ background: cat.dot }}
                />
              )}
              <span className="text-sm">{cat.emoji}</span>
              {cat.label}
              {cat.count != null && (
                <span className="ml-1 opacity-50 font-mono text-[10px]">{cat.count}</span>
              )}
            </button>
          );
        })}
      </div>

      {/* Error state */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-sm text-red-300">
          <p className="font-medium">Failed to load live alerts</p>
          <p className="text-red-400/60 mt-1">{error}</p>
          <p className="text-red-400/40 mt-2 text-xs">
            Make sure the backend is running: uvicorn app.main:app --reload --port 8000
          </p>
        </div>
      )}

      {/* Sky Map — no click-through (live alerts have no detail page yet) */}
      <SkyMap
        alerts={alerts}
        page={page}
        totalPages={totalPages}
        total={total}
        onSelectAlert={null}
      />

      {/* Alert cards grid */}
      {loading ? (
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="bg-white/[0.03] rounded-xl p-5 animate-pulse h-40" />
          ))}
        </div>
      ) : alerts.length === 0 ? (
        <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-12 text-center">
          <p className="text-2xl mb-2">📡</p>
          <p className="text-white/40">{emptyStateCopy(selectedSurvey).title}</p>
          <p className="text-white/20 text-sm mt-1">
            {emptyStateCopy(selectedSurvey).detail}
          </p>
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {alerts.map((alert) => (
            <LiveAlertCard key={alert.id} alert={alert} />
          ))}
        </div>
      )}

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
              .filter((p) => p === 1 || p === totalPages || Math.abs(p - page) <= 1)
              .reduce((acc, p, i, arr) => {
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

          <span className="text-[10px] text-white/20 ml-3">{total} alerts total</span>
        </div>
      )}

      {/* Footer */}
      <p className="text-[10px] text-white/20 text-center py-4">
        Live alerts sourced from the Fink broker (Möller et al. 2021, MNRAS 501, 3272), processing
        both the Zwicky Transient Facility (ZTF) alert stream (SuperNNova and random-forest
        classifiers) and, via Fink's separate LSST deployment, the Vera C. Rubin Observatory's
        Legacy Survey of Space and Time (tag-based discovery, SuperNNova and CATS classifiers).
      </p>
    </div>
  );
}
