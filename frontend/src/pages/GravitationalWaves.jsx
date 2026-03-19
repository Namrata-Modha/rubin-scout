import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { Waves, Search, Loader2, ExternalLink, AlertCircle } from "lucide-react";
import { getGWEvents, crossMatchGWEvent, seedGWEvents } from "../lib/api";
import { getClassInfo, getConstellation } from "../lib/cosmos";

const TYPE_STYLES = {
  BNS: { emoji: "🔔", color: "#ffd43b", label: "Neutron Stars Colliding" },
  NSBH: { emoji: "🕳️", color: "#66d9e8", label: "Black Hole Eats Neutron Star" },
  BBH: { emoji: "⚫", color: "#748ffc", label: "Black Holes Merging" },
  Terrestrial: { emoji: "❌", color: "#868e96", label: "False Alarm" },
};

export default function GravitationalWaves() {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [candidates, setCandidates] = useState(null);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        let res = await getGWEvents();
        if (!res.events || res.events.length === 0) {
          // Auto-seed on first visit
          await seedGWEvents();
          res = await getGWEvents();
        }
        setEvents(res.events || []);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const handleCrossMatch = async (supereventId) => {
    setSearching(true);
    setCandidates(null);
    setSelectedEvent(supereventId);
    try {
      const res = await crossMatchGWEvent(supereventId);
      setCandidates(res);
    } catch (err) {
      setCandidates({ error: err.message, candidates: [] });
    } finally {
      setSearching(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 text-white/30 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      {/* Header */}
      <div>
        <div className="flex items-center gap-3 mb-2">
          <Waves className="w-6 h-6 text-indigo-400" />
          <h1 className="text-2xl font-semibold tracking-tight">
            Gravitational Wave Events
          </h1>
        </div>
        <p className="text-sm text-white/40 max-w-2xl">
          When two massive objects collide in space, they send ripples through
          spacetime itself. LIGO and Virgo detect these waves, and Rubin Scout
          searches for the flash of light from the collision.
        </p>
      </div>

      {/* How it works explainer */}
      <div className="bg-indigo-500/5 border border-indigo-500/10 rounded-xl p-4">
        <p className="text-xs text-indigo-300/60 leading-relaxed">
          <span className="font-medium text-indigo-300/80">How multi-messenger astronomy works:</span> LIGO
          detects gravitational waves and estimates a region of sky where the collision happened.
          Rubin Scout then searches that region for new transient events (like a kilonova flash)
          in our alert database. Finding both the gravitational wave AND the light from the
          same event is one of the most exciting things in modern astronomy.
        </p>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Event cards */}
      <div className="space-y-3">
        {events.map((evt) => {
          const style = TYPE_STYLES[evt.type_key] || TYPE_STYLES.BBH;
          const isSelected = selectedEvent === evt.superevent_id;

          return (
            <div key={evt.superevent_id}>
              <div
                className={`rounded-xl border p-5 transition-all cursor-pointer ${
                  isSelected
                    ? "bg-white/[0.04] border-white/[0.12]"
                    : "bg-white/[0.025] border-white/[0.06] hover:bg-white/[0.035] hover:border-white/[0.08]"
                }`}
                onClick={() =>
                  isSelected ? setSelectedEvent(null) : setSelectedEvent(evt.superevent_id)
                }
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-3">
                    <span
                      className="text-xl w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
                      style={{ background: style.color + "15" }}
                    >
                      {style.emoji}
                    </span>
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="text-sm font-medium text-white/85">
                          {evt.superevent_id}
                        </h3>
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded"
                          style={{
                            background: style.color + "18",
                            color: style.color,
                          }}
                        >
                          {style.label}
                        </span>
                      </div>
                      <p className="text-xs text-white/40 mt-1 max-w-xl leading-relaxed">
                        {evt.description
                          ? evt.description.slice(0, 200) + (evt.description.length > 200 ? "..." : "")
                          : "Gravitational wave event"}
                      </p>
                    </div>
                  </div>

                  <div className="text-right shrink-0 ml-4">
                    <p className="text-[10px] text-white/25">
                      {evt.event_time
                        ? new Date(evt.event_time).toLocaleDateString("en-US", {
                            month: "short",
                            day: "numeric",
                            year: "numeric",
                          })
                        : ""}
                    </p>
                    <div className="flex items-center gap-3 mt-1 text-[10px] text-white/20">
                      {evt.distance_mpc && (
                        <span>{evt.distance_mpc} Mpc</span>
                      )}
                      {evt.area_90_deg2 && (
                        <span>{evt.area_90_deg2} deg\u00B2</span>
                      )}
                    </div>
                  </div>
                </div>

                {/* Quick stats row */}
                <div className="flex items-center gap-4 mt-3 text-[10px] text-white/25">
                  {evt.distance_mpc && (
                    <span>
                      📏 {evt.distance_mpc} million light-years away
                      {evt.distance_err_mpc ? ` (±${evt.distance_err_mpc})` : ""}
                    </span>
                  )}
                  {evt.area_90_deg2 && (
                    <span>📐 Sky area: {evt.area_90_deg2} square degrees</span>
                  )}
                  {evt.n_candidates > 0 && (
                    <span>🔭 {evt.n_candidates} candidate counterpart(s) found</span>
                  )}
                </div>
              </div>

              {/* Expanded: cross-match panel */}
              {isSelected && (
                <div className="mt-2 bg-white/[0.02] border border-white/[0.06] rounded-xl p-5">
                  {evt.description && (
                    <p className="text-xs text-white/40 leading-relaxed mb-4">
                      {evt.description}
                    </p>
                  )}

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleCrossMatch(evt.superevent_id);
                    }}
                    disabled={searching}
                    className="flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-500/20 hover:bg-indigo-500/30 border border-indigo-500/30 text-sm text-indigo-300 transition-all disabled:opacity-40"
                  >
                    {searching ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Search className="w-4 h-4" />
                    )}
                    Search for optical counterparts
                  </button>

                  {/* Results */}
                  {candidates && candidates.superevent_id === evt.superevent_id && (
                    <div className="mt-4">
                      {candidates.error ? (
                        <div className="flex items-center gap-2 text-xs text-red-300/60">
                          <AlertCircle className="w-3.5 h-3.5" />
                          {candidates.error}
                        </div>
                      ) : candidates.candidates.length === 0 ? (
                        <div className="text-xs text-white/30 p-4 text-center bg-white/[0.02] rounded-lg">
                          No optical counterpart candidates found in the search region.
                          {!evt.ra_center && " (This event was poorly localized, so we searched by time window only.)"}
                        </div>
                      ) : (
                        <div className="space-y-2">
                          <p className="text-xs text-white/40">
                            Found {candidates.candidates.length} candidate counterpart(s):
                          </p>
                          {candidates.candidates.map((c) => {
                            const info = getClassInfo(c.classification);
                            return (
                              <Link
                                key={c.oid}
                                to={`/alert/${c.oid}`}
                                onClick={(e) => e.stopPropagation()}
                                className="flex items-center justify-between p-3 rounded-lg bg-white/[0.025] hover:bg-white/[0.04] border border-white/[0.05] transition-all"
                              >
                                <div className="flex items-center gap-2.5">
                                  <span className="text-sm">{info.emoji}</span>
                                  <div>
                                    <p className="text-xs font-medium text-white/70">
                                      {info.name}
                                    </p>
                                    <p className="text-[10px] text-white/30">
                                      {c.oid} · {c.n_detections} observations
                                      {c.distance_deg != null && ` · ${c.distance_deg}° from center`}
                                    </p>
                                  </div>
                                </div>
                                <div className="text-right">
                                  {c.in_90_region && (
                                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-green-500/15 text-green-400/80 border border-green-500/20">
                                      In 90% region
                                    </span>
                                  )}
                                </div>
                              </Link>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {events.length === 0 && !loading && (
        <div className="text-center py-12 text-white/30">
          <Waves className="w-8 h-8 mx-auto mb-3 opacity-30" />
          <p>No gravitational wave events loaded yet.</p>
        </div>
      )}

      <p className="text-[10px] text-white/15 text-center py-4">
        GW event data from LIGO/Virgo/KAGRA GWTC catalogs via GraceDB.
        Cross-matching uses angular distance from skymap centroid.
      </p>
    </div>
  );
}
