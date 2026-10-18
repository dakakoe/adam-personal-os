// @ts-check
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  unwrap,
  kindOf,
  textOf,
  mentionsOf,
  quotedIdOf,
  timestampToDate,
  isSkippableKind,
} from "../src/message.js";

test("unwrap peels ephemeral and view-once wrappers", () => {
  const inner = { conversation: "hi" };
  assert.deepEqual(unwrap({ ephemeralMessage: { message: inner } }), inner);
  assert.deepEqual(unwrap({ viewOnceMessageV2: { message: inner } }), inner);
  // Disappearing view-once: two layers deep.
  assert.deepEqual(
    unwrap({ ephemeralMessage: { message: { viewOnceMessage: { message: inner } } } }),
    inner,
  );
});

test("unwrap terminates on a self-referential message", () => {
  /** @type {any} */ const evil = {};
  evil.ephemeralMessage = { message: evil };
  assert.doesNotThrow(() => unwrap(evil));
});

test("kindOf distinguishes a voice note from shared audio", () => {
  // Only voice notes get transcribed, so this distinction decides whether a
  // message ever gets a body.
  assert.equal(kindOf({ audioMessage: { ptt: true } }), "voice");
  assert.equal(kindOf({ audioMessage: { ptt: false } }), "audio");
  assert.equal(kindOf({ audioMessage: {} }), "audio");
});

test("kindOf covers the rest of the vocabulary", () => {
  assert.equal(kindOf({ conversation: "hi" }), "text");
  assert.equal(kindOf({ extendedTextMessage: { text: "hi" } }), "text");
  assert.equal(kindOf({ imageMessage: {} }), "image");
  assert.equal(kindOf({ videoMessage: {} }), "video");
  assert.equal(kindOf({ videoMessage: { gifPlayback: true } }), "gif");
  assert.equal(kindOf({ stickerMessage: {} }), "sticker");
  assert.equal(kindOf({ documentMessage: {} }), "document");
  assert.equal(kindOf({ locationMessage: {} }), "location");
  assert.equal(kindOf({ contactMessage: {} }), "contact_card");
  assert.equal(kindOf({ reactionMessage: {} }), "reaction");
  assert.equal(kindOf({ protocolMessage: {} }), "protocol");
  assert.equal(kindOf({ somethingNewMeta2027Message: {} }), "other");
  assert.equal(kindOf(null), "other");
});

test("kindOf sees through a wrapper", () => {
  assert.equal(kindOf({ viewOnceMessageV2: { message: { imageMessage: {} } } }), "image");
});

test("textOf reads plain, extended and captioned messages", () => {
  assert.equal(textOf({ conversation: "hello" }), "hello");
  assert.equal(textOf({ extendedTextMessage: { text: "hello" } }), "hello");
  // A captioned image is the message; dropping the caption loses the content.
  assert.equal(textOf({ imageMessage: { caption: "let's do Tuesday" } }), "let's do Tuesday");
  assert.equal(textOf({ videoMessage: { caption: "watch this" } }), "watch this");
});

test("textOf normalizes empty to null so the embedder skips it", () => {
  assert.equal(textOf({ conversation: "   " }), null);
  assert.equal(textOf({ conversation: "" }), null);
  assert.equal(textOf({ imageMessage: {} }), null);
  assert.equal(textOf(null), null);
});

test("mentionsOf normalizes and dedupes", () => {
  const content = {
    extendedTextMessage: {
      text: "@a @b",
      contextInfo: {
        mentionedJid: [
          "3725551234@s.whatsapp.net",
          "3725551234:12@s.whatsapp.net", // same human, other device
          "3729999999@s.whatsapp.net",
        ],
      },
    },
  };
  assert.deepEqual(mentionsOf(content), [
    "3725551234@s.whatsapp.net",
    "3729999999@s.whatsapp.net",
  ]);
});

test("mentionsOf is empty when there are none", () => {
  assert.deepEqual(mentionsOf({ conversation: "no mentions" }), []);
  assert.deepEqual(mentionsOf(null), []);
});

test("quotedIdOf finds the replied-to message", () => {
  assert.equal(
    quotedIdOf({ extendedTextMessage: { text: "yes", contextInfo: { stanzaId: "ABC123" } } }),
    "ABC123",
  );
  assert.equal(quotedIdOf({ conversation: "hi" }), null);
});

test("timestampToDate accepts the three shapes WhatsApp sends", () => {
  const expected = new Date("2026-09-01T12:00:00.000Z").getTime();
  const secs = expected / 1000;
  assert.equal(timestampToDate(secs)?.getTime(), expected);
  assert.equal(timestampToDate(String(secs))?.getTime(), expected);
  assert.equal(timestampToDate({ toNumber: () => secs })?.getTime(), expected);
});

test("timestampToDate rejects values that would poison the timeline", () => {
  // Follow-up due dates and circle cadences are computed off occurred_at, so
  // a garbage timestamp is worse than a dropped message.
  assert.equal(timestampToDate(0), null);
  assert.equal(timestampToDate(-1), null);
  assert.equal(timestampToDate(null), null);
  assert.equal(timestampToDate("not a number"), null);
  assert.equal(timestampToDate(1), null); // 1970
  assert.equal(timestampToDate(99999999999), null); // year 5138
});

test("isSkippableKind drops protocol chatter but keeps real content", () => {
  assert.ok(isSkippableKind("reaction"));
  assert.ok(isSkippableKind("protocol"));
  for (const k of ["text", "voice", "image", "document", "other"]) {
    assert.ok(!isSkippableKind(k), `${k} must be kept`);
  }
});
