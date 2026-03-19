import { useState, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, ExternalLink, Sparkles } from "lucide-react";
import LightCurveChart from "../components/LightCurveChart";
import ClassBadge from "../components/ClassBadge";
import { getAlertDetail } from "../lib/api";
import {
  getClassInfo,
  getConstellation,
  formatTimeSince,
  formatFirstSeen,
} from "../lib/cosmos";

export default function AlertDetail() {
  const { oid } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function fetchDetail() {
      setLoading(true);
      try {
        const result = await getAlertDetail(oid);
        setData(result);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    fetchDetail();
  }, [oid]);

  if (loading) {
    return (
      <div className="space-y-4 animate-pulse">
        <div className="h-8 bg-white/5 rounded w-48" />
        <div className="h-64 bg-white/5 rounded-xl" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-4">
        <Link
          to="/"
          className="flex items-center gap-1.5 text-sm text-white/40 hover:text-white/60"
        >
          <ArrowLeft className="w-4 h-4" /> Back to dashboard
        </Link>
        <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-6 text-center">
          <p className="text-red-300">{error || `Object ${oid} not found`}</p>
        </div>
      </div>
    );
  }

  const obj = data.object;
  const info = getClassInfo(obj.classification);
  const constellation = getConstellation(obj.ra, obj.dec);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Back link */}
      <Link
        to="/"
        className="inline-flex items-center gap-1.5 text-sm text-white/40 hover:text-white/60 transition-colors"
      >
        <ArrowLeft className="w-4 h-4" /> Back to dashboard
      </Link>

      {/* Hero section */}
      <div
        className="rounded-2xl p-6 border"
        style={{
          background: `linear-gradient(135deg, ${info.color}08, ${info.color}03, transparent)`,
          borderColor: info.color + "15",
        }}
      >
        <div className="flex items-start justify-between">
          <div>
            <span className="text-3xl mb-2 block">{info.emoji}</span>
            <h1 className="text-2xl font-semibold text-white/90 mt-2">
              {info.name}
            </h1>
            <p className="text-white/50 mt-1">{info.short}</p>
            <p className="text-xs font-mono text-white/20 mt-2">{obj.oid}</p>
          </div>
          {obj.alert_url && (
            <a
              href={obj.alert_url}
              target="_blank"
              rel="noopener"
              className="flex items-center gap-1.5 text-xs text-white/30 hover:text-white/50 transition-colors"
            >
              Raw data <ExternalLink className="w-3 h-3" />
            </a>
          )}
        </div>

        {/* Story paragraph */}
        <div className="mt-4 p-4 rounded-xl bg-white/[0.03] border border-white/[0.04]">
          <p className="text-sm text-white/50 leading-relaxed">
            {info.description}
          </p>
          {info.danger && (
            <p className="text-xs text-white/30 mt-2 italic">
              {info.danger}
            </p>
          )}
        </div>
      </div>

      {/* Quick facts */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <FactCard
          label="Location"
          value={constellation}
          detail={`RA ${obj.ra?.toFixed(2)}° Dec ${obj.dec?.toFixed(2)}°`}
        />
        <FactCard
          label="Observations"
          value={`${obj.n_detections} times`}
          detail={`Since ${formatFirstSeen(obj.first_detection)}`}
        />
        <FactCard
          label="Last spotted"
          value={formatTimeSince(obj.last_detection)}
          detail={
            obj.last_detection
              ? new Date(obj.last_detection).toLocaleDateString()
              : ""
          }
        />
        <FactCard
          label="Confidence"
          value={
            obj.classification_probability
              ? `${(obj.classification_probability * 100).toFixed(0)}%`
              : "N/A"
          }
          detail={
            obj.classification_probability > 0.8
              ? "High confidence"
              : obj.classification_probability > 0.6
                ? "Moderate confidence"
                : "Low confidence"
          }
        />
      </div>

      {/* Cross-match info */}
      {obj.cross_match_name && (
        <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-4">
          <div className="flex items-center gap-2 mb-1">
            <Sparkles className="w-4 h-4 text-yellow-400/60" />
            <span className="text-sm text-white/60">Known association</span>
          </div>
          <p className="text-sm text-white/40">
            This object is near <span className="text-white/70">{obj.cross_match_name}</span>
            {obj.cross_match_type && (
              <span> (classified as {obj.cross_match_type} in SIMBAD)</span>
            )}
          </p>
        </div>
      )}

      {/* Light curve */}
      <div>
        <h2 className="text-sm font-medium text-white/50 mb-3 flex items-center gap-2">
          Brightness Over Time
          <span className="text-[10px] text-white/20 font-normal">
            Each dot is one observation. Lower = brighter (astronomers are weird).
          </span>
        </h2>
        <LightCurveChart lightCurve={data.light_curve} />
      </div>

      {/* Classification probabilities */}
      {data.probabilities && data.probabilities.length > 0 && (
        <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-4">
          <h2 className="text-sm font-medium text-white/50 mb-1">
            What the AI thinks this is
          </h2>
          <p className="text-[11px] text-white/25 mb-4">
            ALeRCE's machine learning classifier ranked these possibilities:
          </p>
          <div className="space-y-2.5">
            {data.probabilities.slice(0, 6).map((prob) => {
              const pi = getClassInfo(prob.class_name);
              return (
                <div key={prob.class_name} className="flex items-center gap-3">
                  <span className="text-sm w-5 text-center">{pi.emoji}</span>
                  <span className="text-xs text-white/50 w-36 shrink-0">
                    {pi.name}
                  </span>
                  <div className="flex-1 bg-white/[0.04] rounded-full h-2 overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all"
                      style={{
                        width: `${(prob.probability * 100).toFixed(1)}%`,
                        background: pi.color + "90",
                      }}
                    />
                  </div>
                  <span className="text-xs font-mono text-white/30 w-12 text-right">
                    {(prob.probability * 100).toFixed(1)}%
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function FactCard({ label, value, detail }) {
  return (
    <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-3.5">
      <p className="text-[10px] text-white/25 uppercase tracking-wider mb-1">
        {label}
      </p>
      <p className="text-sm font-medium text-white/80">{value}</p>
      {detail && <p className="text-[10px] text-white/25 mt-0.5">{detail}</p>}
    </div>
  );
}
