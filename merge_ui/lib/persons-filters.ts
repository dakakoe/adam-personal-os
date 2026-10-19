/**
 * One definition of a People-list URL.
 *
 * Four chips now steer this list — company, circle, source, sort — and each
 * one used to build its own URL. Two of them dropped everything else on the
 * way, so picking a circle silently cleared an active search, a source filter
 * and the sort; the newer two carried some of it. That drift is what this
 * file exists to end: a chip says which filter IT changes, and the rest is
 * carried for it.
 *
 * `null` clears a filter. `offset` is never carried: a changed filter means a
 * different list, and page 4 of the old one is meaningless in it — usually an
 * empty page, which reads as "no results" rather than "wrong page".
 */

export type PersonsFilters = {
  q?: string;
  company?: string;
  circle?: string;
  source?: string;
  sort?: string;
};

/** The order is the URL's, so the same view always produces the same link —
 *  which matters for anything bookmarked or pasted into a chat. */
const KEYS: (keyof PersonsFilters)[] = ["q", "company", "circle", "source", "sort"];

export function personsHref(
  current: PersonsFilters,
  change: Partial<Record<keyof PersonsFilters, string | null>> = {},
): string {
  const params = new URLSearchParams();
  for (const key of KEYS) {
    // `key in change` rather than a truthiness check: passing null is how a
    // chip clears its own filter, and that has to win over the current value.
    const value = key in change ? change[key] : current[key];
    if (value) params.set(key, value);
  }
  const qs = params.toString();
  return `/persons${qs ? `?${qs}` : ""}`;
}
