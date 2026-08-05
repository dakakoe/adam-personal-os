// Pure geometry + time helpers for the Google-Calendar-style schedule grid.
// No per-event color is stored (Google's colorId isn't synced), so we tint by
// calendar/account. All positioning is done in absolute ms against a day's
// zoned-midnight, which keeps it correct regardless of the viewer's browser tz.

import type { CalendarEvent } from "@/lib/api";

export const HOUR_PX = 48;          // vertical pixels per hour
export const DAY_MIN = 24 * 60;
export const MIN_EVENT_MIN = 24;    // never render a block shorter than this

/** Offset (localTime − UTC) in ms for `tz` at instant `ms`. */
function tzOffsetMs(ms: number, tz: string): number {
  const d = new Date(ms);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz, hour12: false,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(d);
  const m: Record<string, number> = {};
  for (const p of parts) if (p.type !== "literal") m[p.type] = Number(p.value);
  const asUTC = Date.UTC(m.year, m.month - 1, m.day, m.hour === 24 ? 0 : m.hour, m.minute, m.second);
  return asUTC - ms;
}

/** 'YYYY-MM-DD' for `ms` as seen in `tz`. */
export function dayKeyInTz(ms: number, tz: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date(ms));
}

/** Absolute ms of local midnight for the given 'YYYY-MM-DD' in `tz`. */
export function zonedMidnightMs(dateKey: string, tz: string): number {
  const utcGuess = new Date(dateKey + "T00:00:00Z").getTime();
  return utcGuess - tzOffsetMs(utcGuess, tz);
}

/** Minutes since local midnight for `ms` in `tz` (0..1440). */
export function minutesInTz(ms: number, tz: string): number {
  const p = new Intl.DateTimeFormat("en-GB", {
    timeZone: tz, hour12: false, hour: "2-digit", minute: "2-digit",
  }).formatToParts(new Date(ms));
  const m: Record<string, number> = {};
  for (const x of p) if (x.type !== "literal") m[x.type] = Number(x.value);
  return (m.hour === 24 ? 0 : m.hour) * 60 + m.minute;
}

export type PlacedEvent = {
  ev: CalendarEvent;
  top: number;      // minutes from midnight
  height: number;   // minutes
  col: number;      // column index within its overlap cluster
  cols: number;     // total columns in that cluster
  startMs: number;
  endMs: number;
};

/** Greedy interval-graph column packing: overlapping events share a row,
 *  split into the fewest equal columns that keep them side by side. */
function packColumns(items: PlacedEvent[]): PlacedEvent[] {
  items.sort((a, b) => a.top - b.top || a.height - b.height);
  let cluster: PlacedEvent[] = [];
  let clusterEnd = -1;
  const out: PlacedEvent[] = [];
  const flush = () => {
    const colEnds: number[] = [];
    for (const it of cluster) {
      let placed = false;
      for (let c = 0; c < colEnds.length; c++) {
        if (it.top >= colEnds[c]) { it.col = c; colEnds[c] = it.top + it.height; placed = true; break; }
      }
      if (!placed) { it.col = colEnds.length; colEnds.push(it.top + it.height); }
    }
    for (const it of cluster) { it.cols = colEnds.length; out.push(it); }
    cluster = [];
    clusterEnd = -1;
  };
  for (const it of items) {
    if (cluster.length && it.top >= clusterEnd) flush();
    cluster.push(it);
    clusterEnd = Math.max(clusterEnd, it.top + it.height);
  }
  if (cluster.length) flush();
  return out;
}

/** Split a day's events into an all-day list + positioned timed blocks. */
export function layoutDay(events: CalendarEvent[], dayMidnightMs: number, tz: string): {
  allDay: CalendarEvent[];
  timed: PlacedEvent[];
} {
  const dayEndMs = dayMidnightMs + DAY_MIN * 60_000;
  const allDay: CalendarEvent[] = [];
  const timed: PlacedEvent[] = [];
  for (const ev of events) {
    if (!ev.start_ts) continue;
    if (ev.all_day) { allDay.push(ev); continue; }
    const startMs = new Date(ev.start_ts).getTime();
    const endMs = ev.end_ts ? new Date(ev.end_ts).getTime() : startMs + 30 * 60_000;
    if (endMs <= dayMidnightMs || startMs >= dayEndMs) continue;   // not on this day
    const top = Math.max(0, (startMs - dayMidnightMs) / 60_000);
    const bottom = Math.min(DAY_MIN, (endMs - dayMidnightMs) / 60_000);
    timed.push({ ev, top, height: Math.max(MIN_EVENT_MIN, bottom - top), col: 0, cols: 1, startMs, endMs });
  }
  return { allDay, timed: packColumns(timed) };
}

// --- color: hash the account/calendar to a fixed hue, tuned for a dark grid ---
const HUES = [210, 145, 275, 25, 190, 330, 95, 255, 45, 165];

function hashHue(key: string): number {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  return HUES[Math.abs(h) % HUES.length];
}

export type EventColor = { bg: string; border: string; text: string };

export function eventColor(ev: CalendarEvent): EventColor {
  const key = ev.account_email || ev.summary || "?";
  const h = hashHue(key);
  const tentative = ev.self_response === "tentative";
  return {
    bg: `hsl(${h} 45% 20% / ${tentative ? 0.5 : 0.92})`,
    border: `hsl(${h} 60% 55%)`,
    text: `hsl(${h} 70% 85%)`,
  };
}
