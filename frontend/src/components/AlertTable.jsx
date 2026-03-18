import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";
import ClassBadge from "./ClassBadge";

export default function AlertTable({ alerts, loading }) {
  if (loading) {
    return (
      <div className="bg-white/[0.03] border border-white/[0.06] rounded-lg overflow-hidden">
        <div className="p-8 text-center text-white/30 animate-pulse">
          Loading alerts...
        </div>
      </div>
    );
  }

  if (!alerts || alerts.length === 0) {
    return (
      <div className="bg-white/[0.03] border border-white/[0.06] rounded-lg p-8 text-center text-white/30">
        No alerts found for the current filters.
      </div>
    );
  }

  return (
    <div className="bg-white/[0.03] border border-white/[0.06] rounded-lg overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/[0.06] text-white/40 text-xs uppercase tracking-wider">
              <th className="text-left px-4 py-3 font-medium">Object ID</th>
              <th className="text-left px-4 py-3 font-medium">Class</th>
              <th className="text-right px-4 py-3 font-medium">RA</th>
              <th className="text-right px-4 py-3 font-medium">Dec</th>
              <th className="text-right px-4 py-3 font-medium">Detections</th>
              <th className="text-left px-4 py-3 font-medium">Cross-match</th>
              <th className="text-left px-4 py-3 font-medium">Last Seen</th>
              <th className="text-center px-4 py-3 font-medium">Links</th>
            </tr>
          </thead>
          <tbody>
            {alerts.map((alert) => (
              <tr
                key={alert.oid}
                className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors"
              >
                <td className="px-4 py-3">
                  <Link
                    to={`/alert/${alert.oid}`}
                    className="font-mono text-cosmos-400 hover:text-cosmos-300 transition-colors"
                  >
                    {alert.oid}
                  </Link>
                </td>
                <td className="px-4 py-3">
                  <ClassBadge
                    classification={alert.classification}
                    probability={alert.classification_probability}
                  />
                </td>
                <td className="px-4 py-3 text-right font-mono text-white/60">
                  {alert.ra?.toFixed(4)}
                </td>
                <td className="px-4 py-3 text-right font-mono text-white/60">
                  {alert.dec?.toFixed(4)}
                </td>
                <td className="px-4 py-3 text-right font-mono">
                  {alert.n_detections}
                </td>
                <td className="px-4 py-3 text-white/50 text-xs truncate max-w-[160px]">
                  {alert.cross_match_name || "\u2014"}
                </td>
                <td className="px-4 py-3 text-white/40 text-xs">
                  {alert.last_detection
                    ? formatRelativeTime(alert.last_detection)
                    : "\u2014"}
                </td>
                <td className="px-4 py-3 text-center">
                  {alert.alert_url && (
                    <a
                      href={alert.alert_url}
                      target="_blank"
                      rel="noopener"
                      className="text-white/30 hover:text-white/60 transition-colors"
                      title="View on ALeRCE"
                    >
                      <ExternalLink className="w-3.5 h-3.5 inline" />
                    </a>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function formatRelativeTime(isoString) {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}
