/**
 * Translates astronomy jargon into language that curious humans can enjoy.
 * This is what makes Rubin Scout different from ALeRCE.
 */

export const CLASS_INFO = {
  SNIa: {
    name: "Type Ia Supernova",
    emoji: "💥",
    short: "Exploding white dwarf",
    description:
      "A white dwarf star stole too much matter from a companion and detonated in a thermonuclear explosion. These all explode with nearly the same brightness, which is how we measure the expansion of the universe.",
    color: "#ff6b6b",
    danger: "The explosion would sterilize everything within 50 light-years.",
  },
  SNII: {
    name: "Type II Supernova",
    emoji: "🌟",
    short: "Massive star collapse",
    description:
      "A massive star (8+ times our Sun) ran out of fuel and its core collapsed in under a second. The outer layers rebounded in an explosion visible across the universe. This is how heavy elements like gold and iron are forged.",
    color: "#ffa94d",
    danger: "The star lived fast and died young — typically under 20 million years old.",
  },
  SNIbc: {
    name: "Type Ib/c Supernova",
    emoji: "⚡",
    short: "Stripped star explosion",
    description:
      "A massive star that had its outer hydrogen layers stripped away (by a companion star or stellar winds) before it exploded. The explosion reveals the star's bare helium or carbon core.",
    color: "#ff8787",
  },
  SLSN: {
    name: "Superluminous Supernova",
    emoji: "✨",
    short: "Ultra-bright explosion",
    description:
      "An extraordinarily powerful explosion, 10 to 100 times brighter than a normal supernova. These are among the most energetic events in the universe and are still not fully understood.",
    color: "#e599f7",
    danger: "So bright it can outshine its entire host galaxy for weeks.",
  },
  TDE: {
    name: "Tidal Disruption Event",
    emoji: "🕳️",
    short: "Star shredded by black hole",
    description:
      "A star wandered too close to a supermassive black hole and was torn apart by tidal forces. The stellar debris forms a glowing accretion disk as it spirals into the black hole, producing a bright flare.",
    color: "#66d9e8",
    danger: "The star gets stretched into a stream of gas millions of kilometers long.",
  },
  AGN: {
    name: "Active Galactic Nucleus",
    emoji: "🌀",
    short: "Feeding supermassive black hole",
    description:
      "A supermassive black hole at the center of a galaxy, actively swallowing surrounding gas and dust. The material heats up to millions of degrees as it spirals in, creating a beacon of light visible across billions of light-years.",
    color: "#74c0fc",
  },
  Blazar: {
    name: "Blazar",
    emoji: "🔦",
    short: "Black hole jet aimed at Earth",
    description:
      "An active galactic nucleus with a relativistic jet of particles pointed directly at us. We're staring down the barrel of a beam of matter moving at near the speed of light.",
    color: "#748ffc",
  },
  QSO: {
    name: "Quasar",
    emoji: "💎",
    short: "Ancient cosmic lighthouse",
    description:
      "An extremely luminous active galactic nucleus, often billions of light-years away. Some quasars outshine their entire galaxy by a factor of 100. The light we see left when the universe was young.",
    color: "#91a7ff",
  },
  KN: {
    name: "Kilonova",
    emoji: "🔔",
    short: "Neutron star collision",
    description:
      "Two neutron stars spiraled together and merged in a cataclysmic collision. This is the primary source of heavy elements like gold and platinum, and produces both gravitational waves and electromagnetic radiation.",
    color: "#ffd43b",
    danger: "A single kilonova produces several Earth-masses worth of gold.",
  },
  "CV/Nova": {
    name: "Nova",
    emoji: "🔥",
    short: "Recurring stellar eruption",
    description:
      "A white dwarf star in a binary system that periodically erupts when hydrogen from its companion accumulates on its surface and ignites in a thermonuclear flash. Unlike a supernova, the star survives.",
    color: "#69db7c",
  },
};

export function getClassInfo(classification) {
  return (
    CLASS_INFO[classification] || {
      name: classification || "New Transient",
      emoji: "🔭",
      short: "Awaiting classification",
      description: "This transient was recently discovered and reported to the IAU Transient Name Server. It hasn't been spectroscopically classified yet, which means we don't know what type of event it is. Follow-up observations are needed.",
      color: "#adb5bd",
    }
  );
}

/**
 * Convert RA/Dec to approximate constellation.
 * This is a simplified lookup — uses rough boundaries, not IAU official boundaries.
 * Good enough for "it's in the direction of Orion" level context.
 */
