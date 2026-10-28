import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatRelativeDate(d: string | Date | null | undefined): string {
  if (!d) return "—";
  const date = typeof d === "string" ? new Date(d) : d;
  const now = Date.now();
  const diff = (now - date.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  if (diff < 31536000) return `${Math.floor(diff / 604800)}w ago`;
  return `${Math.floor(diff / 31536000)}y ago`;
}

/** Today's date in Asia/Bangkok as `YYYY-MM-DD`. The whole app uses the Bangkok
 *  day boundary (not the browser's local timezone) so client labels always agree
 *  with the server-side counts regardless of the device's clock. */
function bangkokToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(new Date());
}

/** Whole-day offset of a `YYYY-MM-DD` due date from today (Bangkok).
 *  Negative = overdue, 0 = due today, positive = days left. */
function daysUntilDue(due: string): number {
  // Compare both as UTC midnight → an exact integer day diff, timezone-independent.
  return Math.round((Date.parse(due + "T00:00:00Z") - Date.parse(bangkokToday() + "T00:00:00Z")) / 86400000);
}

/** Days from today until the end of the current Mon–Sun week (0 if today is
 *  Sunday). Used so "This week" means the calendar week, not a rolling 7 days. */
function daysLeftInWeek(): number {
  const dowMon0 = (new Date(Date.parse(bangkokToday() + "T00:00:00Z")).getUTCDay() + 6) % 7; // Mon=0..Sun=6
  return 6 - dowMon0;
}

/** Time-to-deadline badge for a task. Returns null when there's nothing to
 *  show (no due date, or the task is done/cancelled). Day-granularity. */
export function taskTimeStatus(
  due: string | null | undefined,
  status?: string,
): { label: string; tone: "danger" | "warn" | "muted" } | null {
  if (!due) return null;
  if (status === "done" || status === "cancelled") return null;
  const days = daysUntilDue(due);
  if (days < 0) return { label: `overdue ${Math.abs(days)}d`, tone: "danger" };
  if (days === 0) return { label: "due today", tone: "warn" };
  if (days <= 2) return { label: `${days}d left`, tone: "warn" };
  return { label: `${days}d left`, tone: "muted" };
}

export type DueBucket = "overdue" | "today" | "week" | "later" | "none";

/** Which due-date bucket a task falls into (Overdue/Today/This week/Later/No
 *  date) — for grouping the Tasks page. Bangkok day boundary. */
export function dueBucket(due: string | null | undefined): DueBucket {
  if (!due) return "none";
  const days = daysUntilDue(due);
  if (days < 0) return "overdue";
  if (days === 0) return "today";
  // "This week" = up to this week's Sunday; anything past it is next week → Later.
  if (days <= daysLeftInWeek()) return "week";
  return "later";
}

/** Absolute date+time of a message/event in the user's tz (Bangkok) — e.g.
 *  "15 Mar 2026, 21:30". For judging whether a surfaced item is stale. */
export function formatDiscussed(iso: string | null | undefined): string | null {
  if (!iso) return null;
  try {
    return new Intl.DateTimeFormat("en-GB", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false,
      timeZone: "Asia/Bangkok",
    }).format(new Date(iso));
  } catch {
    return null;
  }
}

// Free-text country name → flag emoji (best effort; "" when unrecognized).
const _ISO2: Record<string, string> = {
  "united states": "US", "usa": "US", "us": "US", "united states of america": "US", "america": "US",
  "united kingdom": "GB", "uk": "GB", "england": "GB", "britain": "GB", "great britain": "GB",
  "uae": "AE", "united arab emirates": "AE", "abu dhabi": "AE", "dubai": "AE",
  "japan": "JP", "korea": "KR", "south korea": "KR", "china": "CN", "hong kong": "HK", "taiwan": "TW",
  "singapore": "SG", "cayman islands": "KY", "cayman": "KY", "bvi": "VG", "british virgin islands": "VG",
  "russia": "RU", "germany": "DE", "france": "FR", "switzerland": "CH", "netherlands": "NL",
  "canada": "CA", "australia": "AU", "india": "IN", "thailand": "TH", "vietnam": "VN", "indonesia": "ID",
  "israel": "IL", "spain": "ES", "italy": "IT", "poland": "PL", "ukraine": "UA", "brazil": "BR",
  "mexico": "MX", "turkey": "TR", "estonia": "EE", "lithuania": "LT", "portugal": "PT", "ireland": "IE",
  "sweden": "SE", "finland": "FI", "norway": "NO", "denmark": "DK", "austria": "AT", "belgium": "BE",
  "czechia": "CZ", "czech republic": "CZ", "greece": "GR", "malta": "MT", "cyprus": "CY",
  "luxembourg": "LU", "seychelles": "SC", "panama": "PA", "philippines": "PH", "malaysia": "MY",
  "saudi arabia": "SA", "saudi": "SA", "qatar": "QA", "bahrain": "BH", "kazakhstan": "KZ",
  "georgia": "GE", "armenia": "AM", "argentina": "AR", "nigeria": "NG", "south africa": "ZA",
  "egypt": "EG", "new zealand": "NZ", "gibraltar": "GI", "liechtenstein": "LI", "bermuda": "BM",
};

/** Canonical, flag-renderable country names for the country autocomplete.
 *  Every entry's lowercase form is a key in _ISO2 above, so a value picked from
 *  this list always renders a flag (single source of truth for both). */
export const COUNTRIES: string[] = [
  "United States", "United Kingdom", "United Arab Emirates", "Japan", "South Korea",
  "China", "Hong Kong", "Taiwan", "Singapore", "Cayman Islands", "British Virgin Islands",
  "Russia", "Germany", "France", "Switzerland", "Netherlands", "Canada", "Australia",
  "India", "Thailand", "Vietnam", "Indonesia", "Israel", "Spain", "Italy", "Poland",
  "Ukraine", "Brazil", "Mexico", "Turkey", "Estonia", "Lithuania", "Portugal", "Ireland",
  "Sweden", "Finland", "Norway", "Denmark", "Austria", "Belgium", "Czech Republic",
  "Greece", "Malta", "Cyprus", "Luxembourg", "Seychelles", "Panama", "Philippines",
  "Malaysia", "Saudi Arabia", "Qatar", "Bahrain", "Kazakhstan", "Georgia", "Armenia",
  "Argentina", "Nigeria", "South Africa", "Egypt", "New Zealand", "Gibraltar",
  "Liechtenstein", "Bermuda",
];

export function countryFlag(name: string | null | undefined): string {
  if (!name) return "";
  const iso = _ISO2[name.trim().toLowerCase()];
  if (!iso || iso.length !== 2) return "";
  return String.fromCodePoint(...[...iso.toUpperCase()].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase() ?? "").join("") || "?";
}

/** Render a stored birthday ("YYYY-MM-DD"). Year 1900 is our sentinel for
 *  "year unknown / privacy-omitted" — display month+day only in that case. */
export function formatBirthday(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const [yStr, mStr, dStr] = iso.split("T")[0].split("-");
  const year = parseInt(yStr, 10);
  const month = parseInt(mStr, 10);
  const day = parseInt(dStr, 10);
  if (!month || !day) return null;
  const monthName = new Date(2000, month - 1, 1).toLocaleString("en", { month: "short" });
  return year && year !== 1900 ? `${monthName} ${day}, ${year}` : `${monthName} ${day}`;
}
