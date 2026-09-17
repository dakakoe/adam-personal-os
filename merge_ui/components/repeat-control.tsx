"use client";

import { Repeat, Clock } from "lucide-react";
import { cn } from "@/lib/utils";

// 0 = Monday … 6 = Sunday (matches the server's byweekday encoding).
export type RepeatKind = "none" | "daily" | "weekdays" | "weekly" | "monthly" | "yearly";
export type RepeatValue = { kind: RepeatKind; byweekday: number[]; time: string | null; duration: number | null };
export const REPEAT_NONE: RepeatValue = { kind: "none", byweekday: [], time: null, duration: null };

// Shared duration choices (minutes) for timed tasks/routines on the calendar.
export const DURATION_OPTIONS = [15, 30, 45, 60, 90, 120, 180];
export function durationLabel(m: number): string {
  if (m < 60) return `${m} min`;
  return m % 60 === 0 ? `${m / 60} h` : `${(m / 60).toFixed(1)} h`;
}

const CHIP_LABEL = ["M", "T", "W", "T", "F", "S", "S"];
const DOW3 = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const PRESETS: { value: RepeatKind; label: string }[] = [
  { value: "none", label: "Doesn't repeat" },
  { value: "daily", label: "Every day" },
  { value: "weekdays", label: "Every weekday (Mon–Fri)" },
  { value: "weekly", label: "Weekly on…" },
  { value: "monthly", label: "Every month" },
  { value: "yearly", label: "Every year" },
];

function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

/** Plain-English summary of a repeat value (helper text under the control). */
export function humanizeRepeat(v: RepeatValue): string {
  const at = v.time ? ` at ${v.time}` : "";
  switch (v.kind) {
    case "none": return "Doesn't repeat";
    case "daily": return `Every day${at}`;
    case "weekdays": return `Every weekday${at}`;
    case "weekly": {
      const d = [...v.byweekday].sort((a, b) => a - b).map((i) => DOW3[i]);
      return d.length ? `Every ${d.join(", ")}${at}` : `Pick at least one day`;
    }
    case "monthly": return `Monthly on the ${ordinal(new Date().getDate())}${at}`;
    case "yearly": return `Every year on ${new Date().toLocaleString("en", { month: "short", day: "numeric" })}${at}`;
  }
}

/** Map a RepeatValue to the routine API shape. Returns null for "none". */
export function repeatToApi(v: RepeatValue): { freq: string; byweekday: number[]; at_time: string | null; duration_min: number | null } | null {
  if (v.kind === "none") return null;
  const dur = v.time ? v.duration : null;
  if (v.kind === "weekdays") return { freq: "weekly", byweekday: [0, 1, 2, 3, 4], at_time: v.time, duration_min: dur };
  if (v.kind === "weekly") return { freq: "weekly", byweekday: v.byweekday, at_time: v.time, duration_min: dur };
  return { freq: v.kind, byweekday: [], at_time: v.time, duration_min: dur }; // daily | monthly | yearly
}

/** Inverse of repeatToApi: build a RepeatValue from a stored routine's fields (for editing). */
export function apiToRepeat(r: { freq: string; byweekday: number[]; at_time: string | null; duration_min: number | null }): RepeatValue {
  const time = r.at_time ? r.at_time.slice(0, 5) : null;
  const duration = r.duration_min ?? null;
  if (r.freq === "weekly") {
    const wd = [...r.byweekday].sort((a, b) => a - b);
    const isWeekdays = wd.length === 5 && wd.every((d, i) => d === i);
    return { kind: isWeekdays ? "weekdays" : "weekly", byweekday: r.byweekday, time, duration };
  }
  if (r.freq === "daily" || r.freq === "monthly" || r.freq === "yearly") {
    return { kind: r.freq, byweekday: [], time, duration };
  }
  return REPEAT_NONE;
}

