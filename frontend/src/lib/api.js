/**
 * Rubin Scout API client.
 * All endpoints are relative to the FastAPI backend.
 */

const API_BASE = "/api";

async function fetchJSON(url, options = {}) {
  const response = await fetch(`${API_BASE}${url}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }

  return response.json();
}

/** Get recent alerts with optional filters. */
export async function getRecentAlerts({
  classification = null,
  minProbability = 0.5,
  hours = 24,
  limit = 50,
} = {}) {
  const params = new URLSearchParams({
    min_probability: minProbability,
    hours,
    limit,
  });
  if (classification) params.set("classification", classification);
  return fetchJSON(`/alerts/recent?${params}`);
}

/** Get full detail for a single object. */
export async function getAlertDetail(oid) {
  return fetchJSON(`/alerts/${oid}`);
}

/** Cone search around a sky position. */
export async function coneSearch(ra, dec, radiusArcsec = 60) {
  const params = new URLSearchParams({ ra, dec, radius: radiusArcsec });
  return fetchJSON(`/alerts/conesearch/query?${params}`);
}

/** Get summary statistics. */
export async function getSummaryStats(hours = 24) {
  return fetchJSON(`/stats/summary?hours=${hours}`);
}

/** Get list of all classification types. */
export async function getClassifications() {
  return fetchJSON(`/classifications`);
}
