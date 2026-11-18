// @ts-check
/**
 * Linking this droplet as a WhatsApp companion device, by pairing code.
 *
 * Pairing code rather than QR: the droplet is headless, and an 8-character
 * code the user types into their phone survives a web UI, an SSH session and
 * a log file, none of which a QR bitmap does.
 *
 * This is the one place WhatsApp genuinely diverges from the Telegram setup
 * wizard. Telegram's auth is two short subprocesses that both terminate, so
 * merge_api can just communicate() with them. Pairing is ONE long-lived
 * process that prints a code and then has to keep running until the user acts
 * on it. So we do two things: emit stable marker lines on stdout (parsed by
 * merge_api/setup_flow.py) AND mirror progress into memory.source_status, so
 * the wizard can poll rather than hold a pipe open for three minutes.
 *
 * Markers, with the exit code they precede:
 *   already_authorized            0   a session already exists
 *   pairing_code code=ABCD1234    -   printed immediately, process continues
 *   paired jid=<jid>              0   the phone accepted the code
 *   pair_timeout                  4   nobody entered it in time
 *   bad_phone                     2   WHATSAPP_PHONE missing or not digits
 *   session_exists                2   caller must run `reset` first
 */

import { Boom } from "@hapi/boom";
import makeWASocket, {
  Browsers,
  DisconnectReason,
  fetchLatestBaileysVersion,
  jidNormalizedUser,
  makeCacheableSignalKeyStore,
} from "baileys";

import * as db from "./db.js";
import { makeBaileysLogger } from "./log.js";

export const EXIT_OK = 0;
export const EXIT_BAD_INPUT = 2;
export const EXIT_TIMEOUT = 4;

/** stdout, unbuffered enough that the wizard sees it right away. @param {string} line */
function marker(line) {
  process.stdout.write(line + "\n");
}

/**
 * Digits only. WhatsApp wants a full international number with no '+',
 * no spaces and no punctuation.
 * @param {string} phone
 */
export function normalizePhone(phone) {
  const digits = String(phone ?? "").replace(/[^0-9]/g, "");
  return digits.length >= 8 && digits.length <= 20 ? digits : null;
}

/**
 * @param {object} args
 * @param {import('pg').Pool} args.pool
 * @param {import('./config.js').Config} args.cfg
 * @param {import('pino').Logger} args.log
 * @param {any} args.authState
 * @param {boolean} args.alreadyRegistered
 * @returns {Promise<number>}
 */
export async function run({ pool, cfg, log, authState, alreadyRegistered }) {
  if (alreadyRegistered) {
    marker("already_authorized");
    return EXIT_OK;
  }

  const phone = normalizePhone(cfg.phone);
  if (!phone) {
    marker("bad_phone");
    log.error("WHATSAPP_PHONE must be a full international number, digits only");
    return EXIT_BAD_INPUT;
  }

  const { version } = await fetchLatestBaileysVersion();
  const blog = makeBaileysLogger(cfg.logLevel);
  const sock = makeWASocket({
    version,
    logger: blog,
    auth: {
      creds: authState.state.creds,
      keys: makeCacheableSignalKeyStore(authState.state.keys, blog),
    },
    printQRInTerminal: false,
    markOnlineOnConnect: false,
    syncFullHistory: false,
    // See the note in live.js: the standard identity, not a custom one, and
    // it must match what the live socket uses.
    browser: Browsers.ubuntu("Chrome"),
  });

  sock.ev.on("creds.update", authState.saveCreds);

  /** @type {(code: number) => void} */
  let settle;
  /** @type {Promise<number>} */
  const done = new Promise((resolve) => {
    settle = resolve;
  });

  const timer = setTimeout(() => {
    marker("pair_timeout");
    log.error({ seconds: cfg.pairTimeoutSec }, "nobody entered the pairing code in time");
    settle(EXIT_TIMEOUT);
  }, cfg.pairTimeoutSec * 1000);

  // Request the code only once the server has actually offered pairing.
  //
  // A fixed sleep here is a coin flip: too early and the noise handshake has
  // not finished, so the code comes back looking valid but the phone rejects
  // it with "Couldn't link device". The `qr` field appearing on a
  // connection.update is the server saying it is ready to pair a companion
  // device, which is the real signal to wait for.
  let requested = false;
  async function requestCode() {
    if (requested) return;
    requested = true;
    try {
      const code = await sock.requestPairingCode(phone);
      const pretty = String(code).toUpperCase();
      marker(`pairing_code code=${pretty}`);
      log.info(
        { code: pretty, expiresInSec: cfg.pairTimeoutSec, phoneDigits: phone.length },
        "enter this in WhatsApp: Settings, Linked Devices, Link a Device, " +
          "Link with phone number instead",
      );
      await db.markSourceStatus(
        pool,
        cfg.sourceKey,
        "needs_attention",
        `Pairing code ${pretty} — in WhatsApp go to Settings, Linked Devices, ` +
          "Link a Device, then Link with phone number instead",
        log,
      );
    } catch (err) {
      clearTimeout(timer);
      log.error({ err }, "requestPairingCode failed");
      marker("bad_phone");
      settle(EXIT_BAD_INPUT);
    }
  }

  sock.ev.on("connection.update", (u) => {
    const { connection, lastDisconnect, qr } = u;
    if (qr) void requestCode();
    if (connection === "open") {
      clearTimeout(timer);
      const jid = sock.user?.id ? jidNormalizedUser(sock.user.id) : "unknown";
      marker(`paired jid=${jid}`);
      log.info({ jid }, "paired");
      void db
        .markSourceStatus(pool, cfg.sourceKey, "ok", null, log)
        .then(() => settle(EXIT_OK));
      return;
    }
    if (connection !== "close") return;

    const code = new Boom(lastDisconnect?.error)?.output?.statusCode;
    // 515 immediately after pairing is normal and means "reconnect" — but the
    // creds are already saved by then, so the live unit will pick them up and
    // we can call this a success.
    if (code === DisconnectReason.restartRequired) {
      clearTimeout(timer);
      const jid = sock.user?.id ? jidNormalizedUser(sock.user.id) : "unknown";
      marker(`paired jid=${jid}`);
      log.info("paired (restart required, creds saved)");
      void db
        .markSourceStatus(pool, cfg.sourceKey, "ok", null, log)
        .then(() => settle(EXIT_OK));
      return;
    }
    log.warn({ code }, "connection closed during pairing");
  });

  // Fallback: if no `qr` ever arrives (older server behaviour, or an event we
  // don't see), ask anyway rather than sitting silent until the timeout.
  setTimeout(() => void requestCode(), 10_000).unref?.();

  return done;
}
