import { useState, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, ExternalLink } from "lucide-react";
import CosmicAnimation from "../components/CosmicAnimation";
import { getLiveAlertDetail } from "../lib/api";
import { getClassInfo, getConstellation, formatTimeSince } from "../lib/cosmos";

// ── Classification mapping: Fink label → CosmicAnimation key ─────────────────
const FINK_TO_ANIM = {
  "SN candidate":         "SNIa",
  "Early SN Ia candidate": "SNIa",
  "SLSN candidate":       "SLSN",
  "Kilonova candidate":   "KN",
};

// ── Score definitions with plain-English labels ───────────────────────────────
const SCORE_DEFS = [
  {
    key: "snn_sn_vs_all",
    label: "SuperNNova: SN vs everything",
    hint: "Deep-learning classifier. How likely is this to be any kind of supernova vs. something else (AGN, variable star, artefact)?",
    color: "#ff6b6b",
  },
  {
    key: "snn_snia_vs_nonia",
    label: "SuperNNova: Type Ia vs other SN",
    hint: "Among supernovae, how likely is this to be a Type Ia (exploding white dwarf) rather than a core-collapse event?",
    color: "#ff8787",
  },
  {
    key: "rf_kn_vs_nonkn",
    label: "Random Forest: Kilonova likelihood",
    hint: "Fink's random-forest model. A high score means the light curve evolution resembles a neutron-star merger kilonova.",
    color: "#ffd43b",
  },
  {
    key: "slsn_score",
    label: "SLSN classifier score",
    hint: "How likely is this to be a superluminous supernova — an explosion 10–100× brighter than a normal supernova?",
    color: "#e599f7",
  },
];

// ── Shared sub-components ─────────────────────────────────────────────────────

function FactCard({ label, value, detail, mono = false }) {
  return (
    <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-3.5">
      <p className="text-[10px] text-white/25 uppercase tracking-wider mb-1">{label}</p>
      <p className={`text-sm font-medium text-white/80 ${mono ? "font-mono" : ""}`}>
        {value ?? "—"}
      </p>
      {detail && <p className="text-[10px] text-white/25 mt-0.5">{detail}</p>}
    </div>
  );
}

