// @ts-check
/**
 * The live listener: hold a linked-device socket, write raw rows, nothing else.
 *
 * READ-ONLY BY CONSTRUCTION. There is no sock.sendMessage call in this file or
 * anywhere else in this package, and that absence is the enforced boundary of
 * the whole feature. Automated sending is what actually gets WhatsApp accounts
 * banned; passive listening is what we do instead.
 *
 * Shaped after fetchers/telegram/fetcher/live.py — same allowlist gate, same
 * per-message try/catch so one malformed message can never kill the socket,
 * same source_status health writes.
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
import * as media from "./media.js";
import {
  chatKeyFor,
  chatKind,
  isGroup,
  isIgnorable,
  isLid,
  normalizeJid,
  toE164,
} from "./jid.js";
import { isSkippableKind, kindOf, mentionsOf, quotedIdOf, textOf, timestampToDate } from "./message.js";
import { makeBaileysLogger } from "./log.js";

/** Exit code paired with RestartPreventExitStatus=3 in the systemd unit. */
export const EXIT_STOP = 3;
/** Ordinary failure; systemd Restart=on-failure will retry. */
export const EXIT_FAIL = 1;

const RECONNECT_MIN_MS = 1_000;
const RECONNECT_MAX_MS = 60_000;
/** Group subjects change rarely; fetching them per message is a ban vector. */
const GROUP_META_TTL_MS = 6 * 60 * 60 * 1000;

/**
 * @param {object} args
 * @param {import('pg').Pool} args.pool
 * @param {import('./config.js').Config} args.cfg
 * @param {import('pino').Logger} args.log
 * @param {any} args.authState
 * @returns {Promise<number>} process exit code
 */
export async function run({ pool, cfg, log, authState }) {
  /** @type {Map<string, {title: string|null, memberCount: number|null, at: number}>} */
  const groupMeta = new Map();
  let backoff = RECONNECT_MIN_MS;

  for (;;) {
    const outcome = await connectOnce({ pool, cfg, log, authState, groupMeta });

    if (outcome.action === "exit") return outcome.code;
    if (outcome.action === "reconnect-now") {
      backoff = RECONNECT_MIN_MS;
      log.info("reconnecting immediately");
      continue;
    }
    // Full jitter, so a WhatsApp-side outage doesn't turn every client on
    // earth into a synchronized retry storm.
    const wait = Math.floor(Math.random() * backoff);
    log.info({ waitMs: wait }, "reconnecting after backoff");
    await sleep(wait);
    backoff = Math.min(backoff * 2, RECONNECT_MAX_MS);
  }
}

/**
 * One socket lifetime. Resolves when the connection closes, saying what the
 * caller should do next.
 * @returns {Promise<{action: 'exit', code: number} | {action: 'reconnect' | 'reconnect-now'}>}
 */
