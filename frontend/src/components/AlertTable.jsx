import { Link } from "react-router-dom";
import { ExternalLink, Eye } from "lucide-react";
import { getClassInfo, getConstellation, formatTimeSince, getAlertSummary } from "../lib/cosmos";

// Helper function to generate Legacy Survey cutout URLs
const getCutoutUrl = (ra, dec, size = 64) => {
  const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
  return `${apiUrl}/api/images/cutout?ra=${ra}&dec=${dec}&size=${size}&pixscale=0.5`;
};

export default function AlertTable({ alerts, loading }) {
  if (loading) {
    return (
      <div className="grid gap-3 md:grid-cols-2">
        {[...Array(6)].map((_, i) => (
          <div key={i} className="bg-white/[0.03] rounded-xl p-5 animate-pulse h-32" />
        ))}
      </div>
    );
  }

  if (!alerts || alerts.length === 0) {
    return (
      <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-12 text-center">
        <p className="text-2xl mb-2">🔭</p>
        <p className="text-white/40">No transients found with these filters.</p>
        <p className="text-white/20 text-sm mt-1">Try widening the time range or lowering the confidence threshold.</p>
      </div>
    );
  }

  return (
    <div className="grid gap-3 md:grid-cols-2">
      {alerts.map((alert) => (
        <AlertCard key={alert.oid} alert={alert} />
      ))}
    </div>
  );
}

function AlertCard({ alert }) {
  const info = getClassInfo(alert.classification);
  const constellation = getConstellation(alert.ra, alert.dec);
  const summary = getAlertSummary(alert);

  return (
    <Link
      to={`/alert/${alert.oid}`}
      className="group block bg-white/[0.025] hover:bg-white/[0.045] border border-white/[0.06] hover:border-white/[0.12] rounded-xl p-4 transition-all duration-200"
    >
      <div className="flex gap-4">
        {/* Telescope thumbnail - LEFT SIDE */}
        <div className="shrink-0">
          <img
            src={getCutoutUrl(alert.ra, alert.dec, 80)}
            alt={`Sky region ${alert.oid}`}
            className="w-20 h-20 object-cover rounded-lg border border-white/[0.08]"
            loading="lazy"
            onError={(e) => {
              e.target.src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><rect fill="%23334155" width="80" height="80"/><text x="50%" y="50%" text-anchor="middle" dy=".3em" fill="%239ca3af" font-size="10">No image</text></svg>';
            }}
          />
        </div>

        {/* Content - RIGHT SIDE */}
        <div className="flex-1 min-w-0">
          {/* Header: emoji + name + time */}
          <div className="flex items-start justify-between mb-2">
            <div className="flex items-center gap-2.5">
              <span
                className="text-lg w-8 h-8 rounded-lg flex items-center justify-center"
                style={{ background: info.color + "15" }}
              >
                {info.emoji}
              </span>
              <div>
                <p className="text-sm font-medium text-white/85 group-hover:text-white transition-colors">
                  {info.name}
                </p>
                <p className="text-[11px] text-white/35">{info.short}</p>
              </div>
            </div>
            <span className="text-[10px] text-white/25 shrink-0">
              {formatTimeSince(alert.last_detection)}
            </span>
          </div>

          {/* Summary line */}
          <p className="text-xs text-white/45 leading-relaxed mb-3">
            {summary}
          </p>

          {/* Footer: location + links */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3 text-[10px] text-white/25">
              <span>📍 {constellation}</span>
              <span>·</span>
              <span>{alert.n_detections} observation{alert.n_detections !== 1 ? "s" : ""}</span>
              {alert.cross_match_name && (
                <>
                  <span>·</span>
                  <span>Near {alert.cross_match_name}</span>
                </>
              )}
              {alert.broker_source && (
                <>
                  <span>·</span>
                  <span className="uppercase tracking-wider" style={{ fontSize: "8px" }}>
                    {alert.broker_source === "tns" ? "IAU/TNS" : alert.broker_source}
                  </span>
                </>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-mono text-white/15 group-hover:text-white/30 transition-colors">
                {alert.oid}
              </span>
              {alert.alert_url && (
                <a
                  href={alert.alert_url}
                  target="_blank"
                  rel="noopener"
                  onClick={(e) => e.stopPropagation()}
                  className="text-white/15 hover:text-white/40 transition-colors"
                  title={alert.broker_source === "tns" ? "View on TNS" : "View raw data on ALeRCE"}
                >
                  <ExternalLink className="w-3 h-3" />
                </a>
              )}
            </div>
          </div>

          {/* Confidence bar */}
          {alert.classification_probability != null && (
            <div className="mt-2.5 flex items-center gap-2">
              <div className="flex-1 bg-white/[0.04] rounded-full h-1 overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${alert.classification_probability * 100}%`,
                    background: info.color + "80",
                  }}
                />
              </div>
              <span className="text-[9px] font-mono text-white/20">
                {(alert.classification_probability * 100).toFixed(0)}% confidence
              </span>
            </div>
          )}
        </div>
      </div>
    </Link>
  );
}