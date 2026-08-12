import { cookies } from "next/headers";
import { Sun } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { FirstRunBanner } from "@/components/first-run-banner";
import { UpdateBanner } from "@/components/update-banner";
import { TodayClient } from "@/components/today-client";
import { StatsStrip } from "@/components/stats-strip";
import { api, type CalendarEvent, type TaskRow, type ProjectRow, type TodayCounts, type Stats } from "@/lib/api";

async function fetchData() {
  const cookie = (await cookies()).toString();
  const opts = { cookieHeader: cookie };
  let counts: TodayCounts | null = null;
  let stats: Stats | null = null;
  let events: CalendarEvent[] = [];
  let eventsTz: string | undefined;
  let tasks: TaskRow[] = [];
  let projects: ProjectRow[] = [];
  try { counts = await api.getTodayCounts(opts); } catch { /* ignore */ }
  try { stats = await api.getStats(opts); } catch { /* ignore */ }
  try {
    const ev = await api.listEvents({ days: 2 }, opts);   // live agenda: today + tomorrow
    events = ev.events;
    eventsTz = ev.timezone;
  } catch { /* no calendar synced yet */ }
  try {
    const [open, doing, projs] = await Promise.all([
      api.listTasks({ status: "open", limit: 200 }, opts),
      api.listTasks({ status: "doing", limit: 200 }, opts),
      api.listProjects({}, opts),
    ]);
    projects = projs;
    // "For today" = open/doing tasks due on or before today (overdue + due
    // today). Local date in Asia/Bangkok keeps the day boundary aligned with
    // the rest of the system.
    const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(new Date()); // YYYY-MM-DD
    tasks = [...doing, ...open].filter((t) => t.due_date != null && t.due_date <= today);
  } catch { /* ignore */ }
  return { counts, stats, events, eventsTz, tasks, projects };
}

export default async function TodayPage() {
  const { counts, stats, events, eventsTz, tasks, projects } = await fetchData();

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-6xl">
        <UpdateBanner />
        <FirstRunBanner />
        <header className="mb-4 sm:mb-6">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Sun className="h-5 w-5 text-muted-foreground" />
            Today
          </h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-1">
            Your day at a glance — live schedule and what&apos;s due today by project.
          </p>
        </header>

        {stats && <StatsStrip stats={stats} />}
        <TodayClient counts={counts} events={events} eventsTz={eventsTz} todayTasks={tasks} projects={projects} />
      </div>
    </AppShell>
  );
}
