// @ts-check
/**
 * Logging to stderr, matching the Python workers' convention so
 * /srv/memory/logs/whatsapp.log reads like the rest of them.
 *
 * Baileys wants a pino instance of its own and is extremely chatty at debug
 * level, so it gets a silent child unless WHATSAPP_LOG_LEVEL says otherwise.
 */

import pino from "pino";

/** @param {string} level */
export function makeLogger(level = "info") {
  return pino(
    {
      level,
      base: undefined,
      timestamp: pino.stdTimeFunctions.isoTime,
      formatters: { level: (label) => ({ level: label }) },
    },
    pino.destination(2),
  );
}

/**
 * Baileys' own logger. Silent by default — its transport-level chatter is
 * noise next to one line per ingested message, and at debug level it will
 * print message contents into the log file.
 * @param {string} level
 */
export function makeBaileysLogger(level) {
  return makeLogger(level === "debug" ? "debug" : "silent");
}
