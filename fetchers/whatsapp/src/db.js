// @ts-check
/**
 * Every SQL statement the bridge runs. Writes to raw.* only — this process
 * never touches canonical or memory, apart from the source_status health row
 * the Sources page reads.
 *
 * node-postgres does not use server-side prepared statements by default, so
 * there is no equivalent of the Python workers' statement_cache_size=0.
 */

import pg from "pg";

/** @param {string} dbUrl */
export function connect(dbUrl) {
  return new pg.Pool({
    connectionString: dbUrl,
    min: 0,
    max: 4,
    idleTimeoutMillis: 30_000,
    // The socket can idle for hours between messages; a stuck connect must
    // not wedge the whole listener.
    connectionTimeoutMillis: 10_000,
  });
}

/**
 * Flag the source's health for the Sources page. Best-effort by design: a
 * status write must NEVER take down ingest, so every error is swallowed —
 * same contract as db.mark_source_status in the Telegram fetcher.
 * @param {import('pg').Pool} pool
 * @param {string} sourceKey
 * @param {'ok'|'needs_attention'} status
 * @param {string | null} reason
 * @param {import('pino').Logger} [log]
 */
export async function markSourceStatus(pool, sourceKey, status, reason, log) {
  try {
    await pool.query(
      `INSERT INTO memory.source_status (source_key, status, reason, updated_at)
       VALUES ($1, $2, $3, now())
       ON CONFLICT (source_key) DO UPDATE
          SET status = EXCLUDED.status,
              reason = EXCLUDED.reason,
              updated_at = now()`,
      [sourceKey, status, reason],
    );
  } catch (err) {
    log?.warn({ err, sourceKey }, "source_status upsert failed (non-fatal)");
  }
}

/**
 * @param {import('pg').Pool} pool
 * @param {{jid: string, phoneE164?: string|null, lid?: string|null,
 *          pushName?: string|null, notifyName?: string|null,
 *          businessName?: string|null, isMe?: boolean, payload?: object}} c
 */
export async function upsertContact(pool, c) {
  await pool.query(
    // COALESCE(EXCLUDED, existing) everywhere: a later sighting that happens
    // to lack a name must never erase one we already learned. notify_name in
    // particular only ever arrives from the iPhone import.
    `INSERT INTO raw.whatsapp_contact
       (jid, phone_e164, lid, push_name, notify_name, business_name, is_me, payload)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
     ON CONFLICT (jid) DO UPDATE SET
       phone_e164    = COALESCE(EXCLUDED.phone_e164,    raw.whatsapp_contact.phone_e164),
       lid           = COALESCE(EXCLUDED.lid,           raw.whatsapp_contact.lid),
       push_name     = COALESCE(EXCLUDED.push_name,     raw.whatsapp_contact.push_name),
       notify_name   = COALESCE(EXCLUDED.notify_name,   raw.whatsapp_contact.notify_name),
       business_name = COALESCE(EXCLUDED.business_name, raw.whatsapp_contact.business_name),
       is_me         = raw.whatsapp_contact.is_me OR EXCLUDED.is_me,
       last_seen     = now(),
       payload       = raw.whatsapp_contact.payload || EXCLUDED.payload`,
    [
      c.jid,
      c.phoneE164 ?? null,
      c.lid ?? null,
      c.pushName ?? null,
      c.notifyName ?? null,
      c.businessName ?? null,
      c.isMe ?? false,
      JSON.stringify(c.payload ?? {}),
    ],
  );
}

/**
 * Record a LID <-> phone pairing so the normalizer can later upgrade a
 * 'lid:N' identity to its digit form. Without this the same human seen by
 * LID here and by number in the iPhone import stays two people forever.
 * @param {import('pg').Pool} pool
 * @param {string} lid
 * @param {string} phoneJid
 */
export async function recordLidMapping(pool, lid, phoneJid) {
  await pool.query(
    `INSERT INTO raw.whatsapp_lid_map (lid, phone_jid)
     VALUES ($1, $2)
     ON CONFLICT (lid) DO UPDATE SET
       phone_jid = EXCLUDED.phone_jid,
       last_seen = now()`,
    [lid, phoneJid],
  );
}

/**
 * @param {import('pg').Pool} pool
 * @param {{chatJid: string, chatKey: string, kind: string,
 *          title?: string|null, ownerJid?: string|null, payload?: object}} c
 */
