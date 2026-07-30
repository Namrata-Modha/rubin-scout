/**
 * GW event significance tier — how confident GWOSC itself is in a detection.
 *
 * Based on GWOSC's own catalog tags (see backend/app/enrichment/gw_crossmatch.py
 * _CONFIDENT_CATALOGS / _MARGINAL_CATALOGS / _PRELIMINARY_CATALOGS), not an
 * invented threshold. Wording below is drawn from those same comments so the
 * UI never claims more (or less) than the backend classification already
 * established.
 */
export const SIGNIFICANCE_INFO = {
  confident: {
    label: "Confident",
    color: "#69db7c",
    emphasis: "solid",
    description:
      "An official LVK high-significance detection, from a confident-tier GWTC catalog release.",
  },
  marginal: {
    label: "Marginal",
    color: "#ffa94d",
    emphasis: "muted",
    description:
      "Marginal: below the confidence threshold for a confirmed LVK detection, but included in the official GWTC catalog as a sub-threshold candidate.",
  },
  preliminary: {
    label: "Preliminary",
    color: "#ffa94d",
    emphasis: "muted",
    description:
      "Preliminary: an individual LVK discovery-paper detection, published ahead of being folded into a cumulative confident/marginal catalog release.",
  },
  unknown: {
    label: "Unknown tier",
    color: "#868e96",
    emphasis: "neutral",
    description:
      "This event's GWOSC catalog tag isn't recognized by our classifier yet, so no significance tier has been assigned.",
  },
  unclassified: {
    label: "Unclassified",
    color: "#868e96",
    emphasis: "neutral",
    description:
      "This event was ingested before significance tracking existed, so no tier has been assigned.",
  },
};

export function getSignificanceInfo(significance) {
  return SIGNIFICANCE_INFO[significance] || SIGNIFICANCE_INFO.unclassified;
}

/**
 * Compact pill badge for list rows. Three visual weights, not five: confident
 * reads solid/normal, marginal + preliminary share a muted amber (distinct
 * label text, same reduced-emphasis treatment), unknown + unclassified share
 * a neutral grey — distinct from both.
 */
export default function SignificanceBadge({ significance, size = "sm" }) {
  const info = getSignificanceInfo(significance);

  const alpha = { solid: ["2a", "50"], muted: ["12", "22"], neutral: ["10", "1c"] }[info.emphasis];
  const textOpacity = { solid: 1, muted: 0.7, neutral: 0.55 }[info.emphasis];

  if (size === "lg") {
    return (
      <span
        title={info.description}
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium border cursor-help"
        style={{
          background: info.color + alpha[0],
          borderColor: info.color + alpha[1],
          color: info.color,
          opacity: textOpacity,
        }}
      >
        {info.label}
      </span>
    );
  }

  return (
    <span
      title={info.description}
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] border cursor-help"
      style={{
        background: info.color + alpha[0],
        borderColor: info.color + alpha[1],
        color: info.color,
        opacity: textOpacity,
      }}
    >
      {info.label}
    </span>
  );
}