/** Weekdays (0=Mon..6=Sun) a routine fires on; empty = no weekly footprint (monthly/yearly). */
export function routineWeekdays(freq: string, byweekday: number[]): number[] {
  if (freq === "daily") return [0, 1, 2, 3, 4, 5, 6];
  if (freq === "weekly") return byweekday;
  return [];
}

type RoutineLike = { id?: string; title: string; freq: string; byweekday: number[]; at_time: string | null; active: boolean };

/** First active routine that collides with `cand` (same HH:MM on an overlapping weekday); null if none.
 *  Only timed routines clash — an all-day candidate (at_time null) never warns. `selfId` excludes the row being edited. */
export function findRoutineConflict(
  cand: { freq: string; byweekday: number[]; at_time: string | null },
  rows: RoutineLike[], selfId?: string,
): RoutineLike | null {
  if (!cand.at_time) return null;
  const t = cand.at_time.slice(0, 5);
  const candDays = new Set(routineWeekdays(cand.freq, cand.byweekday));
  for (const r of rows) {
    if (!r.active || !r.at_time || r.id === selfId) continue;
    if (r.at_time.slice(0, 5) !== t) continue;
    if (routineWeekdays(r.freq, r.byweekday).some((d) => candDays.has(d))) return r;
  }
  return null;
}

export function RepeatControl({
  value, onChange,
}: {
  value: RepeatValue;
  onChange: (v: RepeatValue) => void;
}) {
  const set = (patch: Partial<RepeatValue>) => onChange({ ...value, ...patch });
  const toggleDay = (d: number) =>
    set({ byweekday: value.byweekday.includes(d) ? value.byweekday.filter((x) => x !== d) : [...value.byweekday, d] });

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <Repeat className="h-3.5 w-3.5" />
        <span className="text-sm">Repeat</span>
      </div>
      <select
        value={value.kind}
        onChange={(e) => set({ kind: e.target.value as RepeatKind })}
        className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background"
      >
        {PRESETS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
      </select>

      {/* Day chips — only for the custom weekly choice (progressive disclosure). */}
      {value.kind === "weekly" && (
        <div className="flex gap-1">
          {CHIP_LABEL.map((lbl, d) => {
            const on = value.byweekday.includes(d);
            return (
              <button
                key={d} type="button" onClick={() => toggleDay(d)}
                title={DOW3[d]}
                className={cn(
                  "h-8 w-8 rounded-full text-xs font-medium border transition-colors",
                  on ? "bg-primary text-primary-foreground border-primary"
                     : "border-border text-muted-foreground hover:bg-accent",
                )}
              >{lbl}</button>
            );
          })}
        </div>
      )}

      {/* Time vs all-day reminder */}
      {value.kind !== "none" && (
        <div className="flex items-center gap-2">
          {value.time === null ? (
            <button type="button" onClick={() => set({ time: "09:00" })}
              className="inline-flex items-center gap-1 h-7 px-2 text-xs rounded-md border border-border text-muted-foreground hover:bg-accent">
              <Clock className="h-3.5 w-3.5" /> Set a time
            </button>
          ) : (
            <>
              <input type="time" value={value.time}
                onChange={(e) => set({ time: e.target.value })}
                className="h-8 px-2 text-sm rounded-md border border-border bg-background [color-scheme:dark]" />
              <span className="text-xs text-muted-foreground">for</span>
              <select value={value.duration ?? 60} onChange={(e) => set({ duration: Number(e.target.value) })}
                title="How long it lasts (on the calendar)"
                className="h-8 px-2 text-sm rounded-md border border-border bg-background">
                {DURATION_OPTIONS.map((m) => <option key={m} value={m}>{durationLabel(m)}</option>)}
              </select>
              <button type="button" onClick={() => set({ time: null })}
                className="h-7 px-2 text-xs rounded-md border border-border text-muted-foreground hover:bg-accent">
                All-day
              </button>
            </>
          )}
        </div>
      )}

      {value.kind !== "none" && (
        <p className="text-xs text-muted-foreground">{humanizeRepeat(value)}</p>
      )}
    </div>
  );
}
