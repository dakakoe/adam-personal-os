/** Day-based grouping shared by the Follow-ups and Tasks lists.
 *
 *  Both surfaces answer the same question — "what's overdue, what's today,
 *  what's coming and on which day" — so the grouping, ordering and drop-target
 *  logic live here rather than being duplicated (and drifting) per page.
 */

/** How late something is, in days: positive = overdue, negative = still ahead.
 *  The sort key that lets planned follow-ups and cadence nudges share one list
 *  — without it there's no common scale to interleave them on.
 *
 *  An all-day item isn't late until its day has passed, matching dueLabel. */
export function daysLate(dueDate: string | null, dueTime: string | null): number {
  // Dateless is never late — it's an intention, not a commitment. Sorts below
  // everything dated, including things still in the future.
  if (!dueDate) return Number.NEGATIVE_INFINITY;
  const [y, m, d] = dueDate.split("-").map(Number);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const dueDay = new Date(y, m - 1, d);
  const wholeDays = Math.round((today.getTime() - dueDay.getTime()) / 86_400_000);
  if (!dueTime) return wholeDays;
  // Timed: late the moment it passes, so today counts as a fraction of a day
  // and a 09:00 slot outranks a 17:00 one on the same morning.
  const [hh, mm] = dueTime.slice(0, 5).split(":").map(Number);
  const due = new Date(y, m - 1, d, hh, mm);
  return (Date.now() - due.getTime()) / 86_400_000;
}

/** Which group a follow-up belongs in.
 *
 *  Overdue / Today / Tomorrow are named, because those are the ones you act
 *  on. Everything further out is grouped by its own DAY: a "This week" bucket
 *  meant "within 7 days", so on a Saturday it swallowed most of next week —
 *  Aug 24 filed under "this week" when it was next Monday. A date can't be
 *  wrong about which day it is.
 *
 *  Group keys are opaque strings so dated groups can be created on the fly;
 *  `groupsOf` returns them already in display order.
 */
export type GroupKey = string;

export type Group = { key: GroupKey; label: string };

const MONTH = { month: "short", day: "numeric" } as const;

function localMidnight(dueDate: string): Date {
  const [y, m, d] = dueDate.split("-").map(Number);
  return new Date(y, m - 1, d);
}

/** Day difference from today, ignoring time of day. */
function dayOffset(dueDate: string): number {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((localMidnight(dueDate).getTime() - today.getTime()) / 86_400_000);
}

/** The group a single follow-up lands in, with the label to show for it. */
export function groupOf(
  dueDate: string | null, dueTime: string | null, settled: boolean,
): Group {
  if (settled) return { key: "settled", label: "Settled" };
  if (!dueDate) return { key: "someday", label: "No date" };
  const off = dayOffset(dueDate);
  if (off < 0) return { key: "overdue", label: "Overdue" };
  const day = localMidnight(dueDate);
  if (off === 0) {
    // A timed slot that has already passed today is overdue; an all-day item
    // isn't, because you still have the rest of the day.
    if (dueTime && daysLate(dueDate, dueTime) > 0) return { key: "overdue", label: "Overdue" };
    return { key: "today", label: `Today · ${day.toLocaleDateString([], MONTH)}` };
  }
  if (off === 1) return { key: "tomorrow", label: `Tomorrow · ${day.toLocaleDateString([], MONTH)}` };
  return {
    key: `d:${dueDate}`,
    // Weekday included because "Mon 24 Aug" answers "is that before my
    // Tuesday call" without counting; the bare date didn't.
    label: day.toLocaleDateString([], { weekday: "short", ...MONTH }),
  };
}

/** Fixed groups come first in this order; dated ones sort between `tomorrow`
 *  and `someday` by their own date, which `d:YYYY-MM-DD` sorts correctly as a
 *  plain string. */
const RANK: Record<string, number> = {
  overdue: 0, today: 1, tomorrow: 2, someday: 4, settled: 5,
};
const rankOf = (key: GroupKey) => (key in RANK ? RANK[key] : 3);

/** Compare two group keys for display order. */
export function compareGroups(a: GroupKey, b: GroupKey): number {
  const ra = rankOf(a), rb = rankOf(b);
  return ra === rb ? a.localeCompare(b) : ra - rb;
}

/** Local today as YYYY-MM-DD, matching how the day groups are computed. */
export function todayISO(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Add n days to a YYYY-MM-DD string, returning YYYY-MM-DD. Constructs a local
 *  Date so month/year roll over correctly. */
export function addDaysISO(iso: string, n: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d + n);
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;
}

/** Where a card dropped onto a group should land. `date: null` with droppable
 *  means "clear the date" (the No-date group). Overdue has no sensible past
 *  date and Settled would need un-settling, so both reject the drop. */
export function groupDropTarget(key: GroupKey): { droppable: boolean; date: string | null } {
  if (key === "today") return { droppable: true, date: todayISO() };
  if (key === "tomorrow") return { droppable: true, date: addDaysISO(todayISO(), 1) };
  if (key === "someday") return { droppable: true, date: null };
  if (key.startsWith("d:")) return { droppable: true, date: key.slice(2) };
  return { droppable: false, date: null };
}

