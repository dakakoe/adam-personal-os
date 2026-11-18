// @ts-check
import { test } from "node:test";
import assert from "node:assert/strict";

import { shouldAutoFollow, parseMaxMembers } from "../src/groups.js";

test("auto-follow takes small groups of a known size", () => {
  assert.equal(shouldAutoFollow(4, 9), true);
  assert.equal(shouldAutoFollow(9, 9), true); // the limit is inclusive
  assert.equal(shouldAutoFollow(10, 9), false); // "less than 10" = up to 9
});

test("auto-follow leaves unknown sizes and the off setting alone", () => {
  assert.equal(shouldAutoFollow(null, 9), false); // metadata fetch failed
  assert.equal(shouldAutoFollow(undefined, 9), false);
  assert.equal(shouldAutoFollow(0, 9), false); // size 0 means we never learned it
  assert.equal(shouldAutoFollow(4, 0), false); // setting off
});

test("the stored setting only ever parses to a whole number", () => {
  assert.equal(parseMaxMembers(9), 9);
  assert.equal(parseMaxMembers("9"), 9);
  assert.equal(parseMaxMembers(null), 0);
  assert.equal(parseMaxMembers(undefined), 0);
  assert.equal(parseMaxMembers(-3), 0);
  assert.equal(parseMaxMembers(2.5), 0);
  assert.equal(parseMaxMembers("lots"), 0);
});
