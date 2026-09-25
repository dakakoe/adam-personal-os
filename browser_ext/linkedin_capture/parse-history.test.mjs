/** Pins parseHistory() against the Experience layouts LinkedIn actually ships.
 *
 *  Run: node --test browser_ext/linkedin_capture/*.test.mjs
 *  (name the files, not the directory — content.js is a content script and
 *  throws the moment it's loaded outside a page)
 *
 *  This function has been wrong three times, each time the same way: a layout
 *  nobody had seen shifted the lines by one and the title came out as the
 *  company, or the location. It reads positionally off rendered text, which is
 *  the only thing on that page LinkedIn can't rename — so the defence isn't a
 *  better selector, it's keeping every layout we've met in front of us.
 *
 *  The parser lives inside content.js's IIFE (a content script can't export),
 *  so it's sliced out and evaluated here rather than imported.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "content.js"), "utf8");
const start = src.indexOf("  const DATE_RE");
const end = src.indexOf("  function listEntries(");
assert.ok(start > 0 && end > start, "could not locate parseHistory in content.js");
const parseHistory = new Function(src.slice(start, end) + "\nreturn parseHistory;")();

test("grouped employer whose header carries the employment type", () => {
  // Two roles at one employer. The header is "Full-time · 2 mos" — the type is
  // stated once for the employer, not per role. Reading two lines back from
  // the date gave title "Dubai, United Arab Emirates", company "Founder".
  const got = parseHistory([
    "Bureau 49",
    "Full-time · 2 mos",
    "Dubai, United Arab Emirates",
    "Founder",
    "Jul 2026 - Present · 2 mos",
    "Business Ownership and Start-up Leadership",
    "Founder",
    "Jul 2023 - Present · 3 yrs 1 mo",
  ], 5);
  assert.equal(got.length, 2);
  for (const e of got) {
    assert.equal(e.title, "Founder");
    assert.equal(e.company, "Bureau 49");
    assert.equal(e.employment_type, "Full-time");
    // The employer's location is stated above the roles; what follows a date
    // in this layout is a skill tag, which must not be read as a place.
    assert.equal(e.location, "Dubai, United Arab Emirates");
  }
});

test("grouped employer whose header is a bare duration", () => {
  const [e] = parseHistory([
    "Sleepagotchi",
    "4 yrs 8 mos",
    "Founder",
    "Mar 2026 - Present · 5 mos",
  ], 5);
  assert.equal(e.title, "Founder");
  assert.equal(e.company, "Sleepagotchi");
});

test("simple entry: role, then company with its own employment type", () => {
  const [e] = parseHistory([
    "Marketing Lead",
    "Gnosis · Full-time",
    "Mar 2026 - Present · 5 mos",
    "Berlin, Germany",
  ], 5);
  assert.equal(e.title, "Marketing Lead");
  assert.equal(e.company, "Gnosis");
  assert.equal(e.employment_type, "Full-time");
});

test("a date range ending in a duration is not a group header", () => {
  // "Jul 2026 - Present · 2 mos" ends the same way a group header does. If the
  // duration test ran before the date test, this entry would be swallowed and
  // the NEXT one would inherit the wrong employer.
  const got = parseHistory([
    "Product Lead",
    "Acme · Full-time",
    "Jul 2026 - Present · 2 mos",
    "Engineer",
    "Aramco · Full-time",
    "Jan 2020 - Jun 2026 · 6 yrs 6 mos",
  ], 5);
  assert.equal(got.length, 2);
  assert.deepEqual(got.map((e) => [e.title, e.company]),
                   [["Product Lead", "Acme"], ["Engineer", "Aramco"]]);
});

test("a group ends when an entry brings its own company line", () => {
  const got = parseHistory([
    "Bureau 49",
    "Full-time · 2 mos",
    "Dubai, United Arab Emirates",
    "Founder",
    "Jul 2026 - Present · 2 mos",
    "Marketing Lead",
    "Gnosis · Full-time",
    "Mar 2020 - Jun 2026 · 6 yrs",
  ], 5);
  assert.deepEqual(got.map((e) => [e.title, e.company]),
                   [["Founder", "Bureau 49"], ["Marketing Lead", "Gnosis"]]);
});
