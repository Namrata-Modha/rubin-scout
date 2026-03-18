import { useState, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, ExternalLink, MapPin, Clock, Hash } from "lucide-react";
import LightCurveChart from "../components/LightCurveChart";
import ClassBadge from "../components/ClassBadge";
import { getAlertDetail } from "../lib/api";

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
        <div className="h-64 bg-white/5 rounded" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-4">
        <Link to="/" className="flex items-center gap-1.5 text-sm text-white/40 hover:text-white/60">
          <ArrowLeft className="w-4 h-4" /> Back to dashboard
        </Link>
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-6 text-center">
          <p className="text-red-300">
            {error || `Object ${oid} not found`}
          </p>
        </div>
      </div>
    );
  }

  const obj = data.object;

  return (
    <div className="space-y-6">
      {/* Back link */}
      <Link
        to="/"
        className="inline-flex items-center gap-1.5 text-sm text-white/40 hover:text-white/60 transition-colors"
      >
        <ArrowLeft className="w-4 h-4" /> Back to dashboard
      </Link>

      {/* Object header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-mono font-semibold">{obj.oid}</h1>
          <div className="flex items-center gap-3 mt-2">
            <ClassBadge
              classification={obj.classification}
              probability={obj.classification_probability}
            />
            {obj.cross_match_name && (
              <span className="text-xs text-white/40">
                Cross-match: {obj.cross_match_name}
                {obj.cross_match_type && ` (${obj.cross_match_type})`}
              </span>
            )}
          </div>
        </div>
        {obj.alert_url && (
          <a
            href={obj.alert_url}
            target="_blank"
            rel="noopener"
            className="flex items-center gap-1.5 text-xs text-cosmos-400 hover:text-cosmos-300 transition-colors"
          >
            View on ALeRCE <ExternalLink className="w-3 h-3" />
          </a>
        )}
      </div>

      {/* Metadata grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetaCard
          icon={MapPin}
          label="Position"
          value={`${obj.ra?.toFixed(5)}, ${obj.dec?.toFixed(5)}`}
          sub="RA, Dec (degrees)"
        />
        <MetaCard
          icon={Hash}
          label="Detections"
          value={obj.n_detections?.toString() || "0"}
          sub="Total measurements"
        />
        <MetaCard
          icon={Clock}
          label="First Seen"
          value={
            obj.first_detection
              ? new Date(obj.first_detection).toLocaleDateString()
              : "N/A"
          }
          sub={obj.first_detection ? new Date(obj.first_detection).toLocaleTimeString() : ""}
        />
        <MetaCard
          icon={Clock}
          label="Last Seen"
          value={
            obj.last_detection
              ? new Date(obj.last_detection).toLocaleDateString()
              : "N/A"
          }
          sub={obj.last_detection ? new Date(obj.last_detection).toLocaleTimeString() : ""}
        />
      </div>

      {/* Light curve */}
      <LightCurveChart
        lightCurve={data.light_curve}
        title={`Light Curve: ${obj.oid}`}
      />

      {/* Classification probabilities */}
      {data.probabilities && data.probabilities.length > 0 && (
        <div className="bg-white/[0.03] border border-white/[0.06] rounded-lg p-4">
          <h3 className="text-sm font-medium text-white/60 mb-3">
            Classification Probabilities
          </h3>
          <div className="space-y-2">
            {data.probabilities.slice(0, 8).map((prob) => (
              <div key={prob.class_name} className="flex items-center gap-3">
                <span className="text-xs font-mono text-white/50 w-20 shrink-0">
                  {prob.class_name}
                </span>
                <div className="flex-1 bg-white/[0.04] rounded-full h-2 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-cosmos-500 transition-all"
                    style={{ width: `${(prob.probability * 100).toFixed(1)}%` }}
                  />
                </div>
                <span className="text-xs font-mono text-white/40 w-12 text-right">
                  {(prob.probability * 100).toFixed(1)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function MetaCard({ icon: Icon, label, value, sub }) {
  return (
    <div className="bg-white/[0.03] border border-white/[0.06] rounded-lg p-3">
      <div className="flex items-center gap-2 mb-1">
        <Icon className="w-3.5 h-3.5 text-white/30" />
        <span className="text-[10px] text-white/30 uppercase tracking-wider">
          {label}
        </span>
      </div>
      <p className="font-mono text-sm">{value}</p>
      {sub && <p className="text-[10px] text-white/20 mt-0.5">{sub}</p>}
    </div>
  );
}
