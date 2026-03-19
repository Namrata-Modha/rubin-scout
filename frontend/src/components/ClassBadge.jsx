import { getClassInfo } from "../lib/cosmos";

export default function ClassBadge({ classification, probability, size = "sm" }) {
  const info = getClassInfo(classification);

  if (size === "lg") {
    return (
      <div className="flex items-center gap-2.5">
        <span className="text-xl">{info.emoji}</span>
        <div>
          <p className="text-sm font-medium text-white/90">{info.name}</p>
          <p className="text-xs text-white/40">{info.short}</p>
        </div>
        {probability != null && (
          <span className="ml-2 text-[10px] font-mono px-1.5 py-0.5 rounded bg-white/[0.06] text-white/40">
            {(probability * 100).toFixed(0)}% match
          </span>
        )}
      </div>
    );
  }

  return (
    <span
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs border"
      style={{
        background: info.color + "18",
        borderColor: info.color + "30",
        color: info.color,
      }}
    >
      <span>{info.emoji}</span>
      <span>{info.name}</span>
      {probability != null && (
        <span className="opacity-50 ml-0.5">{(probability * 100).toFixed(0)}%</span>
      )}
    </span>
  );
}
