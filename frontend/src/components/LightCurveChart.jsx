import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ErrorBar,
} from "recharts";

const BAND_CONFIG = {
  g: { color: "#4fc3f7", label: "g-band" },
  r: { color: "#ef5350", label: "r-band" },
  i: { color: "#ab47bc", label: "i-band" },
};

export default function LightCurveChart({ lightCurve, title }) {
  if (!lightCurve || lightCurve.length === 0) {
    return (
      <div className="bg-white/[0.03] border border-white/[0.06] rounded-lg p-8 text-center text-white/30">
        No light curve data available.
      </div>
    );
  }

  // Group by band
  const byBand = {};
  for (const det of lightCurve) {
    const band = det.band || "unknown";
    if (!byBand[band]) byBand[band] = [];
    byBand[band].push({
      mjd: det.mjd,
      mag: det.magpsf,
      err: det.sigmapsf || 0,
    });
  }

  return (
    <div className="bg-white/[0.03] border border-white/[0.06] rounded-lg p-4">
      {title && (
        <h3 className="text-sm font-medium text-white/60 mb-3">{title}</h3>
      )}
      <ResponsiveContainer width="100%" height={300}>
        <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 20 }}>
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="rgba(255,255,255,0.06)"
          />
          <XAxis
            dataKey="mjd"
            type="number"
            domain={["dataMin - 5", "dataMax + 5"]}
            tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 11 }}
            label={{
              value: "MJD",
              position: "insideBottom",
              offset: -10,
              fill: "rgba(255,255,255,0.3)",
              fontSize: 11,
            }}
          />
          <YAxis
            dataKey="mag"
            type="number"
            reversed={true}
            domain={["dataMin - 0.5", "dataMax + 0.5"]}
            tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 11 }}
            label={{
              value: "Magnitude",
              angle: -90,
              position: "insideLeft",
              fill: "rgba(255,255,255,0.3)",
              fontSize: 11,
            }}
          />
          <Tooltip
            contentStyle={{
              background: "#1a2332",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: "6px",
              fontSize: "12px",
            }}
            formatter={(value, name) => [
              typeof value === "number" ? value.toFixed(3) : value,
              name,
            ]}
          />
          <Legend
            wrapperStyle={{ fontSize: "12px", color: "rgba(255,255,255,0.5)" }}
          />
          {Object.entries(byBand).map(([band, data]) => {
            const config = BAND_CONFIG[band] || {
              color: "#888",
              label: band,
            };
            return (
              <Scatter
                key={band}
                name={config.label}
                data={data}
                fill={config.color}
                fillOpacity={0.8}
                r={3}
              >
                <ErrorBar
                  dataKey="err"
                  width={0}
                  strokeWidth={1}
                  stroke={config.color}
                  opacity={0.4}
                  direction="y"
                />
              </Scatter>
            );
          })}
        </ScatterChart>
      </ResponsiveContainer>
      <p className="text-[10px] text-white/20 mt-2 text-center">
        Lower magnitude = brighter. Error bars show measurement uncertainty.
      </p>
    </div>
  );
}