function connectOnce({ pool, cfg, log, authState, groupMeta }) {
  return new Promise((resolve) => {
    let settled = false;
    /** @param {any} r */
    const finish = (r) => {
      if (!settled) {
        settled = true;
        resolve(r);
      }
    };

    (async () => {
      const { version } = await fetchLatestBaileysVersion();
      const blog = makeBaileysLogger(cfg.logLevel);
      const sock = makeWASocket({
        version,
        logger: blog,
        auth: {
          creds: authState.state.creds,
          // Caching the signal key store cuts a lot of disk churn on a
          // session with hundreds of pre-keys.
          keys: makeCacheableSignalKeyStore(authState.state.keys, blog),
        },
        printQRInTerminal: false,
        // Do NOT mark ourselves online. Presence would make the phone stop
        // pushing notifications to the user's own devices, and a number that
        // is online 24/7 is exactly the signal we don't want to emit.
        markOnlineOnConnect: false,
        // History comes from the iPhone backup importer, not from here.
        syncFullHistory: false,
        // What the user sees in WhatsApp -> Linked Devices.
        // See the note in live.js: the standard identity, not a custom one, and
    // it must match what the live socket uses.
    browser: Browsers.ubuntu("Chrome"),
        // We keep no message store, so an occasional undecryptable message is
        // accepted rather than maintaining one just to answer retries.
        getMessage: async () => undefined,
      });

      sock.ev.on("creds.update", authState.saveCreds);

      sock.ev.on("connection.update", (u) => {
        const { connection, lastDisconnect } = u;
        if (connection === "open") {
          log.info("connected");
          void db.markSourceStatus(pool, cfg.sourceKey, "ok", null, log);
          return;
        }
        if (connection !== "close") return;

        const code = new Boom(lastDisconnect?.error)?.output?.statusCode;
        const name = /** @type {any} */ (DisconnectReason)[String(code)] ?? "unknown";
        log.warn({ code, name }, "connection closed");

        void (async () => {
          switch (code) {
            case DisconnectReason.loggedOut:
            case DisconnectReason.badSession:
            case DisconnectReason.multideviceMismatch:
            case DisconnectReason.forbidden: {
              // The session is dead and no amount of retrying revives it. Say
              // so where the user will see it and stop, rather than looking
              // healthy while silently ingesting nothing.
              await db.markSourceStatus(
                pool,
                cfg.sourceKey,
                "needs_attention",
                `WhatsApp session ended (${name}) — re-pair from Setup, or run ` +
                  "`node src/index.js reset` then `pair` on the droplet",
                log,
              );
              log.error({ name }, "session ended; flagged needs_attention");
              return finish({ action: "exit", code: EXIT_FAIL });
            }
            case DisconnectReason.connectionReplaced: {
              // Another client took this session. Retrying would start a
              // tug-of-war that drops messages for both; stop instead.
              await db.markSourceStatus(
                pool,
                cfg.sourceKey,
                "needs_attention",
                "Session claimed by another WhatsApp client — only one instance " +
                  "may run against a linked device",
                log,
              );
              log.error("session replaced by another client; stopping");
              return finish({ action: "exit", code: EXIT_STOP });
            }
            case DisconnectReason.restartRequired:
              // Normal, and expected right after pairing.
              return finish({ action: "reconnect-now" });
            default:
              return finish({ action: "reconnect" });
          }
        })();
      });

      // --- directory events -------------------------------------------------
      // These are how we learn names and, crucially, LID<->phone pairings.

      sock.ev.on("contacts.upsert", (cs) => void onContacts(cs));
      sock.ev.on("contacts.update", (cs) => void onContacts(cs));

      sock.ev.on("messaging-history.set", ({ contacts, chats }) => {
        // We deliberately ignore the `messages` half: history is the
        // importer's job, and syncFullHistory is off anyway. Contacts and
        // chats are worth taking, because this is where group subjects and
        // address-book names arrive in bulk.
        void onContacts(contacts ?? []);
        void onChats(chats ?? []);
      });

      sock.ev.on("chats.upsert", (cs) => void onChats(cs));
      sock.ev.on("chats.update", (cs) => void onChats(cs));

      sock.ev.on("groups.upsert", (gs) => void onGroups(gs));
      sock.ev.on("groups.update", (gs) => void onGroups(gs));

      sock.ev.on("messages.upsert", ({ messages, type }) => {
        if (type !== "notify" && type !== "append") return;
        void (async () => {
          for (const msg of messages) {
            try {
              await handleMessage(msg);
            } catch (err) {
              // One bad message must never take down the listener.
              log.error({ err, id: msg?.key?.id }, "message handler failed");
            }
          }
        })();
      });

      // --- handlers ---------------------------------------------------------

      /** @param {any[]} contacts */
      async function onContacts(contacts) {
        for (const c of contacts ?? []) {
          try {
            const jid = normalizeJid(c.id);
            if (!jid || isIgnorable(jid) || isGroup(jid)) continue;
            const altRaw = c.lid ?? c.jid ?? c.phoneNumber ?? null;
            const alt = normalizeJid(altRaw);
            await db.upsertContact(pool, {
              jid,
              phoneE164: toE164(jid) ?? toE164(alt),
              lid: isLid(jid) ? jid : isLid(alt) ? alt : null,
              pushName: c.notify ?? c.name ?? null,
              // c.name is WhatsApp's address-book name when it has one. It is
              // higher trust than pushName, which the contact chooses.
              notifyName: c.name ?? null,
              businessName: c.verifiedName ?? null,
              payload: c,
            });
            await learnLid(jid, alt);
          } catch (err) {
            log.warn({ err, id: c?.id }, "contact upsert failed");
          }
        }
      }

      /** @param {any[]} chats */
      async function onChats(chats) {
        for (const c of chats ?? []) {
          try {
            const jid = normalizeJid(c.id);
            const key = chatKeyFor(jid);
            if (!jid || !key) continue;
            await db.upsertChat(pool, {
              chatJid: jid,
              chatKey: key,
              kind: chatKind(jid),
              title: c.name ?? c.subject ?? null,
              payload: {},
            });
            if (isGroup(jid)) {
              await db.upsertGroupAllowlist(pool, {
                chatJid: jid,
                title: c.name ?? c.subject ?? null,
              });
            }
          } catch (err) {
            log.warn({ err, id: c?.id }, "chat upsert failed");
          }
        }
      }

      /** @param {any[]} groups */
      async function onGroups(groups) {
        for (const g of groups ?? []) {
          try {
            const jid = normalizeJid(g.id);
            if (!jid || !isGroup(jid)) continue;
            const title = g.subject ?? null;
            const memberCount = g.participants?.length ?? g.size ?? null;
            groupMeta.set(jid, { title, memberCount, at: Date.now() });
            await db.upsertGroupAllowlist(pool, { chatJid: jid, title, memberCount });
          } catch (err) {
            log.warn({ err, id: g?.id }, "group upsert failed");
          }
        }
      }

      /**
       * Group subject and size, cached. Never fetched on the message hot path
       * without a cache miss — a network round trip per message is both slow
       * and the kind of traffic pattern that gets a client flagged.
       * @param {string} jid
       */
      async function groupInfo(jid) {
        const hit = groupMeta.get(jid);
        if (hit && Date.now() - hit.at < GROUP_META_TTL_MS) return hit;
        try {
          const meta = await sock.groupMetadata(jid);
          const info = {
            title: meta?.subject ?? null,
            memberCount: meta?.participants?.length ?? null,
            at: Date.now(),
          };
          groupMeta.set(jid, info);
          return info;
        } catch (err) {
          log.warn({ err, jid }, "groupMetadata failed");
          const stale = { title: null, memberCount: null, at: Date.now() };
          groupMeta.set(jid, stale);
          return stale;
        }
      }

      /** @param {string|null} a @param {string|null} b */
      async function learnLid(a, b) {
        if (!a || !b) return;
        const lid = isLid(a) ? a : isLid(b) ? b : null;
        const phone = toE164(a) ? a : toE164(b) ? b : null;
        if (lid && phone) await db.recordLidMapping(pool, lid, phone);
      }

      /** @param {any} msg */
      async function handleMessage(msg) {
        const chatJid = normalizeJid(msg.key?.remoteJid);
        if (!chatJid || isIgnorable(chatJid)) return;

        const chatKey = chatKeyFor(chatJid);
        if (!chatKey) return;

        const kind = kindOf(msg.message);
        if (isSkippableKind(kind)) return;

        const messageDate = timestampToDate(msg.messageTimestamp);
        if (!messageDate) {
          log.warn({ id: msg.key?.id }, "unusable timestamp; skipping");
          return;
        }
        const sourceMessageId = msg.key?.id ? String(msg.key.id) : null;
        if (!sourceMessageId) return;

        const fromMe = msg.key?.fromMe === true;
        const group = isGroup(chatJid);

        if (group) {
          // Opt-in, exactly like Telegram: record the group so it can be
          // listed and enabled later, then drop the message unless it has
          // been enabled. Stamping last_message_at here is what makes a
          // "recently active" sort reflect the chat rather than our scan.
          const info = await groupInfo(chatJid);
          await db.upsertGroupAllowlist(pool, {
            chatJid,
            title: info.title,
            memberCount: info.memberCount,
            lastMessageAt: messageDate,
          });
          if (!(await db.isGroupEnabled(pool, chatJid))) return;
        }

        // In a group the sender is the participant; in a DM it is the chat
        // itself for inbound, and us for outbound.
        const senderJid = group
          ? normalizeJid(msg.key?.participant ?? msg.participant)
          : fromMe
            ? normalizeJid(sock.user?.id ? jidNormalizedUser(sock.user.id) : null)
            : chatJid;

        await db.upsertChat(pool, {
          chatJid,
          chatKey,
          kind: chatKind(chatJid),
          title: group ? (await groupInfo(chatJid)).title : null,
          payload: {},
        });

        // In a DM the chat IS the counterparty, whichever direction the
        // message went, so record them even when we sent it. Skipping
        // outbound here meant a conversation you started — the normal case
        // for reaching out to someone — produced a message row with no
        // contact, hence no person, hence an interaction attached to nobody.
        //
        // pushName on an outbound message is OUR OWN name, never theirs, so
        // it must not be written as the counterparty's.
        if (!group) {
          await db.upsertContact(pool, {
            jid: chatJid,
            phoneE164: toE164(chatJid),
            lid: isLid(chatJid) ? chatJid : null,
            pushName: fromMe ? null : (msg.pushName ?? null),
            payload: {},
          });
        } else if (senderJid && !fromMe) {
          // Group senders only when it isn't us: recording ourselves would
          // create a canonical person for the account owner.
          await db.upsertContact(pool, {
            jid: senderJid,
            phoneE164: toE164(senderJid),
            lid: isLid(senderJid) ? senderJid : null,
            pushName: msg.pushName ?? null,
            payload: {},
          });
        }

        // Newer Baileys exposes the other representation of a jid alongside
        // it; that pairing is the only way a LID-only contact ever becomes a
        // phone identity.
        await learnLid(chatJid, normalizeJid(msg.key?.remoteJidAlt));
        await learnLid(senderJid, normalizeJid(msg.key?.participantAlt));

        const rowId = await db.insertMessage(pool, {
          chatJid,
          chatKey,
          sourceMessageId,
          fromMe,
          senderJid,
          senderPhoneE164: toE164(senderJid),
          messageDate,
          kind,
          text: textOf(msg.message),
          voiceFilePath: null,
          mediaFilePath: null,
          quotedMessageId: quotedIdOf(msg.message),
          mentionedJids: mentionsOf(msg.message),
          payload: msg,
        });

        log.info(
          { chat: chatKey, group, kind, fromMe, id: sourceMessageId, new: rowId !== null },
          "message",
        );

        if (rowId === null) return; // duplicate; media already handled

        // Media is fetched AFTER the row exists, so a slow or expired
        // download never costs us the record that the message happened.
        const wantVoice = cfg.downloadVoice && kind === "voice";
        const wantMedia = cfg.downloadMedia && ["image", "video", "gif", "document", "sticker"].includes(kind);
        if (!wantVoice && !wantMedia) return;

        const dir = wantVoice ? cfg.voiceDir : cfg.mediaDir;
        const p = await media.download({
          msg, kind, chatKey, messageId: sourceMessageId, dir, sock, log,
        });
        if (!p) return;
        if (wantVoice) await db.setVoicePath(pool, rowId, p);
        else await db.setMediaPath(pool, rowId, p);
      }
    })().catch((err) => {
      log.error({ err }, "socket setup failed");
      finish({ action: "reconnect" });
    });
  });
}

/** @param {number} ms */
function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
