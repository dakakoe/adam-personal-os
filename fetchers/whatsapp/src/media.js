// @ts-check
/**
 * Downloading voice notes and, optionally, other media.
 *
 * Only voice matters for the corpus: the Whisper worker turns a .ogg into a
 * message body, which is the difference between a voice note being a searchable
 * memory and being an empty row. Images and video are off by default.
 */

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { downloadMediaMessage } from "baileys";

import { sanitizeForPath } from "./jid.js";

/**
 * Where a message's media lands. The resulting absolute path is STORED in
 * raw.whatsapp_message.voice_file_path and that column is the contract —
 * unlike the Telegram convention, nothing downstream may rebuild this from
 * parts, because chat keys contain '@', '.' and '-'.
 * @param {string} dir
 * @param {string} chatKey
 * @param {string} messageId
 * @param {string} ext
 */
export function mediaPathFor(dir, chatKey, messageId, ext) {
  return path.join(dir, `${sanitizeForPath(chatKey)}_${sanitizeForPath(messageId)}${ext}`);
}

/** WhatsApp voice notes are Opus in an OGG container, same as Telegram's. */
const EXT_BY_KIND = {
  voice: ".ogg",
  audio: ".ogg",
  image: ".jpg",
  video: ".mp4",
  gif: ".mp4",
  document: ".bin",
  sticker: ".webp",
};

/**
 * Fetch the media bytes and write them out. Returns the path, or null if this
 * kind carries no media or the download failed — a failed download must never
 * cost us the message row, so the caller records the message either way.
 *
 * @param {object} args
 * @param {any} args.msg
 * @param {string} args.kind
 * @param {string} args.chatKey
 * @param {string} args.messageId
 * @param {string} args.dir
 * @param {any} args.sock
 * @param {import('pino').Logger} args.log
 * @returns {Promise<string | null>}
 */
export async function download({ msg, kind, chatKey, messageId, dir, sock, log }) {
  const ext = EXT_BY_KIND[/** @type {keyof typeof EXT_BY_KIND} */ (kind)];
  if (!ext) return null;
  try {
    await mkdir(dir, { recursive: true, mode: 0o700 });
    const buf = await downloadMediaMessage(
      msg,
      "buffer",
      {},
      {
        logger: log,
        // Media on WhatsApp's servers expires. reuploadRequest asks the
        // sender's device to re-upload it, which is the difference between
        // getting an old voice note and getting nothing.
        reuploadRequest: sock.updateMediaMessage,
      },
    );
    if (!buf || !(/** @type {Buffer} */ (buf).length)) return null;
    const out = mediaPathFor(dir, chatKey, messageId, ext);
    await writeFile(out, /** @type {Buffer} */ (buf), { mode: 0o600 });
    return out;
  } catch (err) {
    log.warn({ err, messageId, kind }, "media download failed (message still recorded)");
    return null;
  }
}
