// @ts-check
/**
 * Reading a WhatsApp message object. Pure functions, no I/O.
 *
 * A message's real content can be buried under wrappers — a disappearing
 * message is an ephemeralMessage around the real one, a view-once photo is
 * viewOnceMessage(V2/V2Extension) around it, and an edit arrives as a
 * protocolMessage carrying editedMessage. unwrap() peels those first so
 * every other function here sees the actual content node.
 */

import { normalizeJid } from "./jid.js";

/** Wrappers that contain the real message rather than being one. */
const WRAPPERS = [
  "ephemeralMessage",
  "viewOnceMessage",
  "viewOnceMessageV2",
  "viewOnceMessageV2Extension",
  "documentWithCaptionMessage",
  "editedMessage",
];

/**
 * Peel wrapper layers off a message content node.
 * @param {any} content
 * @returns {any}
 */
export function unwrap(content) {
  let node = content;
  // Bounded: a malformed message must not spin here.
  for (let depth = 0; node && depth < 6; depth++) {
    const key = WRAPPERS.find((w) => node[w]?.message);
    if (!key) break;
    node = node[key].message;
  }
  return node ?? null;
}

/**
 * What sort of message this is, for raw.whatsapp_message.kind and from there
 * the canonical.interaction channel. Order matters: a voice note is an
 * audioMessage with ptt set, and must be distinguished from ordinary shared
 * audio because only voice notes get transcribed.
 * @param {any} content
 */
export function kindOf(content) {
  const m = unwrap(content);
  if (!m) return "other";
  if (m.audioMessage) return m.audioMessage.ptt ? "voice" : "audio";
  if (m.imageMessage) return "image";
  if (m.videoMessage) return m.videoMessage.gifPlayback ? "gif" : "video";
  if (m.stickerMessage) return "sticker";
  if (m.documentMessage) return "document";
  if (m.locationMessage || m.liveLocationMessage) return "location";
  if (m.contactMessage || m.contactsArrayMessage) return "contact_card";
  if (m.pollCreationMessage || m.pollCreationMessageV2 || m.pollCreationMessageV3)
    return "poll";
  if (m.reactionMessage) return "reaction";
  if (m.protocolMessage) return "protocol";
  if (m.conversation || m.extendedTextMessage) return "text";
  return "other";
}

/**
 * The searchable text. Captions count — an image captioned "let's do Tuesday"
 * is the message, and dropping it would lose the only part worth remembering.
 * @param {any} content
 */
export function textOf(content) {
  const m = unwrap(content);
  if (!m) return null;
  const t =
    m.conversation ??
    m.extendedTextMessage?.text ??
    m.imageMessage?.caption ??
    m.videoMessage?.caption ??
    m.documentMessage?.caption ??
    m.documentMessage?.fileName ??
    m.locationMessage?.name ??
    m.contactMessage?.displayName ??
    m.pollCreationMessage?.name ??
    m.pollCreationMessageV3?.name ??
    null;
  if (typeof t !== "string") return null;
  const trimmed = t.trim();
  return trimmed.length ? trimmed : null;
}

/** Every contextInfo on a message, wherever it hangs. @param {any} content */
function contextsOf(content) {
  const m = unwrap(content);
  if (!m) return [];
  return Object.values(m)
    .filter((v) => v && typeof v === "object" && "contextInfo" in v)
    .map((v) => /** @type {any} */ (v).contextInfo)
    .filter(Boolean);
}

/**
 * Mentions, normalized and deduped. WhatsApp delivers these structured, so
 * unlike the Telegram extractor there is no regex and no chance of matching a
 * handle inside a URL or an email address.
 * @param {any} content
 * @returns {string[]}
 */
export function mentionsOf(content) {
  /** @type {Set<string>} */
  const out = new Set();
  for (const ctx of contextsOf(content)) {
    for (const raw of ctx.mentionedJid ?? []) {
      const n = normalizeJid(raw);
      if (n) out.add(n);
    }
  }
  return [...out];
}

/** The id of the quoted message, if this is a reply. @param {any} content */
export function quotedIdOf(content) {
  for (const ctx of contextsOf(content)) {
    if (ctx.stanzaId) return String(ctx.stanzaId);
  }
  return null;
}

/**
 * WhatsApp timestamps are Unix seconds, and arrive as a number, a string, or
 * a protobuf Long depending on the path. Returns null rather than an Invalid
 * Date so the caller can skip the row instead of writing a broken one.
 * @param {any} ts
 * @returns {Date | null}
 */
export function timestampToDate(ts) {
  if (ts == null) return null;
  const secs =
    typeof ts === "number"
      ? ts
      : typeof ts === "string"
        ? Number(ts)
        : typeof ts?.toNumber === "function"
          ? ts.toNumber()
          : typeof ts?.low === "number"
            ? ts.low
            : NaN;
  if (!Number.isFinite(secs) || secs <= 0) return null;
  const d = new Date(secs * 1000);
  // WhatsApp launched in 2009; anything before that is a decoding error, and
  // far-future values are corruption. Better to drop than to poison the
  // timeline that follow-ups and cadences are computed from.
  const year = d.getUTCFullYear();
  if (year < 2009 || year > new Date().getUTCFullYear() + 1) return null;
  return d;
}

/**
 * Kinds that carry no memory value and are never written: reactions, delivery
 * receipts, key distribution, revokes and other protocol chatter.
 * @param {string} kind
 */
export function isSkippableKind(kind) {
  return kind === "reaction" || kind === "protocol";
}
