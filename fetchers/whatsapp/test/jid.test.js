// @ts-check
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  normalizeJid,
  isGroup,
  isLid,
  isIgnorable,
  toE164,
  chatKeyFor,
  chatKind,
  sanitizeForPath,
} from "../src/jid.js";

test("normalizeJid strips the device suffix", () => {
  // The one that bites: the same human sending from a second linked device
  // arrives as '…:12@s.whatsapp.net' and would otherwise become a second
  // contact, a second person, and a split conversation.
  assert.equal(normalizeJid("3725551234:12@s.whatsapp.net"), "3725551234@s.whatsapp.net");
  assert.equal(normalizeJid("3725551234@s.whatsapp.net"), "3725551234@s.whatsapp.net");
  assert.equal(normalizeJid("987654321:3@lid"), "987654321@lid");
});

test("normalizeJid leaves group jids alone", () => {
  const g = "120363012345678901@g.us";
  assert.equal(normalizeJid(g), g);
});

test("normalizeJid rejects malformed input", () => {
  for (const bad of [null, undefined, "", "nosuffix", "@s.whatsapp.net", ":12@s.whatsapp.net"]) {
    assert.equal(normalizeJid(/** @type {any} */ (bad)), null, `expected null for ${bad}`);
  }
});

test("isGroup / isLid", () => {
  assert.ok(isGroup("120363012345678901@g.us"));
  assert.ok(!isGroup("3725551234@s.whatsapp.net"));
  assert.ok(isLid("987654321@lid"));
  assert.ok(!isLid("3725551234@s.whatsapp.net"));
});

test("isIgnorable covers status, broadcast lists and channels", () => {
  assert.ok(isIgnorable("status@broadcast"));
  assert.ok(isIgnorable("1234-5678@broadcast"));
  assert.ok(isIgnorable("abc123@newsletter"));
  assert.ok(isIgnorable(null));
  assert.ok(!isIgnorable("3725551234@s.whatsapp.net"));
  assert.ok(!isIgnorable("120363012345678901@g.us"));
});

test("toE164 returns bare digits only for phone jids", () => {
  assert.equal(toE164("3725551234@s.whatsapp.net"), "3725551234");
  assert.equal(toE164("3725551234:12@s.whatsapp.net"), "3725551234");
  // A LID is not a phone number, however numeric it looks.
  assert.equal(toE164("987654321@lid"), null);
  assert.equal(toE164("120363012345678901@g.us"), null);
  assert.equal(toE164("status@broadcast"), null);
});

test("chatKeyFor gives one stable handle per conversation", () => {
  assert.equal(chatKeyFor("3725551234@s.whatsapp.net"), "3725551234");
  assert.equal(chatKeyFor("3725551234:9@s.whatsapp.net"), "3725551234");
  assert.equal(chatKeyFor("120363012345678901@g.us"), "120363012345678901@g.us");
  assert.equal(chatKeyFor("987654321@lid"), "lid:987654321");
  assert.equal(chatKeyFor("status@broadcast"), null);
  assert.equal(chatKeyFor(null), null);
});

test("a lid chat key can never be mistaken for a phone number", () => {
  // The whole LID-upgrade story depends on these two namespaces not
  // colliding: a person seen only by LID must not look like they have a
  // phone identity already.
  const lid = chatKeyFor("3725551234@lid");
  assert.equal(lid, "lid:3725551234");
  assert.notEqual(lid, chatKeyFor("3725551234@s.whatsapp.net"));
});

test("chatKind", () => {
  assert.equal(chatKind("120363012345678901@g.us"), "group");
  assert.equal(chatKind("3725551234@s.whatsapp.net"), "private");
  assert.equal(chatKind("abc@newsletter"), "newsletter");
  assert.equal(chatKind("1-2@broadcast"), "broadcast");
});

test("sanitizeForPath keeps filenames safe and bounded", () => {
  assert.equal(sanitizeForPath("120363012345678901@g.us"), "120363012345678901_g.us");
  assert.equal(sanitizeForPath("../../etc/passwd"), ".._.._etc_passwd");
  assert.ok(!sanitizeForPath("a".repeat(500)).includes("/"));
  assert.ok(sanitizeForPath("a".repeat(500)).length <= 120);
});
