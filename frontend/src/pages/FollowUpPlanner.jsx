/**
 * Transient Follow-Up Planner (/followup)
 *
 * Gives the ARIES / ILMT team everything needed to triage a sky position:
 * ZTF transient history, SIMBAD cross-match, GW coincidences, and
 * observatory visibility for tonight — all via GET /api/ilmt/followup.
 */
import { useState, useEffect, useMemo, useRef } from "react";
import { Target, Search, Eye, EyeOff, Moon, ExternalLink, Copy, Check } from "lucide-react";
import ClassBadge from "../components/ClassBadge";
import { getObservatories, getIlmtFollowup } from "../lib/api";
import { formatUTC, formatDate } from "../lib/cosmos";

// Render backend URL used in the reproducible curl command so researchers
// can copy-paste it into their own scripts outside the browser.
const API_BASE =
  import.meta.env.VITE_API_URL || "https://rubin-scout-api.onrender.com";

// Sentinel value for the "custom coordinates" observatory option
const CUSTOM_KEY = "custom";

// Stable reference — avoids reallocating the array on every render
const SKELETON_ITEMS = [0, 1, 2, 3];

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function RecommendationBanner({ recommendation, reason }) {
  const styles = {
    PRIORITY_FOLLOWUP: {
      bg: "bg-green-500/10",
      border: "border-green-500/25",
      text: "text-green-300",
      dot: "bg-green-400",
      label: "Priority Follow-Up",
    },
    NEEDS_MORE_DATA: {
      bg: "bg-orange-500/10",
      border: "border-orange-500/25",
      text: "text-orange-300",
      dot: "bg-orange-400",
      label: "Needs More Data",
    },
    LIKELY_KNOWN: {
      bg: "bg-white/[0.04]",
      border: "border-white/[0.1]",
      text: "text-white/55",
      dot: "bg-white/30",
      label: "Likely Known Source",
    },
  };
  const s = styles[recommendation] ?? styles.NEEDS_MORE_DATA;

  return (
    <div className={`rounded-xl border px-5 py-4 ${s.bg} ${s.border}`}>
      <div className="flex items-center gap-2.5 mb-2">
        <span className={`w-2 h-2 rounded-full shrink-0 ${s.dot}`} />
        <span className={`text-sm font-semibold tracking-wider uppercase ${s.text}`}>
          {s.label}
        </span>
      </div>
      <p className="text-sm text-white/55 leading-relaxed">{reason}</p>
    </div>
  );
}

function Tag({ color, label }) {
  const palettes = {
    blue: "bg-blue-500/15 border-blue-500/25 text-blue-300",
    green: "bg-green-500/15 border-green-500/25 text-green-300",
    purple: "bg-purple-500/15 border-purple-500/25 text-purple-300",
    gray: "bg-white/[0.06] border-white/[0.1] text-white/40",
  };
  return (
    <span
      className={`inline-block text-[10px] px-1.5 py-0.5 rounded border ${palettes[color] ?? palettes.gray}`}
    >
      {label}
    </span>
  );
}

