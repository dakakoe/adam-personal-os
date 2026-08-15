"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Calendar, CheckSquare, Folder, Plus } from "lucide-react";
import { TaskDetailPanel } from "@/components/task-detail-panel";
import { CreateTaskDialog } from "@/components/tasks-client";
import { ScheduleGrid, buildDays } from "@/components/schedule-grid";
import { zonedMidnightMs } from "@/lib/day-layout";
import { Button } from "@/components/ui/button";
import { api, type CalendarEvent, type TaskRow, type ProjectRow, type TodayCounts } from "@/lib/api";
import { taskTimeStatus, cn } from "@/lib/utils";
import { toast } from "sonner";
import { celebrateCompletion } from "@/lib/celebrate";

const TIME_TONE: Record<string, string> = {
  danger: "text-rose-400 border-rose-500/40",
  warn: "text-amber-400 border-amber-500/40",
  muted: "text-muted-foreground border-border",
};

function fmtTime(iso: string | null, tz?: string): string {
  if (!iso) return "";
  try {
    return new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit", minute: "2-digit", hour12: false,
      ...(tz ? { timeZone: tz } : {}),
    }).format(new Date(iso));
  } catch { return ""; }
}

function fmtDay(iso: string | null, tz?: string): string {
  if (!iso) return "";
  try {
    return new Intl.DateTimeFormat("en-US", {
      weekday: "short", month: "short", day: "numeric",
      ...(tz ? { timeZone: tz } : {}),
    }).format(new Date(iso));
  } catch { return ""; }
}

/** Today's live agenda from /api/events (raw Google Calendar sync — fresh
 *  within ~30 min), rendered as a Google-Calendar-style day grid. */
function ScheduleSection({ events, tz }: { events: CalendarEvent[]; tz?: string }) {
  const zone = tz || "Asia/Bangkok";
  const [nowMs, setNowMs] = useState(0);
  useEffect(() => {
    setNowMs(Date.now());
    const t = setInterval(() => setNowMs(Date.now()), 60_000);
    return () => clearInterval(t);
  }, []);
  const todayKey = new Intl.DateTimeFormat("en-CA", { timeZone: zone }).format(new Date());
  const days = buildDays(zonedMidnightMs(todayKey, zone), 1, null, zone, todayKey);

  const header = (
    <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-1.5">
      <Calendar className="h-3.5 w-3.5" /> Schedule
      <span className="font-normal normal-case text-[11px]">· live from Google Calendar</span>
      <Link href="/schedule" className="ml-auto font-normal normal-case text-[11px] text-primary hover:underline">Schedule →</Link>
    </h2>
  );
  if (events.length === 0) {
    return (
      <div>
        {header}
        <div className="rounded-md border border-border bg-card/40 p-6 text-center text-xs text-muted-foreground">No meetings.</div>
      </div>
    );
  }
  return (
    <div>
      {header}
      <ScheduleGrid days={days} events={events} tz={zone} nowMs={nowMs} maxHeight="32rem" />
    </div>
  );
}

