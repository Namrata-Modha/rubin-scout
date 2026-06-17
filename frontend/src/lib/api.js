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

/** Get gravitational wave events with optional pagination. */
export async function getGWEvents({ limit = 20, offset = 0 } = {}) {
  return fetchJSON(`/gw/events?limit=${limit}&offset=${offset}`);
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

/** Get FRB (Fast Radio Burst) alerts. */
export async function getFRBEvents({ limit = 100, offset = 0 } = {}) {
  const params = new URLSearchParams({
    classification: "FRB",
    min_probability: 0,
    hours: 87600,
    limit,
    offset,
  });
  return fetchJSON(`/alerts/recent?${params}`);
}

/** Get paginated live alerts from alerts_live (Fink/ZTF broker). */
export async function getLiveAlerts({ limit = 50, offset = 0, classification = null } = {}) {
  const params = new URLSearchParams({ limit, offset });
  if (classification) params.set("classification", classification);
  return fetchJSON(`/alerts/live?${params}`);
}

/** Get distinct classification values + counts from alerts_live. */
export async function getLiveClassifications() {
  return fetchJSON(`/alerts/live/classifications`);
}

/** Get full detail for a single live alert by its Fink candid external_id. */
export async function getLiveAlertDetail(externalId) {
  return fetchJSON(`/live-alerts/live/${externalId}`);
}

/** Ask the RAG knowledge base a question. */
export async function askQuestion(question) {
  return fetchJSON(`/ask`, {
    method: "POST",
    body: JSON.stringify({ question }),
  });
}

/** Get built-in observatory presets. */
export async function getObservatories() {
  return fetchJSON(`/observatories`);
}

/** Get tonight's visibility for an object from a given observer location. */
export async function getVisibility(oid, { lat, lon, elevation = 0, date = null } = {}) {
  const params = new URLSearchParams({ lat, lon, elevation });
  if (date) params.set("date", date);
  return fetchJSON(`/alerts/${oid}/visibility?${params}`);
}

/**
 * ILMT follow-up planner — returns ZTF history, SIMBAD cross-match, GW
 * coincidences, observatory visibility, and a follow-up recommendation.
 *
 * @param {object} opts
 * @param {number} opts.ra - Right Ascension in degrees
 * @param {number} opts.dec - Declination in degrees
 * @param {number} opts.mjd - Modified Julian Date of observation
 * @param {number} [opts.radiusArcsec=5.0] - Cone-search radius in arcseconds
 * @param {string} [opts.observatoryKey] - Preset key from /api/observatories, or "custom"
 * @param {number} [opts.obsLat] - Custom observer latitude (required when observatoryKey="custom")
 * @param {number} [opts.obsLon] - Custom observer longitude (required when observatoryKey="custom")
 * @param {number} [opts.obsElevation=0] - Custom observer elevation in metres
 */
export async function getIlmtFollowup({
  ra,
  dec,
  mjd,
  radiusArcsec = 5.0,
  observatoryKey = null,
  obsLat = null,
  obsLon = null,
  obsElevation = 0,
} = {}) {
  const params = new URLSearchParams({ ra, dec, mjd, radius_arcsec: radiusArcsec });
  if (observatoryKey) params.set("observatory_key", observatoryKey);
  if (observatoryKey === "custom") {
    if (obsLat != null) params.set("obs_lat", obsLat);
    if (obsLon != null) params.set("obs_lon", obsLon);
    params.set("obs_elevation", obsElevation);
  }
  return fetchJSON(`/ilmt/followup?${params}`);
}
