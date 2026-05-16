import { useState, useEffect } from "react";
import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { Eye, EyeOff, ChevronDown, Moon } from "lucide-react";
import { getVisibility, getObservatories } from "../lib/api";

const DEFAULT_PRESET_KEY = "devasthal";

function formatHour(isoString) {
  try {
    return new Date(isoString).toUTCString().slice(17, 22) + " UTC";
  } catch {
    return isoString;
  }
}

function CustomDot({ cx, cy, payload }) {
  const color = payload.altitude > 30 ? "#51cf66" : "#ff6b6b";
  return <circle cx={cx} cy={cy} r={3} fill={color} stroke="none" />;
}

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  return (
    <div className="bg-black/80 border border-white/10 rounded-lg px-3 py-2 text-xs">
      <p className="text-white/60">{formatHour(d.time)}</p>
      <p style={{ color: d.altitude > 30 ? "#51cf66" : "#ff6b6b" }}>
        Altitude: {d.altitude.toFixed(1)}°
      </p>
      <p className="text-yellow-400/60">Sun: {d.sun_altitude.toFixed(1)}°</p>
    </div>
  );
}

export default function VisibilityCard({ oid }) {
  const [open, setOpen] = useState(false);
  const [presets, setPresets] = useState({});
  const [selectedKey, setSelectedKey] = useState(DEFAULT_PRESET_KEY);
  const [custom, setCustom] = useState({ lat: "", lon: "", elevation: "0" });
  const [useCustom, setUseCustom] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // When the card opens (or selection changes), load presets if needed then
  // immediately fetch visibility — sequentially so the preset is always
  // available before we try to read its coordinates.
  useEffect(() => {
    if (!open) return;

    async function run() {
      // Ensure presets are loaded first
      let activePresets = presets;
      if (Object.keys(activePresets).length === 0) {
        try {
          const res = await getObservatories();
          activePresets = res.observatories || {};
          setPresets(activePresets);
        } catch {
          return; // can't proceed without presets
        }
      }

      // Derive coordinates from preset or custom input
      let lat, lon, elevation;
      if (useCustom) {
        lat = parseFloat(custom.lat);
        lon = parseFloat(custom.lon);
        elevation = parseFloat(custom.elevation) || 0;
        if (isNaN(lat) || isNaN(lon)) return;
      } else {
        const preset = activePresets[selectedKey];
        if (!preset) return;
        lat = preset.lat;
        lon = preset.lon;
        elevation = preset.elevation_m ?? 0;
      }

      setLoading(true);
      setError(null);
      try {
        const result = await getVisibility(oid, { lat, lon, elevation });
        console.log("[VisibilityCard] API response:", result);
        setData(result);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }

    run();
  }, [open, selectedKey, useCustom]);

  async function fetchVisibility() {
    // Called by the Refresh button — presets are guaranteed loaded by then
    let lat, lon, elevation;
    if (useCustom) {
      lat = parseFloat(custom.lat);
      lon = parseFloat(custom.lon);
      elevation = parseFloat(custom.elevation) || 0;
      if (isNaN(lat) || isNaN(lon)) return;
    } else {
      const preset = presets[selectedKey];
      if (!preset) return;
      lat = preset.lat;
      lon = preset.lon;
      elevation = preset.elevation_m ?? 0;
    }

    setLoading(true);
    setError(null);
    try {
      const result = await getVisibility(oid, { lat, lon, elevation });
      console.log("[VisibilityCard] API response:", result);
      setData(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const observatoryName = useCustom
    ? "Custom location"
    : (presets[selectedKey]?.name ?? selectedKey);

  // Use the raw strings from the API — they must exactly match the `time`
  // values in hourly_altitudes (both come from Python datetime.isoformat()).
  // Passing through new Date().toISOString() changes the format (+00:00 → .000Z)
  // which breaks ReferenceArea's x-axis lookup.
  const darkStartTime = data?.dark_start ?? null;
  const darkEndTime = data?.dark_end ?? null;

  return (
    <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl overflow-hidden">
      {/* Header / toggle */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-white/[0.03] transition-colors"
      >
        <div className="flex items-center gap-2">
          <Eye className="w-4 h-4 text-white/40" />
          <span className="text-sm font-medium text-white/60">Visibility Tonight</span>
        </div>
        <ChevronDown
          className={`w-4 h-4 text-white/30 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div className="px-5 pb-5 space-y-4 border-t border-white/[0.05]">
          {/* Observatory selector */}
          <div className="flex flex-wrap items-center gap-3 pt-4">
            <select
              value={useCustom ? "__custom__" : selectedKey}
              onChange={(e) => {
                if (e.target.value === "__custom__") {
                  setUseCustom(true);
                } else {
                  setUseCustom(false);
                  setSelectedKey(e.target.value);
                }
              }}
              className="bg-white/[0.06] border border-white/[0.1] rounded-lg px-3 py-1.5 text-sm text-white/70 focus:outline-none focus:border-white/20"
            >
              {Object.entries(presets).map(([key, obs]) => (
                <option key={key} value={key}>
                  {obs.name} — {obs.location}
                </option>
              ))}
              <option value="__custom__">Custom coordinates…</option>
            </select>

            {!loading && data && (
              <button
                onClick={fetchVisibility}
                className="text-xs text-white/30 hover:text-white/50 transition-colors"
              >
                Refresh
              </button>
            )}
          </div>

          {/* Custom lat/lon inputs */}
          {useCustom && (
            <div className="flex flex-wrap gap-2">
              {[
                { label: "Lat", field: "lat", placeholder: "29.36" },
                { label: "Lon", field: "lon", placeholder: "79.69" },
                { label: "Elev (m)", field: "elevation", placeholder: "0" },
              ].map(({ label, field, placeholder }) => (
                <label key={field} className="flex items-center gap-1.5 text-xs text-white/40">
                  {label}
                  <input
                    type="number"
                    placeholder={placeholder}
                    value={custom[field]}
                    onChange={(e) =>
                      setCustom((prev) => ({ ...prev, [field]: e.target.value }))
                    }
                    className="w-24 bg-white/[0.06] border border-white/[0.1] rounded px-2 py-1 text-white/70 focus:outline-none"
                  />
                </label>
              ))}
              <button
                onClick={fetchVisibility}
                className="px-3 py-1 text-xs bg-cosmos-600/60 hover:bg-cosmos-600 text-white rounded-lg transition-colors"
              >
                Compute
              </button>
            </div>
          )}

          {loading && (
            <div className="h-32 flex items-center justify-center">
              <p className="text-xs text-white/30 animate-pulse">Computing visibility…</p>
            </div>
          )}

          {error && (
            <p className="text-xs text-red-400/70">{error}</p>
          )}

          {data && !loading && (
            <>
              {/* Observable badge */}
              <div className="flex items-center gap-3">
                <div
                  className="flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-medium"
                  style={{
                    background: data.observable ? "#51cf6620" : "#ff6b6b20",
                    color: data.observable ? "#51cf66" : "#ff6b6b",
                    border: `1px solid ${data.observable ? "#51cf6640" : "#ff6b6b40"}`,
                  }}
                >
                  {data.observable ? (
                    <Eye className="w-3.5 h-3.5" />
                  ) : (
                    <EyeOff className="w-3.5 h-3.5" />
                  )}
                  Observable tonight at {observatoryName}:{" "}
                  {data.observable ? "YES" : "NO"}
                </div>

                <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/[0.04] border border-white/[0.06] text-xs text-white/50">
                  <Moon className="w-3 h-3 text-yellow-400/60" />
                  Moon {data.moon_separation}° away
                </div>
              </div>

              <div className="flex gap-4 text-xs text-white/35">
                <span>Peak altitude: <span className="text-white/60">{data.max_altitude}°</span></span>
                {data.dark_start && (
                  <span>
                    Dark window: {formatHour(data.dark_start)} – {formatHour(data.dark_end)}
                  </span>
                )}
              </div>

              {/* Altitude chart — explicit px height; "100%" is unreliable in Recharts */}
              <div className="mt-2">
                <ResponsiveContainer width="100%" height={192}>
                  <ComposedChart
                    data={data.hourly_altitudes}
                    margin={{ top: 5, right: 10, left: -10, bottom: 5 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis
                      dataKey="time"
                      tickFormatter={(v) => formatHour(v).slice(0, 5)}
                      tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 10 }}
                      interval={3}
                    />
                    <YAxis
                      domain={[-10, 90]}
                      tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 10 }}
                      tickFormatter={(v) => `${v}°`}
                    />
                    <Tooltip content={<CustomTooltip />} />

                    {/* Shade the dark time window */}
                    {darkStartTime && darkEndTime && (
                      <ReferenceArea
                        x1={darkStartTime}
                        x2={darkEndTime}
                        fill="rgba(120,80,220,0.12)"
                        stroke="rgba(120,80,220,0.2)"
                        strokeDasharray="4 2"
                        label={{ value: "dark", position: "insideTop", fill: "rgba(180,150,255,0.4)", fontSize: 10 }}
                      />
                    )}

                    {/* Horizon / observable threshold */}
                    <ReferenceLine y={0} stroke="rgba(255,255,255,0.1)" />
                    <ReferenceLine
                      y={30}
                      stroke="rgba(81,207,102,0.4)"
                      strokeDasharray="5 3"
                      label={{ value: "30° min", position: "right", fill: "rgba(81,207,102,0.5)", fontSize: 9 }}
                    />

                    <Line
                      type="monotone"
                      dataKey="altitude"
                      stroke="rgba(255,255,255,0.3)"
                      strokeWidth={1.5}
                      dot={<CustomDot />}
                      activeDot={{ r: 4 }}
                      isAnimationActive={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>

              <p className="text-[10px] text-white/20">
                Green dots = above 30° (observable). Purple shading = astronomical dark time (sun below −18°).
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
