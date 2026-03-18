import clsx from "clsx";

const CLASS_COLORS = {
  SNIa: "bg-red-400/20 text-red-300 border-red-400/30",
  SNII: "bg-orange-400/20 text-orange-300 border-orange-400/30",
  SNIbc: "bg-rose-400/20 text-rose-300 border-rose-400/30",
  SLSN: "bg-purple-400/20 text-purple-300 border-purple-400/30",
  TDE: "bg-cyan-400/20 text-cyan-300 border-cyan-400/30",
  AGN: "bg-blue-400/20 text-blue-300 border-blue-400/30",
  Blazar: "bg-indigo-400/20 text-indigo-300 border-indigo-400/30",
  QSO: "bg-sky-400/20 text-sky-300 border-sky-400/30",
  KN: "bg-yellow-400/20 text-yellow-300 border-yellow-400/30",
  "CV/Nova": "bg-green-400/20 text-green-300 border-green-400/30",
};

export default function ClassBadge({ classification, probability }) {
  const colorClass =
    CLASS_COLORS[classification] ||
    "bg-gray-400/20 text-gray-300 border-gray-400/30";

  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-mono border",
        colorClass
      )}
    >
      {classification || "unknown"}
      {probability != null && (
        <span className="opacity-60">{(probability * 100).toFixed(0)}%</span>
      )}
    </span>
  );
}
