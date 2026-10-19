// @ts-check
/**
 * JID handling. Pure functions, no I/O — this is the module the whole
 * ingest's correctness rests on, so it is the one that gets tests.
 *
 * WhatsApp identifies things with "JIDs" and hands us several shapes:
 *
 *   3725551234@s.whatsapp.net      a person, by phone number
 *   3725551234:12@s.whatsapp.net   the same person, from linked device 12
 *   987654321@lid                  a person whose number is hidden from us
 *   120363012345678901@g.us        a group
 *   status@broadcast               status updates
 *   1234-5678@broadcast            a broadcast list
 *   abc123@newsletter              a channel
 *
 * The device suffix is the one that quietly breaks things: the same human
 * arrives under a different JID depending on which of their phones sent the
 * message, so nothing may be compared or stored before normalizeJid().
 */

/** @param {string | null | undefined} jid */
export function normalizeJid(jid) {
  if (!jid) return null;
  const at = jid.indexOf("@");
  if (at < 0) return null;
  const server = jid.slice(at + 1);
  let user = jid.slice(0, at);
  // Strip the device suffix ('3725551234:12' -> '3725551234') and any
  // agent suffix WhatsApp occasionally appends after an underscore.
  const colon = user.indexOf(":");
  if (colon >= 0) user = user.slice(0, colon);
  const underscore = user.indexOf("_");
  if (underscore >= 0) user = user.slice(0, underscore);
  if (!user) return null;
  return `${user}@${server}`;
}

/** @param {string | null | undefined} jid */
export function isGroup(jid) {
  return typeof jid === "string" && jid.endsWith("@g.us");
}

/** @param {string | null | undefined} jid */
export function isLid(jid) {
  return typeof jid === "string" && jid.endsWith("@lid");
}

/**
 * Everything we deliberately never ingest: status posts, broadcast lists and
 * channels. None of them are a conversation with a person, and channels in
 * particular would flood the corpus with marketing.
 * @param {string | null | undefined} jid
 */
export function isIgnorable(jid) {
  if (typeof jid !== "string") return true;
  return (
    jid === "status@broadcast" ||
    jid.endsWith("@broadcast") ||
    jid.endsWith("@newsletter")
  );
}

/**
 * Bare international digits for a phone JID, else null. No '+', no spaces —
 * the same shape enrichment's _norm_phone() produces, so WhatsApp numbers
 * compare directly against Telegram's without further work.
 * @param {string | null | undefined} jid
 */
export function toE164(jid) {
  const n = normalizeJid(jid);
  if (!n || !n.endsWith("@s.whatsapp.net")) return null;
  const user = n.slice(0, n.indexOf("@"));
  return /^[0-9]{5,20}$/.test(user) ? user : null;
}

/**
 * The stable handle a conversation is stored and joined under.
 *
 *   group          -> the full '@g.us' jid (groups have no LID duality)
 *   phone chat     -> bare digits
 *   LID-only chat  -> 'lid:<n>', so it is addressable but never mistaken
 *                     for a phone number, and greppable when the LID is
 *                     later resolved to one
 *
 * Returns null for anything we do not ingest.
 * @param {string | null | undefined} jid
 */
export function chatKeyFor(jid) {
  const n = normalizeJid(jid);
  if (!n || isIgnorable(n)) return null;
  if (isGroup(n)) return n;
  if (isLid(n)) return `lid:${n.slice(0, n.indexOf("@"))}`;
  return toE164(n);
}

/** Conversation kind, for raw.whatsapp_chat.kind. @param {string} jid */
export function chatKind(jid) {
  if (isGroup(jid)) return "group";
  if (typeof jid === "string" && jid.endsWith("@newsletter")) return "newsletter";
  if (typeof jid === "string" && jid.endsWith("@broadcast")) return "broadcast";
  return "private";
}

/**
 * Safe for a filename. WhatsApp chat keys contain '@', '.' and '-', and group
 * JIDs are long — this is why voice paths are STORED rather than rebuilt
 * downstream the way the Telegram ones are.
 * @param {string} s
 */
export function sanitizeForPath(s) {
  return String(s).replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 120);
}
