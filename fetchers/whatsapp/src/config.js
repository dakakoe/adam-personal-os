// @ts-check
/**
 * Env -> config. Mirrors fetchers/telegram/fetcher/config.py deliberately:
 * a <WORKER>_DATABASE_URL override, else the same URL built from the shared
 * POSTGRES_* vars every other worker reads out of
 * /srv/memory/secrets/.env.
 */

/** @param {string} name */
function required(name) {
  const v = process.env[name];
  if (!v) {
    throw new Error(`missing required env: ${name}`);
  }
  return v;
}

/** @param {string} name @param {boolean} dflt */
function boolEnv(name, dflt) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return dflt;
  return !["0", "false", "no", "off"].includes(raw.trim().toLowerCase());
}

function buildDbUrl() {
  const user = encodeURIComponent(required("POSTGRES_USER"));
  const pw = encodeURIComponent(required("POSTGRES_PASSWORD"));
  const db = required("POSTGRES_DB");
  const host = process.env.POSTGRES_HOST || "127.0.0.1";
  const port = process.env.POSTGRES_PORT || "5432";
  return `postgres://${user}:${pw}@${host}:${port}/${db}`;
}

export function load() {
  return {
    dbUrl: process.env.WHATSAPP_DATABASE_URL || buildDbUrl(),
    sessionDir: process.env.WHATSAPP_SESSION_DIR || "/srv/memory/apps/whatsapp/session",
    voiceDir: process.env.WHATSAPP_VOICE_DIR || "/srv/memory/data/whatsapp-voice",
    mediaDir: process.env.WHATSAPP_MEDIA_DIR || "/srv/memory/data/whatsapp-media",
    // Only needed to request a pairing code; the live listener reads the
    // number back out of the saved session.
    phone: process.env.WHATSAPP_PHONE || "",
    downloadVoice: boolEnv("WHATSAPP_DOWNLOAD_VOICE", true),
    // Images and video are off by default: they cost disk and, unlike a voice
    // note, nothing downstream can read them.
    downloadMedia: boolEnv("WHATSAPP_DOWNLOAD_MEDIA", false),
    pairTimeoutSec: Number(process.env.WHATSAPP_PAIR_TIMEOUT_SEC || 180),
    logLevel: process.env.WHATSAPP_LOG_LEVEL || "info",
    sourceKey: "whatsapp",
  };
}

/** @typedef {ReturnType<typeof load>} Config */