const CONSTELLATIONS = [
  { name: "Orion", ra: [75, 95], dec: [-10, 15] },
  { name: "Sagittarius", ra: [260, 290], dec: [-35, -15] },
  { name: "Cassiopeia", ra: [345, 40], dec: [50, 65] },
  { name: "Ursa Major", ra: [150, 210], dec: [40, 65] },
  { name: "Leo", ra: [150, 180], dec: [-5, 30] },
  { name: "Virgo", ra: [180, 210], dec: [-20, 15] },
  { name: "Scorpius", ra: [240, 265], dec: [-45, -20] },
  { name: "Cygnus", ra: [290, 325], dec: [27, 55] },
  { name: "Taurus", ra: [50, 85], dec: [10, 30] },
  { name: "Gemini", ra: [90, 120], dec: [15, 35] },
  { name: "Aquarius", ra: [315, 355], dec: [-20, 5] },
  { name: "Pisces", ra: [345, 30], dec: [-5, 25] },
  { name: "Andromeda", ra: [350, 25], dec: [25, 50] },
  { name: "Perseus", ra: [40, 65], dec: [30, 55] },
  { name: "Pegasus", ra: [320, 355], dec: [10, 35] },
  { name: "Draco", ra: [180, 280], dec: [55, 75] },
  { name: "Centaurus", ra: [180, 220], dec: [-60, -30] },
  { name: "Lyra", ra: [275, 295], dec: [25, 45] },
  { name: "Aquila", ra: [285, 310], dec: [-10, 15] },
  { name: "Cepheus", ra: [310, 350], dec: [55, 80] },
  { name: "Bootes", ra: [210, 240], dec: [15, 50] },
  { name: "Hercules", ra: [240, 275], dec: [15, 50] },
  { name: "Canis Major", ra: [95, 115], dec: [-30, -15] },
  { name: "Puppis", ra: [110, 130], dec: [-40, -15] },
];

function raInRange(ra, range) {
  if (range[0] > range[1]) {
    // Wraps around 0 (e.g., Cassiopeia: 345-40)
    return ra >= range[0] || ra <= range[1];
  }
  return ra >= range[0] && ra <= range[1];
}

export function getConstellation(ra, dec) {
  for (const c of CONSTELLATIONS) {
    if (raInRange(ra, c.ra) && dec >= c.dec[0] && dec <= c.dec[1]) {
      return c.name;
    }
  }
  // Fallback: general direction
  if (dec > 45) return "the northern sky";
  if (dec < -45) return "the southern sky";
  return "deep space";
}

/**
 * Format a date into a human-friendly relative string.
 */
export function formatTimeSince(isoString) {
  if (!isoString) return "unknown";
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now - date;
  const mins = Math.floor(diffMs / 60000);
  const hours = Math.floor(mins / 60);
  const days = Math.floor(hours / 24);
  const months = Math.floor(days / 30);
  const years = Math.floor(days / 365);

  if (mins < 60) return `${mins} minutes ago`;
  if (hours < 24) return `${hours} hours ago`;
  if (days < 30) return `${days} days ago`;
  if (months < 12) return `${months} months ago`;
  return `${years} years ago`;
}

export function formatFirstSeen(isoString) {
  if (!isoString) return "";
  const date = new Date(isoString);
  return date.toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
  });
}

/**
 * Generate a one-line human summary for an alert.
 */
export function getAlertSummary(alert) {
  const info = getClassInfo(alert.classification);
  const constellation = getConstellation(alert.ra, alert.dec);
  const firstSeen = formatFirstSeen(alert.first_detection);
  const source = alert.broker_source === "tns" ? "Reported to IAU" : "Detected";

  if (!alert.classification) {
    return `${info.emoji} New transient discovered in ${constellation}${firstSeen ? ` on ${firstSeen}` : ""}. Awaiting spectroscopic classification.`;
  }
  if (alert.classification?.startsWith("SN")) {
    return `${info.emoji} ${info.short} spotted in ${constellation}${firstSeen ? `, first detected ${firstSeen}` : ""}. ${source} with ${alert.n_detections} observation${alert.n_detections !== 1 ? "s" : ""}.`;
  }
  if (alert.classification === "TDE") {
    return `${info.emoji} A star being torn apart by a black hole in ${constellation}. ${source} with ${alert.n_detections} observation${alert.n_detections !== 1 ? "s" : ""}.`;
  }
  if (alert.classification === "AGN" || alert.classification === "Blazar") {
    return `${info.emoji} A supermassive black hole actively feeding in ${constellation}. Monitored ${alert.n_detections} times since ${firstSeen || "discovery"}.`;
  }
  return `${info.emoji} ${info.short} detected in ${constellation}. ${alert.n_detections} observation${alert.n_detections !== 1 ? "s" : ""}.`;
}
