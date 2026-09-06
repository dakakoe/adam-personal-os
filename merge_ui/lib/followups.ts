import { channelDisplayName } from "@/lib/channels";

/** Formatting shared by the Follow-ups page and the contact-page panel, so the
 *  two read identically — a follow-up shouldn't describe itself one way in the
 *  pipeline and another way on the person. */

/** Tomorrow, as a date input value. The default when adding one: most
 *  reconnects are soon-ish, and the time is deliberately left empty so a
 *  follow-up is an all-day intent unless you say otherwise. */
export function defaultDueDate(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** "12 Aug 15:00", "tomorrow", "today 09:30 · 2d overdue".
 *
 *  An all-day follow-up isn't overdue *during* its day — only once the day is
 *  gone. A timed one is overdue the moment it passes. Treating them the same
 *  would nag you all morning about something you still have all day to do. */
export function dueLabel(dueDate: string | null, dueTime: string | null): { text: string; overdue: boolean } {
  if (!dueDate) return { text: "someday", overdue: false };
  const [y, m, d] = dueDate.split("-").map(Number);
  const hhmm = dueTime ? dueTime.slice(0, 5) : null;
  const [hh, mm] = hhmm ? hhmm.split(":").map(Number) : [0, 0];
  const due = new Date(y, m - 1, d, hh, mm);

  const today = new Date(); today.setHours(0, 0, 0, 0);
  const dueDay = new Date(y, m - 1, d);
  const days = Math.round((dueDay.getTime() - today.getTime()) / 86_400_000);

  const overdue = dueTime ? due.getTime() < Date.now() : days < 0;
  const when =
    days === 0 ? "today" :
    days === 1 ? "tomorrow" :
    days === -1 ? "yesterday" :
    dueDay.toLocaleDateString([], { day: "numeric", month: "short" });

  const base = hhmm ? `${when} ${hhmm}` : when;
  if (!overdue) return { text: base, overdue: false };
  const late = days < 0 ? ` · ${-days}d overdue` : " · overdue";
  return { text: base + late, overdue: true };
}

/** 'telegram_text' → 'Telegram'. A manual tick says so plainly, because the
 *  distinction matters: one we observed, the other you asserted. */
export function channelLabel(via: string | null, source: string | null): string {
  if (source === "manual") return "marked by you";
  if (!via) return "seen";
  return channelDisplayName(via);
}

// Day grouping is shared with the Tasks list; see lib/day-groups.
export {
  daysLate, groupOf, compareGroups, todayISO, addDaysISO, groupDropTarget,
} from "./day-groups";
export type { Group, GroupKey } from "./day-groups";
