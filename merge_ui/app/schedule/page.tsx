import { cookies } from "next/headers";
import { AppShell } from "@/components/app-shell";
import { ScheduleClient } from "@/components/schedule-client";
import { api, type CalendarEvent } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function SchedulePage() {
  const cookie = (await cookies()).toString();
  const tz = "Asia/Bangkok";
  const todayKey = new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(new Date()); // YYYY-MM-DD
  let events: CalendarEvent[] = [];
  let timezone = tz;
  try {
    const r = await api.listEvents({ start: todayKey, end: todayKey + "T23:59:59" }, { cookieHeader: cookie });
    events = r.events;
    timezone = r.timezone || tz;
  } catch { /* no calendar synced yet */ }

  return (
    <AppShell>
      <div className="max-w-6xl mx-auto w-full">
        <ScheduleClient initialEvents={events} tz={timezone} todayKey={todayKey} />
      </div>
    </AppShell>
  );
}
