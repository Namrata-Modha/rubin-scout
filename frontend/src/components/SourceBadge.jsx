/**
 * Survey/pipeline badge — which telescope + broker produced this alert.
 *
 * alerts_live holds both ZTF and LSST/Rubin rows, distinguished by
 * alert_type ("ztf_fink" / "lsst_fink"). The two surveys share the
 * classification column but use two incompatible vocabularies (Fink's
 * fixed ZTF class strings vs. LSST's matched tag names — see
 * lsst_service.py's module docstring), so this badge is the one signal
 * that always disambiguates them regardless of what classification says.
 */
export const SOURCE_INFO = {
  ztf_fink: {
    label: "ZTF",
    fullName: "Zwicky Transient Facility",
    color: "#748ffc",
    description:
      "Detected by the Zwicky Transient Facility at Palomar Observatory, processed through the Fink broker.",
  },
  lsst_fink: {
    label: "Rubin / LSST",
    fullName: "Vera C. Rubin Observatory (LSST)",
    color: "#ff922b",
    description:
      "Detected by the Vera C. Rubin Observatory's Legacy Survey of Space and Time, processed through Fink's separate LSST deployment.",
  },
};

export function getSourceInfo(alertType) {
  return (
    SOURCE_INFO[alertType] || {
      label: alertType || "Unknown survey",
      fullName: alertType || "Unknown survey",
      color: "#868e96",
      description: "Survey/pipeline not recognized.",
    }
  );
}

export default function SourceBadge({ alertType, size = "sm" }) {
  const info = getSourceInfo(alertType);

  if (size === "lg") {
    return (
      <span
        title={info.description}
        className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium border cursor-help"
        style={{
          background: info.color + "20",
          borderColor: info.color + "45",
          color: info.color,
        }}
      >
        {info.label}
      </span>
    );
  }

  return (
    <span
      title={info.description}
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] border cursor-help shrink-0"
      style={{
        background: info.color + "18",
        borderColor: info.color + "30",
        color: info.color,
      }}
    >
      {info.label}
    </span>
  );
}