export async function upsertChat(pool, c) {
  await pool.query(
    `INSERT INTO raw.whatsapp_chat (chat_jid, chat_key, kind, title, owner_jid, payload)
     VALUES ($1,$2,$3,$4,$5,$6::jsonb)
     ON CONFLICT (chat_jid) DO UPDATE SET
       chat_key  = EXCLUDED.chat_key,
       kind      = EXCLUDED.kind,
       title     = COALESCE(EXCLUDED.title, raw.whatsapp_chat.title),
       owner_jid = COALESCE(EXCLUDED.owner_jid, raw.whatsapp_chat.owner_jid),
       last_seen = now(),
       payload   = raw.whatsapp_chat.payload || EXCLUDED.payload`,
    [c.chatJid, c.chatKey, c.kind, c.title ?? null, c.ownerJid ?? null, JSON.stringify(c.payload ?? {})],
  );
}

/**
 * Auto-discover a group with enabled=FALSE, exactly like the Telegram
 * fetcher: the list fills itself in so the user can opt groups in later,
 * while nothing from them is ingested until they do.
 * @param {import('pg').Pool} pool
 * @param {{chatJid: string, title?: string|null, memberCount?: number|null,
 *          lastMessageAt?: Date|null}} g
 */
export async function upsertGroupAllowlist(pool, g) {
  await pool.query(
    `INSERT INTO raw.whatsapp_group_allowlist
       (chat_jid, title, kind, member_count, last_message_at)
     VALUES ($1, $2, 'group', $3, $4)
     ON CONFLICT (chat_jid) DO UPDATE SET
       title           = COALESCE(EXCLUDED.title, raw.whatsapp_group_allowlist.title),
       member_count    = COALESCE(EXCLUDED.member_count, raw.whatsapp_group_allowlist.member_count),
       last_seen_at    = now(),
       last_message_at = GREATEST(
         raw.whatsapp_group_allowlist.last_message_at,
         EXCLUDED.last_message_at
       )`,
    [g.chatJid, g.title ?? null, g.memberCount ?? null, g.lastMessageAt ?? null],
  );
}

/**
 * The opt-in gate on the message hot path.
 * @param {import('pg').Pool} pool
 * @param {string} chatJid
 */
export async function isGroupEnabled(pool, chatJid) {
  const { rows } = await pool.query(
    `SELECT enabled FROM raw.whatsapp_group_allowlist WHERE chat_jid = $1`,
    [chatJid],
  );
  return rows.length > 0 && rows[0].enabled === true;
}

/**
 * Returns true if a new row was written, false if it was a duplicate — the
 * same signal the Telegram fetcher's insert_message returns, which is what
 * makes the per-message log line honest about what happened.
 * @param {import('pg').Pool} pool
 * @param {{chatJid: string, chatKey: string, sourceMessageId: string,
 *          fromMe: boolean, senderJid: string|null, senderPhoneE164: string|null,
 *          messageDate: Date, kind: string, text: string|null,
 *          voiceFilePath: string|null, mediaFilePath: string|null,
 *          quotedMessageId: string|null, mentionedJids: string[],
 *          payload: object}} m
 */
export async function insertMessage(pool, m) {
  const { rows } = await pool.query(
    `INSERT INTO raw.whatsapp_message
       (chat_jid, chat_key, source_message_id, from_me, sender_jid,
        sender_phone_e164, message_date, origin, kind, text,
        voice_file_path, media_file_path, quoted_message_id,
        mentioned_jids, payload)
     VALUES ($1,$2,$3,$4,$5,$6,$7,'live',$8,$9,$10,$11,$12,$13,$14::jsonb)
     ON CONFLICT (source_message_id, from_me) DO NOTHING
     RETURNING id`,
    [
      m.chatJid,
      m.chatKey,
      m.sourceMessageId,
      m.fromMe,
      m.senderJid,
      m.senderPhoneE164,
      m.messageDate,
      m.kind,
      m.text,
      m.voiceFilePath,
      m.mediaFilePath,
      m.quotedMessageId,
      m.mentionedJids.length ? m.mentionedJids : null,
      JSON.stringify(m.payload ?? {}),
    ],
  );
  return rows.length > 0 ? rows[0].id : null;
}

/**
 * Attach a downloaded voice file to a row that already exists. Used when the
 * download finishes after the insert, so a slow media fetch never delays
 * recording that the message happened.
 * @param {import('pg').Pool} pool
 * @param {number} id
 * @param {string} path
 */
export async function setVoicePath(pool, id, path) {
  await pool.query(
    `UPDATE raw.whatsapp_message SET voice_file_path = $2 WHERE id = $1`,
    [id, path],
  );
}

/**
 * @param {import('pg').Pool} pool
 * @param {number} id
 * @param {string} path
 */
export async function setMediaPath(pool, id, path) {
  await pool.query(
    `UPDATE raw.whatsapp_message SET media_file_path = $2 WHERE id = $1`,
    [id, path],
  );
}