function TasksToday({ tasks, projects, onOpen, onAdd }: { tasks: TaskRow[]; projects: ProjectRow[]; onOpen: (id: string) => void; onAdd: () => void }) {
  const router = useRouter();
  async function toggleDone(t: TaskRow, done: boolean) {
    try {
      await api.patchTask(t.id, { status: done ? "done" : "open" });
      if (done) void celebrateCompletion(async () => {
        try { await api.patchTask(t.id, { status: "open" }); router.refresh(); }
        catch (e) { toast.error(e instanceof Error ? e.message : "Undo failed"); }
      });
      router.refresh();
    }
    catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
  }
  // Group by project (project order first, Unassigned last).
  const buckets = new Map<string, TaskRow[]>();
  for (const t of tasks) {
    const key = t.project_id ?? "__none__";
    (buckets.get(key) ?? buckets.set(key, []).get(key)!).push(t);
  }
  const groups: { project: ProjectRow | null; items: TaskRow[] }[] = [];
  for (const p of projects) {
    const items = buckets.get(p.id);
    if (items?.length) groups.push({ project: p, items });
  }
  const none = buckets.get("__none__");
  if (none?.length) groups.push({ project: null, items: none });

  return (
    <div>
      <div className="flex items-center justify-between gap-2 mb-2">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
          <CheckSquare className="h-3.5 w-3.5" /> Tasks for today
          <span className="font-normal normal-case">({tasks.length})</span>
        </h2>
        <Button size="sm" variant="outline" className="h-7" onClick={onAdd}>
          <Plus className="h-3.5 w-3.5 mr-1" /> New task
        </Button>
      </div>
      {tasks.length === 0 ? (
        <div className="rounded-md border border-border bg-card/40 p-6 text-center text-xs text-muted-foreground">
          Nothing due today. Enjoy the breathing room.
        </div>
      ) : (
        groups.map((g, gi) => (
          <div key={gi} className="mb-2">
            <div className="flex items-center gap-2 px-1 pb-1 text-[11px] font-medium text-muted-foreground">
              {g.project ? (
                <><span className="h-2 w-2 rounded-full shrink-0" style={{ backgroundColor: g.project.color ?? "var(--color-muted-foreground)" }} />{g.project.name}</>
              ) : (
                <><Folder className="h-3 w-3" /> Unassigned</>
              )}
            </div>
            <ul className="rounded-md border border-border bg-card/40 divide-y divide-border overflow-hidden">
              {g.items.map((t) => {
                const ts = taskTimeStatus(t.due_date, t.status);
                return (
                  <li key={t.id} className="flex items-center gap-2 px-3 py-2 hover:bg-accent/20 transition-colors">
                    <input type="checkbox" title="Mark done"
                      checked={t.status === "done"}
                      onChange={(e) => toggleDone(t, e.target.checked)}
                      className="h-3.5 w-3.5 rounded border-border accent-emerald-500 shrink-0" />
                    <button onClick={() => onOpen(t.id)} className="flex-1 min-w-0 text-left flex items-center gap-2">
                      <span className={cn("flex-1 min-w-0 truncate text-sm",
                        (t.status === "done" || t.status === "cancelled") && "line-through text-muted-foreground")}>{t.title}</span>
                      {ts && <span className={cn("text-[10px] font-mono px-1 rounded border shrink-0", TIME_TONE[ts.tone])}>{ts.label}</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))
      )}
    </div>
  );
}

function CountPill({ label, value, href }: { label: string; value: number; href: string }) {
  return (
    <Link href={href} className="flex flex-col items-start rounded-md border border-border bg-card/40 px-3 py-2 hover:bg-accent/30 transition-colors min-w-[5.5rem]">
      <span className="text-lg font-semibold tabular leading-none">{value}</span>
      <span className="text-[11px] text-muted-foreground mt-1">{label}</span>
    </Link>
  );
}

export function TodayClient({
  counts, events, eventsTz, todayTasks, projects,
}: {
  counts: TodayCounts | null;
  events: CalendarEvent[];
  eventsTz?: string;
  todayTasks: TaskRow[];
  projects: ProjectRow[];
}) {
  const router = useRouter();
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  return (
    <div>
      {counts && (
        <div className="mb-5 flex flex-wrap gap-2">
          <CountPill label="meetings today" value={counts.meetings_today} href="/today" />
          <CountPill label="open tasks" value={counts.open_tasks} href="/plan" />
          <CountPill label="live deals" value={counts.live_opps} href="/plan" />
          <CountPill label="owed a reply" value={counts.owed_reply} href="/persons" />
          <CountPill label="inbox" value={counts.pending_suggestions} href="/suggestions" />
        </div>
      )}

      {/* Schedule + Tasks for today, side by side on wide screens */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
        <ScheduleSection events={events} tz={eventsTz} />
        <TasksToday tasks={todayTasks} projects={projects} onOpen={setOpenTaskId} onAdd={() => setCreateOpen(true)} />
      </div>

      {/* Opened from a day-scoped page, so it opens already dated today —
          otherwise the task comes out undated and instantly disappears from
          the list you just added it to ("Tasks for today" is due_date <= today).
          Still a normal editable field: a default, not a lock.
          Asia/Bangkok matches the day boundary the page itself filters on. */}
      <CreateTaskDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        projects={projects}
        defaults={{
          due_date: new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(new Date()),
        }}
      />

      <TaskDetailPanel
        taskId={openTaskId}
        onClose={() => setOpenTaskId(null)}
        onChanged={() => router.refresh()}
        projects={projects}
      />
    </div>
  );
}