function ZTFCard({ history }) {
  if (!history || history.length === 0) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-white/40">
          No prior ZTF history at this position in the Rubin Scout database.
        </p>
        <p className="text-xs text-white/25">
          For complete ZTF coverage, query{" "}
          <a
            href="https://alerce.online"
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:text-white/40 transition-colors"
          >
            ALeRCE
          </a>{" "}
          directly.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {history.map((obj) => (
        <div
          key={obj.oid}
          className="pb-3 border-b border-white/[0.05] last:border-0 last:pb-0 space-y-1.5"
        >
          {/* Classification + activity tags */}
          <div className="flex flex-wrap items-center gap-1.5">
            <ClassBadge
              classification={obj.classification}
              probability={obj.classification_probability}
            />
            {obj.pre_existing && <Tag color="blue" label="pre-existing" />}
            {obj.new_activity && <Tag color="green" label="new activity" />}
          </div>

          {/* Metadata */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-white/35">
            <span className="font-mono">{obj.oid}</span>
            <span>·</span>
            <span>{obj.n_detections} detections</span>
            <span>·</span>
            <span>{obj.distance_arcsec}&Prime; away</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-white/22">
            <span>First: {formatDate(obj.first_detection)}</span>
            <span>·</span>
            <span>Last: {formatDate(obj.last_detection)}</span>
          </div>

          {/* ALeRCE link */}
          <a
            href={obj.alerce_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[11px] text-cosmos-400/60 hover:text-cosmos-400 transition-colors"
          >
            View on ALeRCE <ExternalLink className="w-2.5 h-2.5" />
          </a>
        </div>
      ))}

      <p className="text-[10px] text-white/20 pt-1">
        For complete ZTF coverage, query{" "}
        <a
          href="https://alerce.online"
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:text-white/30 transition-colors"
        >
          ALeRCE
        </a>{" "}
        directly.
      </p>
    </div>
  );
}

function SimbadCard({ simbad }) {
  if (!simbad || !simbad.name) {
    return (
      <p className="text-sm text-white/40">
        No known catalog object within search radius.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-x-4 gap-y-2.5">
        <div>
          <p className="text-[10px] text-white/25 uppercase tracking-wider mb-0.5">Name</p>
          <p className="text-sm text-white/80 font-mono break-all">{simbad.name}</p>
        </div>
        <div>
          <p className="text-[10px] text-white/25 uppercase tracking-wider mb-0.5">Object type</p>
          <p className="text-sm text-white/70">{simbad.type || "—"}</p>
        </div>
        <div>
          <p className="text-[10px] text-white/25 uppercase tracking-wider mb-0.5">Separation</p>
          <p className="text-sm text-white/70">
            {simbad.distance_arcsec != null
              ? `${simbad.distance_arcsec.toFixed(2)}"`
              : "—"}
          </p>
        </div>
      </div>

      <a
        href={`https://simbad.u-strasbg.fr/simbad/sim-id?Ident=${encodeURIComponent(
          simbad.name
        )}`}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 text-[11px] text-cosmos-400/60 hover:text-cosmos-400 transition-colors"
      >
        View in SIMBAD <ExternalLink className="w-2.5 h-2.5" />
      </a>
    </div>
  );
}

function GWCard({ coincidences }) {
  if (!coincidences || coincidences.length === 0) {
    return (
      <p className="text-sm text-white/40">
        No coincident GW events in the 30 days preceding this observation.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {coincidences.map((ev) => {
        // Pick the dominant classification type by probability
        const cls =
          ev.classification && typeof ev.classification === "object"
            ? Object.entries(ev.classification).sort((a, b) => b[1] - a[1])[0]
            : null;

        return (
          <div
            key={ev.superevent_id}
            className="pb-3 border-b border-white/[0.05] last:border-0 last:pb-0 space-y-1"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-mono text-white/80">
                {ev.superevent_id}
              </span>
              {ev.localized ? (
                <Tag color="purple" label="localized" />
              ) : (
                <Tag color="gray" label="unlocalized" />
              )}
            </div>

            <p className="text-[11px] text-white/35">
              {ev.event_time
                ? new Date(ev.event_time).toUTCString().slice(0, 25)
                : "—"}
            </p>

            {cls && (
              <p className="text-[11px] text-white/30">
                Dominant type:{" "}
                <span className="text-white/50">{cls[0]}</span>{" "}
                ({(cls[1] * 100).toFixed(0)}%)
              </p>
            )}

            {ev.separation_deg != null && (
              <p className="text-[11px] text-white/25">
                {ev.separation_deg}° from centroid &mdash; 90% region radius:{" "}
                {ev.credible_radius_deg}°
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function VisibilityResultCard({ vis }) {
  if (!vis) {
    return <p className="text-sm text-white/40">Visibility data unavailable.</p>;
  }

  const { observable } = vis;

  return (
    <div className="space-y-3">
      {/* Observable badge — same style as VisibilityCard.jsx */}
      <div
        className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-medium"
        style={{
          background: observable ? "#51cf6620" : "#ff6b6b20",
          color: observable ? "#51cf66" : "#ff6b6b",
          border: `1px solid ${observable ? "#51cf6640" : "#ff6b6b40"}`,
        }}
      >
        {observable ? (
          <Eye className="w-3.5 h-3.5" />
        ) : (
          <EyeOff className="w-3.5 h-3.5" />
        )}
        {observable ? "Observable tonight" : "Not observable tonight"}
      </div>

      {/* Key metrics grid */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
        <div>
          <p className="text-white/25 uppercase tracking-wider text-[10px] mb-0.5">
            Peak altitude
          </p>
          <p className="text-white/65">{vis.max_altitude ?? "—"}°</p>
        </div>
        <div>
          <p className="text-white/25 uppercase tracking-wider text-[10px] mb-0.5">
            Observable hours
          </p>
          <p className="text-white/65">{vis.observable_hours ?? "—"} hr</p>
        </div>
        <div>
          <p className="text-white/25 uppercase tracking-wider text-[10px] mb-0.5">
            Dark window
          </p>
          <p className="text-white/65">
            {formatUTC(vis.dark_start)} &ndash; {formatUTC(vis.dark_end)}
          </p>
        </div>
        {vis.moon_separation != null && (
          <div>
            <p className="text-white/25 uppercase tracking-wider text-[10px] mb-0.5">
              Moon separation
            </p>
            <div className="flex items-center gap-1 text-white/65">
              <Moon className="w-3 h-3 text-yellow-400/50" />
              {vis.moon_separation}°
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const INPUT_CLS =
  "w-full bg-white/[0.05] border border-white/[0.1] rounded-lg px-3 py-2 text-sm text-white/80 placeholder-white/20 focus:outline-none focus:border-cosmos-500/50 transition-colors";
const LABEL_CLS = "block text-[11px] text-white/35 uppercase tracking-wider mb-1.5";

export default function FollowUpPlanner() {
  const [presets, setPresets] = useState({});
  const [form, setForm] = useState({
    ra: "",
    dec: "",
    mjd: "",
    radius_arcsec: "5.0",
    observatory_key: "devasthal",
    obs_lat: "",
    obs_lon: "",
    obs_elevation: "0",
  });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  // Load observatory presets on mount
  useEffect(() => {
    getObservatories()
      .then((res) => setPresets(res.observatories || {}))
      .catch(() => {}); // silently degrade; presets are optional
  }, []);

  const isCustom = form.observatory_key === CUSTOM_KEY;

  function updateForm(field, val) {
    setForm((prev) => ({ ...prev, [field]: val }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const params = {
        ra: parseFloat(form.ra),
        dec: parseFloat(form.dec),
        mjd: parseFloat(form.mjd),
        radiusArcsec: parseFloat(form.radius_arcsec) || 5.0,
        observatoryKey: form.observatory_key,
      };
      if (isCustom) {
        params.obsLat = parseFloat(form.obs_lat);
        params.obsLon = parseFloat(form.obs_lon);
        params.obsElevation = parseFloat(form.obs_elevation) || 0;
      }
      const res = await getIlmtFollowup(params);
      setResult(res);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const curlUrl = useMemo(() => {
    if (!result) return "";
    const p = new URLSearchParams({
      ra: result.query.ra,
      dec: result.query.dec,
      mjd: result.query.mjd,
      radius_arcsec: result.query.radius_arcsec,
    });
    if (form.observatory_key && form.observatory_key !== CUSTOM_KEY)
      p.set("observatory_key", form.observatory_key);
    if (isCustom) {
      if (form.obs_lat) p.set("obs_lat", form.obs_lat);
      if (form.obs_lon) p.set("obs_lon", form.obs_lon);
      if (form.obs_elevation) p.set("obs_elevation", form.obs_elevation);
    }
    return `${API_BASE}/api/ilmt/followup?${p}`;
  }, [result, form, isCustom]);

  // Store the timeout ID so we can cancel it if the component unmounts early
  const copiedTimerRef = useRef(null);
  function copyCmd() {
    navigator.clipboard.writeText(`curl "${curlUrl}"`).catch(() => {});
    setCopied(true);
    clearTimeout(copiedTimerRef.current);
    copiedTimerRef.current = setTimeout(() => setCopied(false), 2000);
  }
  useEffect(() => () => clearTimeout(copiedTimerRef.current), []);

  // Backend returns the resolved name; fall back to the form selection for
  // the pre-result state (observatory label shown before first query).
  const visObsName =
    result?.visibility_devasthal?.observatory_name ??
    (isCustom ? "Custom location" : presets[form.observatory_key]?.name ?? form.observatory_key);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Page header */}
      <div className="flex items-start gap-3">
        <Target className="w-5 h-5 text-cosmos-400 mt-1 shrink-0" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Transient Follow-Up Planner
          </h1>
          <p className="text-sm text-white/40 mt-0.5 leading-relaxed">
            ARIES / ILMT observing decision support &mdash; cross-matches a sky
            position against ZTF transient history, SIMBAD, and LIGO/Virgo/KAGRA
            events, then gives a go/no-go recommendation for tonight.
          </p>
        </div>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Query form                                                           */}
      {/* ------------------------------------------------------------------ */}
      <form
        onSubmit={handleSubmit}
        className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-5 space-y-5"
      >
        {/* Coordinate + epoch row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <label className={LABEL_CLS}>Right Ascension (°)</label>
            <input
              type="number"
              min="0"
              max="360"
              step="any"
              placeholder="83.82"
              value={form.ra}
              onChange={(e) => updateForm("ra", e.target.value)}
              required
              className={INPUT_CLS}
            />
          </div>
          <div>
            <label className={LABEL_CLS}>Declination (°)</label>
            <input
              type="number"
              min="-90"
              max="90"
              step="any"
              placeholder="22.01"
              value={form.dec}
              onChange={(e) => updateForm("dec", e.target.value)}
              required
              className={INPUT_CLS}
            />
          </div>
          <div>
            <label className={LABEL_CLS}>
              MJD{" "}
              <span className="text-white/20 normal-case font-normal tracking-normal">
                (observation epoch)
              </span>
            </label>
            <input
              type="number"
              min="40000"
              max="80000"
              step="any"
              placeholder="60300"
              value={form.mjd}
              onChange={(e) => updateForm("mjd", e.target.value)}
              required
              className={INPUT_CLS}
            />
          </div>
          <div>
            <label className={LABEL_CLS}>
              Search radius{" "}
              <span className="text-white/20 normal-case font-normal tracking-normal">
                (arcsec)
              </span>
            </label>
            <input
              type="number"
              min="0.5"
              max="300"
              step="any"
              placeholder="5.0"
              value={form.radius_arcsec}
              onChange={(e) => updateForm("radius_arcsec", e.target.value)}
              className={INPUT_CLS}
            />
          </div>
        </div>

        {/* Observatory selector */}
        <div>
          <label className={LABEL_CLS}>Observatory</label>
          <select
            value={form.observatory_key}
            onChange={(e) => updateForm("observatory_key", e.target.value)}
            className="bg-white/[0.05] border border-white/[0.1] rounded-lg px-3 py-2 text-sm text-white/80 focus:outline-none focus:border-cosmos-500/50 transition-colors min-w-[260px]"
            style={{
              backgroundColor: "#1c1c2e",
              color: "rgba(255,255,255,0.75)",
              borderColor: "rgba(255,255,255,0.12)",
              colorScheme: "dark",
            }}
          >
            {Object.entries(presets).map(([key, obs]) => (
              <option key={key} value={key}>
                {obs.name} — {obs.location}
              </option>
            ))}
            <option value={CUSTOM_KEY}>Custom coordinates…</option>
          </select>
        </div>

        {/* Custom coords — shown only when "custom" selected */}
        {isCustom && (
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className={LABEL_CLS}>Latitude (°N)</label>
              <input
                type="number"
                min="-90"
                max="90"
                step="any"
                placeholder="29.36"
                value={form.obs_lat}
                onChange={(e) => updateForm("obs_lat", e.target.value)}
                required
                className={INPUT_CLS}
              />
            </div>
            <div>
              <label className={LABEL_CLS}>Longitude (°E)</label>
              <input
                type="number"
                min="-180"
                max="180"
                step="any"
                placeholder="79.69"
                value={form.obs_lon}
                onChange={(e) => updateForm("obs_lon", e.target.value)}
                required
                className={INPUT_CLS}
              />
            </div>
            <div>
              <label className={LABEL_CLS}>Elevation (m)</label>
              <input
                type="number"
                min="-500"
                max="5000"
                step="any"
                placeholder="0"
                value={form.obs_elevation}
                onChange={(e) => updateForm("obs_elevation", e.target.value)}
                className={INPUT_CLS}
              />
            </div>
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-cosmos-600 hover:bg-cosmos-500 text-white text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Search className="w-4 h-4" />
          {loading ? "Computing…" : "Run Follow-Up Query"}
        </button>
      </form>

      {/* ------------------------------------------------------------------ */}
      {/* Error state                                                          */}
      {/* ------------------------------------------------------------------ */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-sm text-red-300">
          <p className="font-medium">Query failed</p>
          <p className="text-red-400/60 mt-1">{error}</p>
          <p className="text-red-400/40 mt-2 text-xs">
            Make sure the backend is running and reachable.
          </p>
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Loading skeleton                                                     */}
      {/* ------------------------------------------------------------------ */}
      {loading && (
        <div className="space-y-4 animate-pulse">
          <div className="h-20 bg-white/[0.04] rounded-xl" />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {SKELETON_ITEMS.map((i) => (
              <div key={i} className="h-52 bg-white/[0.04] rounded-xl" />
            ))}
          </div>
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Results                                                              */}
      {/* ------------------------------------------------------------------ */}
      {result && !loading && (
        <>
          {/* Recommendation banner */}
          <RecommendationBanner
            recommendation={result.recommendation}
            reason={result.recommendation_reason}
          />

          {/* 2×2 card grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* ZTF History */}
            <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-5">
              <h3 className="text-[11px] font-medium text-white/40 uppercase tracking-wider mb-4 flex items-center gap-2">
                <span>🔭</span>
                ZTF History
                <span className="ml-auto text-white/20 normal-case font-normal">
                  {result.ztf_history.length} match
                  {result.ztf_history.length !== 1 ? "es" : ""}
                </span>
              </h3>
              <ZTFCard history={result.ztf_history} />
            </div>

            {/* SIMBAD */}
            <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-5">
              <h3 className="text-[11px] font-medium text-white/40 uppercase tracking-wider mb-4 flex items-center gap-2">
                <span>📚</span>
                SIMBAD Cross-match
              </h3>
              <SimbadCard simbad={result.simbad} />
            </div>

            {/* GW Coincidence */}
            <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-5">
              <h3 className="text-[11px] font-medium text-white/40 uppercase tracking-wider mb-4 flex items-center gap-2">
                <span>🌊</span>
                GW Coincidence
                <span className="ml-auto text-white/20 normal-case font-normal">
                  ±30 days
                </span>
              </h3>
              <GWCard coincidences={result.gw_coincidence} />
            </div>

            {/* Observatory Visibility */}
            <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-5">
              <h3 className="text-[11px] font-medium text-white/40 uppercase tracking-wider mb-1 flex items-center gap-2">
                <Eye className="w-3.5 h-3.5" />
                Observatory Visibility
              </h3>
              <p className="text-[11px] text-white/28 mb-4">{visObsName}</p>
              <VisibilityResultCard vis={result.visibility_devasthal} />
            </div>
          </div>

          {/* Reproducible curl command */}
          <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-4">
            <div className="flex items-center justify-between mb-2">
              <p className="text-[11px] text-white/30 uppercase tracking-wider">
                Reproducible query
              </p>
              <button
                onClick={copyCmd}
                className="flex items-center gap-1.5 text-[11px] text-white/30 hover:text-white/55 transition-colors"
              >
                {copied ? (
                  <Check className="w-3 h-3 text-green-400" />
                ) : (
                  <Copy className="w-3 h-3" />
                )}
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>
            <pre className="text-[11px] font-mono text-white/50 overflow-x-auto whitespace-pre-wrap break-all leading-relaxed select-all">
              {`curl "${curlUrl}"`}
            </pre>
          </div>
        </>
      )}
    </div>
  );
}
