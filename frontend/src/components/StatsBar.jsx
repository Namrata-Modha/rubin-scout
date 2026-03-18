import { Activity, Star, Clock, Zap } from "lucide-react";

export default function StatsBar({ stats, loading }) {
  if (loading) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="bg-white/5 rounded-lg p-4 animate-pulse h-20" />
        ))}
      </div>
    );
  }

  if (!stats) return null;

  const cards = [
    {
      label: "Total Alerts",
      value: stats.total_alerts?.toLocaleString() || "0",
      icon: Activity,
      color: "text-cosmos-400",
    },
    {
      label: "Supernovae",
      value: (
        (stats.by_classification?.SNIa || 0) +
        (stats.by_classification?.SNII || 0) +
        (stats.by_classification?.SNIbc || 0)
      ).toLocaleString(),
      icon: Star,
      color: "text-red-400",
    },
    {
      label: "AGN / Blazars",
      value: (
        (stats.by_classification?.AGN || 0) +
        (stats.by_classification?.Blazar || 0)
      ).toLocaleString(),
      icon: Zap,
      color: "text-blue-400",
    },
    {
      label: "Latest",
      value: stats.latest_alert
        ? new Date(stats.latest_alert.last_detection).toLocaleTimeString()
        : "N/A",
      icon: Clock,
      color: "text-green-400",
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
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
