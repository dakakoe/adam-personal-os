"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { api, type CalendarEvent } from "@/lib/api";
import { dayKeyInTz, zonedMidnightMs } from "@/lib/day-layout";
import { ScheduleGrid, buildDays } from "@/components/schedule-grid";
import { cn } from "@/lib/utils";

const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function shiftKey(key: string, days: number, tz: string): string {
  return dayKeyInTz(zonedMidnightMs(key, tz) + days * 86_400_000, tz);
}
function weekdayIdx(key: string, tz: string): number {
  const wd = new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short" }).format(new Date(zonedMidnightMs(key, tz)));
  return Math.max(0, WD.indexOf(wd));
}

export function ScheduleClient({
  initialEvents, tz, todayKey,
}: {
  initialEvents: CalendarEvent[];
  tz: string;
  todayKey: string;
}) {
  const [view, setView] = useState<"day" | "week">("day");
  const [anchor, setAnchor] = useState(todayKey);      // focused day (YYYY-MM-DD)
  const [events, setEvents] = useState<CalendarEvent[]>(initialEvents);
  const [loading, setLoading] = useState(false);
  const [nowMs, setNowMs] = useState(0);

  // client clock for the now-line (ticks each minute)
  useEffect(() => {
    setNowMs(Date.now());
    const t = setInterval(() => setNowMs(Date.now()), 60_000);
    return () => clearInterval(t);
  }, []);

  const count = view === "week" ? 7 : 1;
  const startKey = view === "week" ? shiftKey(anchor, -weekdayIdx(anchor, tz), tz) : anchor;
  const days = buildDays(zonedMidnightMs(startKey, tz), count, null, tz, todayKey);

  const load = useCallback(async () => {
    const endKey = shiftKey(startKey, count, tz);   // exclusive
    setLoading(true);
    try {
      const r = await api.listEvents({ start: startKey, end: endKey });
      setEvents(r.events);
    } catch { /* keep prior */ } finally { setLoading(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startKey, count, tz]);

  useEffect(() => { load(); }, [load]);

  const step = (dir: number) => setAnchor((k) => shiftKey(k, dir * count, tz));

  const title = (() => {
    const fmt = (key: string, opts: Intl.DateTimeFormatOptions) =>
      new Intl.DateTimeFormat("en-US", { timeZone: tz, ...opts }).format(new Date(zonedMidnightMs(key, tz)));
    if (view === "day") return fmt(anchor, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
    const endKey = shiftKey(startKey, 6, tz);
    const sameMonth = fmt(startKey, { month: "short" }) === fmt(endKey, { month: "short" });
    return `${fmt(startKey, { month: "short", day: "numeric" })} – ${fmt(endKey, sameMonth ? { day: "numeric" } : { month: "short", day: "numeric" })}, ${fmt(endKey, { year: "numeric" })}`;
  })();

  return (
    <div className="flex flex-col gap-3 min-h-0">
      <div className="flex items-center gap-2 flex-wrap">
        <h1 className="text-xl font-semibold mr-auto">{title}</h1>
        {loading && <RefreshCw className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        <button onClick={() => setAnchor(todayKey)}
          className="h-8 px-3 text-sm rounded-md border border-border hover:bg-accent">Today</button>
        <div className="flex items-center rounded-md border border-border overflow-hidden">
          <button onClick={() => step(-1)} className="h-8 w-8 grid place-items-center hover:bg-accent" title="Previous">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <button onClick={() => step(1)} className="h-8 w-8 grid place-items-center hover:bg-accent border-l border-border" title="Next">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
        <div className="flex items-center rounded-md border border-border overflow-hidden text-sm">
          {(["day", "week"] as const).map((v) => (
            <button key={v} onClick={() => setView(v)}
              className={cn("h-8 px-3 capitalize", view === v ? "bg-primary text-primary-foreground" : "hover:bg-accent")}>
              {v}
            </button>
          ))}
        </div>
      </div>

      <ScheduleGrid days={days} events={events} tz={tz} nowMs={nowMs} />
    </div>
  );
}
