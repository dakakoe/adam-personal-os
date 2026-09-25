// @ts-check
import { test } from "node:test";
import assert from "node:assert/strict";

import { normalizePhone } from "../src/pair.js";

test("normalizePhone strips everything WhatsApp will not accept", () => {
  assert.equal(normalizePhone("+372 555 1234"), "3725551234");
  assert.equal(normalizePhone("+372-555-1234"), "3725551234");
  assert.equal(normalizePhone("(372) 555 1234"), "3725551234");
  assert.equal(normalizePhone("3725551234"), "3725551234");
});

test("normalizePhone rejects what cannot be a number", () => {
  // Rejecting here is the difference between a clear 'bad_phone' marker and
  // an opaque failure from requestPairingCode.
  for (const bad of ["", "   ", "abc", "12345", "+1", null, undefined]) {
    assert.equal(normalizePhone(/** @type {any} */ (bad)), null, `expected null for ${bad}`);
  }
  assert.equal(normalizePhone("1".repeat(25)), null);
});