function ScoreBar({ label, hint, value, color }) {
  const pct = value != null ? Math.min(Math.max(value * 100, 0), 100) : null;
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-white/50" title={hint}>
          {label}
          <span className="ml-1 text-white/20 cursor-help text-[10px]">ⓘ</span>
        </span>
        <span className="text-[11px] font-mono text-white/40">
          {pct != null ? `${pct.toFixed(1)}%` : "—"}
        </span>
      </div>
      <div className="h-1.5 bg-white/[0.06] rounded-full overflow-hidden">
        {pct != null && (
          <div
            className="h-full rounded-full transition-all"
            style={{ width: `${pct}%`, background: color, opacity: 0.75 }}
          />
        )}
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-5">
      <h2 className="text-sm font-medium text-white/50 mb-4">{title}</h2>
      {children}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function LiveAlertDetail() {
  const { externalId } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getLiveAlertDetail(externalId)
      .then(setData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [externalId]);

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto space-y-4 animate-pulse">
        <div className="h-6 bg-white/5 rounded w-32" />
        <div className="h-48 bg-white/5 rounded-2xl" />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[...Array(4)].map((_, i) => <div key={i} className="h-20 bg-white/5 rounded-xl" />)}
        </div>
        <div className="h-40 bg-white/5 rounded-xl" />
        <div className="h-40 bg-white/5 rounded-xl" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="max-w-4xl mx-auto space-y-4">
        <Link to="/live-sky" className="inline-flex items-center gap-1.5 text-sm text-white/40 hover:text-white/60">
          <ArrowLeft className="w-4 h-4" /> Back to Live Sky
        </Link>
        <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-6 text-center">
          <p className="text-red-300 font-medium">Alert not found</p>
          <p className="text-red-400/60 text-sm mt-1">{error || `No alert with ID ${externalId}`}</p>
        </div>
      </div>
    );
  }

  const info = getClassInfo(data.classification);
  const animKey = FINK_TO_ANIM[data.classification] ?? null;
  const coords = data.coords ?? {};
  const phot = data.photometry ?? {};
  const scores = data.classification_scores ?? {};
  const ctx = data.context ?? {};
  const xm = data.crossmatch ?? {};
  const host = data.host ?? {};
  const constellation = getConstellation(coords.ra ?? data.ra, coords.dec ?? data.dec);

  // Classify real/bogus scores into plain words
  const rbLabel = (rb) => {
    if (rb == null) return "—";
    if (rb >= 0.65) return "Real (likely genuine transient)";
    if (rb >= 0.3) return "Uncertain";
    return "Bogus (likely artefact)";
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Back link */}
      <Link
        to="/live-sky"
        className="inline-flex items-center gap-1.5 text-sm text-white/40 hover:text-white/60 transition-colors"
      >
        <ArrowLeft className="w-4 h-4" /> Back to Live Sky
      </Link>

      {/* Hero */}
      <div
        className="rounded-2xl p-6 border"
        style={{
          background: `linear-gradient(135deg, ${info.color}08, ${info.color}03, transparent)`,
          borderColor: info.color + "18",
        }}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <span className="text-3xl mb-2 block">{info.emoji}</span>
            <h1 className="text-2xl font-semibold text-white/90 mt-2">{info.name}</h1>
            <p className="text-white/50 mt-1">{info.short}</p>

            {/* ZTF object ID */}
            {data.object_id && (
              <div className="flex items-center gap-2 mt-3">
                <span className="text-xs text-white/25">ZTF ID</span>
                <a
                  href={`https://fink-portal.org/${data.object_id}`}
                  target="_blank"
                  rel="noopener"
                  className="text-xs font-mono text-cosmos-400 hover:text-cosmos-300 flex items-center gap-1 transition-colors"
                >
                  {data.object_id}
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
            )}

            {/* Candid */}
            <p className="text-[11px] font-mono text-white/20 mt-1">{externalId}</p>
          </div>

          <div className="text-right shrink-0">
            <p className="text-xs text-white/25">Detected</p>
            <p className="text-sm text-white/60 mt-0.5">
              {data.detected_at ? formatTimeSince(data.detected_at) : "—"}
            </p>
            <p className="text-xs text-white/25 mt-2">Ingested</p>
            <p className="text-sm text-white/60 mt-0.5">
              {data.ingested_at ? formatTimeSince(data.ingested_at) : "—"}
            </p>
          </div>
        </div>

        {/* Description */}
        <div className="mt-4 p-4 rounded-xl bg-white/[0.03] border border-white/[0.04]">
          <p className="text-sm text-white/50 leading-relaxed">{info.description}</p>
          {info.danger && (
            <p className="text-xs text-white/30 mt-2 italic">{info.danger}</p>
          )}
        </div>
      </div>

      {/* Cosmic animation */}
      {animKey && <CosmicAnimation classification={animKey} />}

      {/* Quick facts */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <FactCard
          label="Direction"
          value={ctx.constellation || constellation}
          detail={`RA ${(coords.ra ?? data.ra)?.toFixed(4)}°`}
        />
        <FactCard
          label="Declination"
          value={`${(coords.dec ?? data.dec)?.toFixed(4)}°`}
          detail="South is negative"
          mono
        />
        <FactCard
          label="Julian Date"
          value={coords.jd?.toFixed(3) ?? "—"}
          detail="Days since Jan 1, 4713 BC"
          mono
        />
        <FactCard
          label="Active for"
          value={ctx.lapse != null ? `${ctx.lapse.toFixed(1)} days` : "—"}
          detail={ctx.firstdate ? `Since ${ctx.firstdate.slice(0, 10)}` : undefined}
        />
      </div>

      {/* Photometry */}
      <Section title="Photometry">
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-4">
          <FactCard
            label="Magnitude (PSF)"
            value={phot.magpsf?.toFixed(3) ?? "—"}
            detail="Brightness — lower = brighter"
            mono
          />
          <FactCard
            label="Uncertainty (±σ)"
            value={phot.sigmapsf?.toFixed(4) ?? "—"}
            detail="Photometric error in magnitudes"
            mono
          />
          <FactCard
            label="Limiting magnitude"
            value={phot.diffmaglim?.toFixed(2) ?? "—"}
            detail="Faintest detectable source this night"
            mono
          />
        </div>

        {/* Real/bogus scores */}
        <div className="space-y-3 pt-3 border-t border-white/[0.05]">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-white/50">
                Real/Bogus score
                <span className="ml-1 text-white/20 cursor-help text-[10px]" title="Trained on real vs artefact detections. ≥0.65 = likely real.">ⓘ</span>
              </p>
              <p className="text-[10px] text-white/25 mt-0.5">
                {phot.rb != null ? rbLabel(phot.rb) : "Not available"}
              </p>
            </div>
            <span className="text-sm font-mono text-white/60">
              {phot.rb?.toFixed(3) ?? "—"}
            </span>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-white/50">
                Deep Learning Real/Bogus
                <span className="ml-1 text-white/20 cursor-help text-[10px]" title="DeepCNN trained on ZTF images. More accurate than rb for faint sources.">ⓘ</span>
              </p>
              <p className="text-[10px] text-white/25 mt-0.5">
                {phot.drb != null ? rbLabel(phot.drb) : "Not available"}
              </p>
            </div>
            <span className="text-sm font-mono text-white/60">
              {phot.drb?.toFixed(3) ?? "—"}
            </span>
          </div>
        </div>
      </Section>

      {/* Classification scores */}
      <Section title="Fink classifier scores">
        <p className="text-[11px] text-white/25 mb-4 -mt-2 leading-relaxed">
          Four independent Fink ML pipelines evaluate every ZTF alert. Each bar shows that
          pipeline's confidence that this is the named event type. Scores are independent —
          they don't sum to 1.
        </p>
        <div className="space-y-4">
          {SCORE_DEFS.map((def) => (
            <ScoreBar
              key={def.key}
              label={def.label}
              hint={def.hint}
              value={scores[def.key]}
              color={def.color}
            />
          ))}
        </div>
      </Section>

      {/* Cross-match */}
      <Section title="Catalog cross-match">
        <div className="space-y-2 text-sm">
          {[
            {
              label: "CDS Xmatch",
              value: xm.cdsxmatch && xm.cdsxmatch !== "Unknown" ? xm.cdsxmatch : null,
              fallback: "No known catalog match",
              hint: "Nearest source in the SIMBAD/VizieR catalog within 1.5 arcsec",
            },
            {
              label: "TNS name",
              value: xm.tns,
              fallback: "Not reported to TNS",
              hint: "IAU Transient Name Server — official designation if spectroscopically classified",
            },
            {
              label: "VSX",
              value: xm.vsx,
              fallback: "Not in AAVSO VSX",
              hint: "Variable Star Index — known variable star at this position",
            },
            {
              label: "Mangrove galaxy (2MASS)",
              value: xm.mangrove_2MASS_name,
              fallback: "No 2MASS host match",
              hint: "Nearest galaxy in the Mangrove catalog (2MASS photometry)",
            },
            {
              label: "Mangrove galaxy (HyperLEDA)",
              value: xm.mangrove_HyperLEDA_name,
              fallback: null,
            },
          ].map(({ label, value, fallback, hint }) => (
            <div key={label} className="flex items-start justify-between gap-4 py-1.5 border-b border-white/[0.04] last:border-0">
              <span className="text-white/35 shrink-0" title={hint}>
                {label}
                {hint && <span className="ml-1 text-white/15 cursor-help text-[10px]">ⓘ</span>}
              </span>
              <span className={`text-right font-mono text-[12px] ${value ? "text-white/70" : "text-white/20"}`}>
                {value ?? fallback ?? "—"}
              </span>
            </div>
          ))}

          {xm.mangrove_lum_dist != null && (
            <div className="flex items-start justify-between gap-4 py-1.5">
              <span className="text-white/35">Luminosity distance</span>
              <span className="text-right font-mono text-[12px] text-white/70">
                {parseFloat(xm.mangrove_lum_dist).toFixed(1)} Mpc
              </span>
            </div>
          )}
        </div>
      </Section>

      {/* Host context */}
      <Section title="Host &amp; detection context">
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <FactCard
            label="Star/galaxy score"
            value={host.classtar?.toFixed(3) ?? "—"}
            detail="0 = galaxy, 1 = point source (star)"
            mono
          />
          <FactCard
            label="Nearest source (arcsec)"
            value={host.distnr?.toFixed(2) ?? "—"}
            detail={host.magnr != null ? `Ref. mag ${host.magnr.toFixed(2)}` : undefined}
            mono
          />
          <FactCard
            label="Detection history"
            value={host.ndethist ?? "—"}
            detail="Total ZTF detections of this position"
          />
          {host.nmtchps != null && (
            <FactCard
              label="Nearby sources"
              value={host.nmtchps}
              detail="Sources within 30 arcsec in PS1"
            />
          )}
        </div>
      </Section>

      {/* Footer */}
      <p className="text-[10px] text-white/20 text-center py-4">
        Alert data from the Fink broker (Möller et al. 2021, MNRAS 501, 3272) processing the
        ZTF alert stream. Classifications by SuperNNova and Fink random-forest pipelines.
        External ID (candid) {externalId}.
      </p>
    </div>
  );
}
