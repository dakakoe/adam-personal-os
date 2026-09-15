// @ts-check
/**
 * Entry point. Subcommands: live | pair | reset.
 *
 * Mirrors fetchers/telegram/fetcher/__main__.py — hardened umask first,
 * logging to stderr, one subcommand per process, explicit exit codes.
 */

import { access, mkdir, rename } from "node:fs/promises";
import path from "node:path";
import { useMultiFileAuthState } from "baileys";

import * as config from "./config.js";
import * as db from "./db.js";
import * as live from "./live.js";
import * as pair from "./pair.js";
import { makeLogger } from "./log.js";

/**
 * Everything this process writes — the session, voice notes — is
 * auth-token-equivalent or private message content. Same reasoning as the
 * Telegram fetcher's _harden_umask.
 */
process.umask(0o077);

/** @param {string} p */
async function exists(p) {
  try {
    await access(p);
    return true;
  } catch {
    return false;
  }
}

/** @param {import('./config.js').Config} cfg */
async function loadAuth(cfg) {
  await mkdir(cfg.sessionDir, { recursive: true, mode: 0o700 });
  const { state, saveCreds } = await useMultiFileAuthState(cfg.sessionDir);
  return { state, saveCreds };
}

/**
 * Registered means the phone has accepted a pairing and the creds are usable.
 * @param {any} authState
 */
function isRegistered(authState) {
  return authState.state?.creds?.registered === true;
}

async function main() {
  const cmd = process.argv[2] || "live";
  const cfg = config.load();
  const log = makeLogger(cfg.logLevel);

  if (cmd === "reset") {
    // Archive rather than delete: a logged-out session is useless for
    // reconnecting but may still be wanted for forensics, and an
    // irreversible rm in an auth path is how people lose things.
    if (!(await exists(path.join(cfg.sessionDir, "creds.json")))) {
      log.info({ dir: cfg.sessionDir }, "no session to reset");
      return 0;
    }
    const backup = `${cfg.sessionDir}.bak.${Date.now()}`;
    await rename(cfg.sessionDir, backup);
    await mkdir(cfg.sessionDir, { recursive: true, mode: 0o700 });
    log.info({ backup }, "session archived; run `pair` to link again");
    return 0;
  }

  const pool = db.connect(cfg.dbUrl);
  try {
    const authState = await loadAuth(cfg);

    if (cmd === "pair") {
      // Refuse to pair over a live session: requesting a code against
      // registered creds is how you end up with a half-written auth store.
      if (isRegistered(authState)) {
        process.stdout.write("session_exists\n");
        log.error("a session already exists; run `reset` first");
        return pair.EXIT_BAD_INPUT;
      }
      return await pair.run({ pool, cfg, log, authState, alreadyRegistered: false });
    }

    if (cmd !== "live") {
      log.error({ cmd }, "unknown subcommand (expected: live | pair | reset)");
      return 2;
    }

    if (!isRegistered(authState)) {
      // No TTY here, so we cannot prompt. Flag it where the user will see it
      // and exit — same contract as the Telegram live command when its
      // session is unauthorized.
      await db.markSourceStatus(
        pool,
        cfg.sourceKey,
        "needs_attention",
        "WhatsApp is not paired — link a device from Setup, or run " +
          "`node src/index.js pair` on the droplet",
        log,
      );
      log.error("not paired; flagged needs_attention");
      return live.EXIT_FAIL;
    }

    log.info({ voiceDir: cfg.voiceDir, downloadVoice: cfg.downloadVoice }, "starting listener");
    return await live.run({ pool, cfg, log, authState });
  } finally {
    await pool.end().catch(() => {});
  }
}

main()
  .then((code) => process.exit(code ?? 0))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
