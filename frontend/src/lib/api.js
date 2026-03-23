/**
 * Rubin Scout API client.
 * In development: proxied through Vite to localhost:8000
 * In production: hits the Render backend URL directly
 */

const API_BASE = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL}/api`
  : "/api";

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

/** Get recent alerts with optional filters and pagination. */
export async function getRecentAlerts({
  classification = null,
  minProbability = 0.5,
  hours = 87600,
  limit = 12,
  offset = 0,
} = {}) {
  const params = new URLSearchParams({
    min_probability: minProbability,
    hours,
    limit,
    offset,
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

/** Get all gravitational wave events. */
export async function getGWEvents() {
  return fetchJSON(`/gw/events`);
}

/** Get a single GW event. */
export async function getGWEvent(superEventId) {
  return fetchJSON(`/gw/events/${superEventId}`);
}

/** Run cross-matching for a GW event. */
export async function crossMatchGWEvent(superEventId, { searchRadiusDeg = 15, timeWindowDays = 30 } = {}) {
  return fetchJSON(`/gw/events/${superEventId}/crossmatch?search_radius_deg=${searchRadiusDeg}&time_window_days=${timeWindowDays}`, { method: "POST" });
}

/** Seed GW events into the database. */
export async function seedGWEvents() {
  return fetchJSON(`/gw/seed`, { method: "POST" });
}
