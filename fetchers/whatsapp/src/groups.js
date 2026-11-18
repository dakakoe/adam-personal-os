// @ts-check
/**
 * Auto-follow for small WhatsApp groups — the owner's "follow group chats up
 * to N members" setting, set from /groups?source=whatsapp. Same rule as the
 * Telegram fetcher's should_auto_follow, minus channels (WhatsApp channels and
 * broadcasts never reach the allowlist; they are dropped at the door).
 */

export const AUTO_FOLLOW_SETTING = "whatsapp_auto_follow_max_members";

/**
 * A group with a KNOWN size within the limit. Unknown size (metadata fetch
 * failed) is left alone rather than guessed, and 0 means the setting is off.
 * @param {number|null|undefined} memberCount
 * @param {number} maxMembers
 */
export function shouldAutoFollow(memberCount, maxMembers) {
  return (
    Number.isInteger(maxMembers) &&
    maxMembers > 0 &&
    typeof memberCount === "number" &&
    memberCount > 0 &&
    memberCount <= maxMembers
  );
}

/**
 * The stored setting value as a whole number of members (0 = off). The row is
 * jsonb, so node-postgres hands back a number, but a string or junk must not
 * turn auto-follow on by accident.
 * @param {unknown} value
 */
export function parseMaxMembers(value) {
  const n = typeof value === "string" ? Number(value) : value;
  return typeof n === "number" && Number.isInteger(n) && n > 0 ? n : 0;
}
