"use client";

import { useEffect, useRef } from "react";
import { MapPin, Users } from "lucide-react";
import type { CalendarEvent } from "@/lib/api";
import {
  HOUR_PX, DAY_MIN, layoutDay, minutesInTz, eventColor, dayKeyInTz, zonedMidnightMs,
} from "@/lib/day-layout";
import { cn } from "@/lib/utils";

export type GridDay = { key: string; midnightMs: number; weekday: string; dayNum: string; isToday: boolean };

function hourLabel(h: number): string {
  if (h === 0 || h === 24) return "";
  const ampm = h < 12 ? "AM" : "PM";
  const hr = h % 12 === 0 ? 12 : h % 12;
  return `${hr} ${ampm}`;
}

function fmtRange(ev: CalendarEvent, tz: string): string {
  const f = (iso: string | null) => iso
    ? new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso))
    : "";
  return ev.end_ts ? `${f(ev.start_ts)}–${f(ev.end_ts)}` : f(ev.start_ts);
}

export function ScheduleGrid({
  days, events, tz, nowMs, maxHeight = "calc(100vh - 12rem)",
}: {
  days: GridDay[];
  events: CalendarEvent[];
  tz: string;
  nowMs: number;
  maxHeight?: string;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const gridCols = `3.5rem repeat(${days.length}, minmax(0, 1fr))`;
  const bodyH = (DAY_MIN / 60) * HOUR_PX;

  // Scroll to ~1h before now (or 7 AM) on first mount so the day opens usefully.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const anchorMin = days.some((d) => d.isToday) ? minutesInTz(nowMs, tz) - 60 : 7 * 60;
    el.scrollTop = Math.max(0, (Math.max(0, anchorMin) / 60) * HOUR_PX);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days.map((d) => d.key).join(",")]);

  const perDay = days.map((d) => layoutDay(events, d.midnightMs, tz));
  const anyAllDay = perDay.some((p) => p.allDay.length > 0);

  return (
    <div className="rounded-lg border border-border bg-card/30 overflow-hidden flex flex-col min-h-0">
      {/* Header: weekday + date per column */}
      {days.length > 1 && (
        <div className="grid border-b border-border" style={{ gridTemplateColumns: gridCols }}>
          <div className="border-r border-border" />
          {days.map((d) => (
            <div key={d.key} className="px-2 py-1.5 text-center border-r border-border last:border-r-0">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{d.weekday}</div>
              <div className={cn("text-lg leading-tight font-semibold inline-flex items-center justify-center h-8 w-8 rounded-full mx-auto",
                d.isToday ? "bg-primary text-primary-foreground" : "text-foreground")}>{d.dayNum}</div>
            </div>
          ))}
        </div>
      )}

      {/* All-day row */}
      {anyAllDay && (
        <div className="grid border-b border-border bg-background/40" style={{ gridTemplateColumns: gridCols }}>
          <div className="px-1 py-1 text-[10px] text-muted-foreground text-right border-r border-border">all-day</div>
          {days.map((d, i) => (
            <div key={d.key} className="p-1 border-r border-border last:border-r-0 space-y-0.5 min-h-[1.75rem]">
              {perDay[i].allDay.map((ev, j) => {
                const c = eventColor(ev);
                return (
                  <div key={j} title={ev.summary || ""}
                    className="truncate text-[11px] rounded px-1.5 py-0.5"
                    style={{ background: c.bg, color: c.text, borderLeft: `2px solid ${c.border}` }}>
                    {ev.summary || "(no title)"}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}

      {/* Scrollable timed grid */}
      <div ref={scrollRef} className="overflow-y-auto min-h-0" style={{ maxHeight }}>
        <div className="grid relative" style={{ gridTemplateColumns: gridCols, height: bodyH }}>
          {/* Hour axis */}
          <div className="relative border-r border-border">
            {Array.from({ length: 24 }, (_, h) => (
              <div key={h} className="absolute right-1 -translate-y-1/2 text-[10px] text-muted-foreground tabular"
                style={{ top: (h / 24) * bodyH }}>{hourLabel(h)}</div>
            ))}
          </div>

          {/* Day columns */}
          {days.map((d, i) => {
            const nowMin = d.isToday && nowMs > 0 ? minutesInTz(nowMs, tz) : -1;
            return (
              <div key={d.key} className="relative border-r border-border last:border-r-0"
                style={{
                  backgroundImage: `repeating-linear-gradient(to bottom, transparent 0, transparent ${HOUR_PX - 1}px, hsl(0 0% 100% / 0.06) ${HOUR_PX - 1}px, hsl(0 0% 100% / 0.06) ${HOUR_PX}px)`,
                }}>
                {perDay[i].timed.map((p, j) => {
                  const c = eventColor(p.ev);
                  const gap = p.cols > 1 ? 2 : 0;
                  const tall = p.height >= 40;
                  return (
                    <div key={j}
                      title={`${p.ev.summary || "(no title)"}\n${fmtRange(p.ev, tz)}${p.ev.location ? "\n" + p.ev.location : ""}`}
                      className="absolute rounded-md px-1.5 py-0.5 overflow-hidden text-[11px] leading-tight"
                      style={{
                        top: (p.top / DAY_MIN) * bodyH,
                        height: (p.height / DAY_MIN) * bodyH - 1,
                        left: `calc(${(p.col / p.cols) * 100}% + ${gap}px)`,
                        width: `calc(${100 / p.cols}% - ${gap * 2}px)`,
                        background: c.bg, color: c.text, borderLeft: `2px solid ${c.border}`,
                      }}>
                      <div className="font-medium truncate">{p.ev.summary || "(no title)"}</div>
                      {tall && (
                        <div className="opacity-75 truncate flex items-center gap-1">
                          {fmtRange(p.ev, tz)}
                          {p.ev.attendee_count ? <><Users className="h-2.5 w-2.5" />{p.ev.attendee_count}</> : null}
                          {p.ev.location ? <><MapPin className="h-2.5 w-2.5" />{p.ev.location.slice(0, 20)}</> : null}
                        </div>
                      )}
                    </div>
                  );
                })}

                {/* Now-line */}
                {nowMin >= 0 && nowMin <= DAY_MIN && (
                  <div className="absolute left-0 right-0 z-10 pointer-events-none" style={{ top: (nowMin / DAY_MIN) * bodyH }}>
                    <div className="h-px bg-rose-500" />
                    <div className="absolute -left-1 -top-1 h-2 w-2 rounded-full bg-rose-500" />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** Build the GridDay descriptors for a Day (1) or Week (7) starting at `anchorMs`. */
export function buildDays(anchorMs: number, count: number, weekStartMs: number | null, tz: string, todayKey: string): GridDay[] {
  const base = weekStartMs ?? anchorMs;
  return Array.from({ length: count }, (_, i) => {
    // Derive each day's real zoned-midnight from its date key (handles any tz);
    // the +i*day step only needs to land on the right calendar date.
    const key = dayKeyInTz(base + i * 86_400_000, tz);
    const ms = zonedMidnightMs(key, tz);
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short", day: "numeric" }).formatToParts(new Date(ms));
    const weekday = parts.find((p) => p.type === "weekday")?.value ?? "";
    const dayNum = parts.find((p) => p.type === "day")?.value ?? "";
    return { key, midnightMs: ms, weekday, dayNum, isToday: key === todayKey };
  });
}
