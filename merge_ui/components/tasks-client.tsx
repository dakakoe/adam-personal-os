"use client";

import { useState, useEffect, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Plus, Trash2, User, Users, ListTree, CalendarClock, Folder, Repeat, CalendarPlus, CalendarCheck, Target, Circle, CheckCircle2, X, UserPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { api, calendarOptions, type CalendarOption, type TaskRow, type ProjectRow, type PersonRow } from "@/lib/api";
import { PersonPicker } from "@/components/person-picker";
import { ProjectBadge } from "@/components/project-badge";
import { TaskDetailPanel } from "@/components/task-detail-panel";
import { taskTimeStatus, dueBucket, type DueBucket, cn } from "@/lib/utils";
import { RepeatControl, repeatToApi, REPEAT_NONE, findRoutineConflict, type RepeatValue, DURATION_OPTIONS, durationLabel } from "@/components/repeat-control";
import { RoutinesPanel } from "@/components/routines-panel";
import { toast } from "sonner";
import { celebrateCompletion } from "@/lib/celebrate";

const BUCKETS: { key: DueBucket; label: string; dot: string }[] = [
  { key: "overdue", label: "Overdue", dot: "bg-rose-500" },
  { key: "today", label: "Today", dot: "bg-amber-500" },
  { key: "week", label: "This week", dot: "bg-emerald-500" },
  { key: "later", label: "Later", dot: "bg-sky-500" },
  { key: "none", label: "No date", dot: "bg-muted-foreground/60" },
];

const FILTER_SELECT = "h-7 text-xs px-1.5 rounded-md border border-border bg-secondary/40 text-muted-foreground";
const QUICK_BTN = "h-8 px-2 text-[11px] rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-foreground";

const TIME_TONE: Record<string, string> = {
  danger: "text-rose-400 border-rose-500/40",
  warn: "text-amber-400 border-amber-500/40",
  muted: "text-muted-foreground border-border",
};


/** Local YYYY-MM-DD for today + offsetDays (Today/Tomorrow quick buttons). */
function ymd(offsetDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** A tick to mark a task done (toggles open↔done) — the list stays a simple
 *  done/not-done checkbox. Full status options (doing/cancelled) still live in the
 *  task detail panel, so that functionality is preserved for later. */
function DoneToggle({ task }: { task: TaskRow }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const done = task.status === "done";
  async function toggle(e: React.MouseEvent) {
    e.stopPropagation();
    setBusy(true);
    try {
      await api.patchTask(task.id, { status: done ? "open" : "done" });
      if (!done) void celebrateCompletion(async () => {
        try { await api.patchTask(task.id, { status: "open" }); router.refresh(); }
        catch (err) { toast.error(err instanceof Error ? err.message : "Undo failed"); }
      });
      router.refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <button
      type="button"
      onClick={toggle}
      disabled={busy}
      title={done ? "Mark open" : "Mark done"}
      className="grid place-items-center h-7 w-7 shrink-0 rounded text-muted-foreground hover:text-foreground transition-colors"
    >
      {done ? <CheckCircle2 className="h-5 w-5 text-emerald-400" /> : <Circle className="h-5 w-5" />}
    </button>
  );
}

export function TasksClient({
  initialTasks, projects, projectId,
}: {
  initialTasks: TaskRow[];
  projects: ProjectRow[];
  projectId?: string;   // when rendered on a project page: scope routines + default new-task project
}) {
  const router = useRouter();
  const [createOpen, setCreateOpen] = useState(false);
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [view, setView] = useState<"tasks" | "routines">("tasks");
  const [group, setGroup] = useState<"due" | "project">("due");
  const [status, setStatus] = useState("active");          // active = open+doing
  const [projectFilter, setProjectFilter] = useState("");
  const [assigneeFilter, setAssigneeFilter] = useState(""); // "" any, "__me__", or person_id
  const [q, setQ] = useState("");
  const [quick, setQuick] = useState("");
  const [, startTransition] = useTransition();
  const refresh = () => startTransition(() => router.refresh());

  // Press "n" to add a task (ignored while typing in a field or a dialog is open).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "n" && e.key !== "N") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      if (createOpen || openTaskId) return;
      e.preventDefault();
      setCreateOpen(true);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [createOpen, openTaskId]);

  // Assignees present in the data (for the filter).
  const assignees = Array.from(
    new Map(initialTasks.map((t) => [t.assignee_person_id ?? "__me__", t.assignee_name ?? "Me"])),
  ).sort((a, b) => a[1].localeCompare(b[1]));

  // Client-side filtering.
  let tasks = initialTasks;
  if (status === "active") tasks = tasks.filter((t) => t.status === "open" || t.status === "doing");
  else if (status !== "all") tasks = tasks.filter((t) => t.status === status);
  if (projectFilter) tasks = tasks.filter((t) => (t.project_id ?? "") === projectFilter);
  if (assigneeFilter) tasks = tasks.filter((t) => (t.assignee_person_id ?? "__me__") === assigneeFilter);
  if (q.trim()) {
    const qq = q.trim().toLowerCase();
    tasks = tasks.filter((t) => t.title.toLowerCase().includes(qq));
  }

  // Grouping.
  const groups: { label: string; dot?: string; color?: string | null; items: TaskRow[] }[] = [];
  if (group === "due") {
    for (const b of BUCKETS) {
      const items = tasks.filter((t) => dueBucket(t.due_date) === b.key);
      if (items.length) groups.push({ label: b.label, dot: b.dot, items });
    }
  } else {
    const byId = new Map<string, TaskRow[]>();
    for (const t of tasks) {
      const k = t.project_id ?? "__none__";
      (byId.get(k) ?? byId.set(k, []).get(k)!).push(t);
    }
    for (const p of projects) {
      const items = byId.get(p.id);
      if (items?.length) groups.push({ label: p.name, color: p.color, items });
    }
    const none = byId.get("__none__");
    if (none?.length) groups.push({ label: "Unassigned", color: null, items: none });
  }

  async function onDelete(t: TaskRow) {
    if (!confirm(`Delete task "${t.title}"?`)) return;
    try { await api.deleteTask(t.id); toast.success("Deleted"); refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }
  async function quickAdd(due?: string) {
    const t = quick.trim();
    if (!t) return;
    try {
      await api.createTask({ title: t, due_date: due, project_id: projectFilter || undefined });
      setQuick(""); refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Add failed"); }
  }

  return (
    <>
      {/* Toolbar */}
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <div className="inline-flex rounded-md border border-border overflow-hidden">
          {(["tasks", "routines"] as const).map((v) => (
            <button key={v} onClick={() => setView(v)}
              className={cn("px-2.5 py-1 inline-flex items-center gap-1", view === v ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
              {v === "routines" && <Repeat className="h-3 w-3" />}
              {v === "tasks" ? "Tasks" : "Routines"}
            </button>
          ))}
        </div>
        {view === "tasks" && (
          <>
            <div className="inline-flex rounded-md border border-border overflow-hidden">
              {(["due", "project"] as const).map((g) => (
                <button key={g} onClick={() => setGroup(g)}
                  className={cn("px-2.5 py-1", group === g ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
                  {g === "due" ? "Due date" : "Project"}
                </button>
              ))}
            </div>
            <select value={status} onChange={(e) => setStatus(e.target.value)} title="Status" className={FILTER_SELECT}>
              <option value="active">Active</option>
              <option value="open">open</option>
              <option value="doing">doing</option>
              <option value="done">done</option>
              <option value="cancelled">cancelled</option>
              <option value="all">All statuses</option>
            </select>
            <select value={projectFilter} onChange={(e) => setProjectFilter(e.target.value)} title="Project" className={FILTER_SELECT}>
              <option value="">All projects</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            {assignees.length > 1 && (
              <select value={assigneeFilter} onChange={(e) => setAssigneeFilter(e.target.value)} title="Assignee" className={FILTER_SELECT}>
                <option value="">Anyone</option>
                {assignees.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
              </select>
            )}
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…"
              className="h-7 px-2 rounded-md border border-border bg-background w-32 outline-none" />
          </>
        )}
        <Button size="sm" className="ml-auto" onClick={() => setCreateOpen(true)} title="New task (press N)">
          <Plus className="h-3.5 w-3.5 mr-1" /> New task
          <kbd className="ml-1.5 hidden sm:inline-block text-[10px] font-mono px-1 rounded border border-primary-foreground/30 text-primary-foreground/80">N</kbd>
        </Button>
      </div>

      {view === "routines" ? (
        <RoutinesPanel projectId={projectId} projects={projects} />
      ) : (
      <>
      {/* Quick add */}
      <div className="mb-4 flex items-center gap-1.5">
        <Input value={quick} onChange={(e) => setQuick(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") quickAdd(); }}
          placeholder="Quick add a task…" className="h-8 text-sm" />
        <button type="button" onClick={() => quickAdd(ymd(0))} disabled={!quick.trim()} className={QUICK_BTN}>Today</button>
        <button type="button" onClick={() => quickAdd(ymd(1))} disabled={!quick.trim()} className={QUICK_BTN}>Tomorrow</button>
        <button type="button" onClick={() => quickAdd()} disabled={!quick.trim()} className={QUICK_BTN}>Add</button>
      </div>

      {groups.length === 0 ? (
        <div className="rounded-md border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
          No tasks match. Adjust the filters or add one above.
        </div>
      ) : groups.map((g, gi) => (
        <div key={gi} className="mb-4">
          <div className="flex items-center gap-2 px-1 pb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {g.dot ? (
              <span className={cn("h-2 w-2 rounded-full", g.dot)} />
            ) : g.color !== undefined && g.color !== null ? (
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: g.color }} />
            ) : (
              <Folder className="h-3.5 w-3.5" />
            )}
            {g.label} <span className="text-muted-foreground/60 normal-case">({g.items.length})</span>
          </div>
          <ul className="divide-y divide-border rounded-md border border-border overflow-hidden bg-card/40">
            {g.items.map((t) => (
              <TaskRowItem key={t.id} t={t} onOpen={() => setOpenTaskId(t.id)} onDelete={() => onDelete(t)} />
            ))}
          </ul>
        </div>
      ))}
      </>
      )}

      <CreateTaskDialog open={createOpen} onOpenChange={setCreateOpen} projects={projects}
        defaults={projectId ? { project_id: projectId } : undefined} />
      <TaskDetailPanel taskId={openTaskId} onClose={() => setOpenTaskId(null)} onChanged={refresh} projects={projects} />
    </>
  );
}

function TaskRowItem({ t, onOpen, onDelete }: { t: TaskRow; onOpen: () => void; onDelete: () => void }) {
  const ts = taskTimeStatus(t.due_date, t.status);
  return (
    <li className="flex items-center gap-3 px-3 sm:px-4 py-2.5 hover:bg-accent/20 transition-colors">
      <DoneToggle task={t} />
      <button type="button" onClick={onOpen} className="min-w-0 flex-1 text-left">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className={cn("font-medium truncate",
            (t.status === "done" || t.status === "cancelled") && "line-through text-muted-foreground")}>
            {t.title}
          </span>
          <ProjectBadge slug={t.project_slug} name={t.project_name} color={t.project_color} />
          {t.opportunity_title && (
            <span title={`Deal: ${t.opportunity_title}`}
              className="text-[10px] px-1 rounded border border-violet-500/40 text-violet-400 inline-flex items-center gap-0.5 max-w-[12rem] truncate">
              <Target className="h-2.5 w-2.5 shrink-0" /> {t.opportunity_title}
            </span>
          )}
          {t.source_kind === "recurring" && (
            <span title="From a recurring routine"
              className="text-[10px] px-1 rounded border border-sky-500/40 text-sky-400 inline-flex items-center gap-0.5">
              <Repeat className="h-2.5 w-2.5" /> routine
            </span>
          )}
          {ts && (
            <span className={cn("text-[10px] font-mono px-1 rounded border inline-flex items-center gap-0.5", TIME_TONE[ts.tone])}>
              <CalendarClock className="h-2.5 w-2.5" /> {ts.label}
            </span>
          )}
          {t.gcal_account && (
            <span title={`On ${t.gcal_account}'s calendar`}
              className="text-[10px] px-1 rounded border border-emerald-500/40 text-emerald-400 inline-flex items-center gap-0.5">
              <CalendarCheck className="h-2.5 w-2.5" /> calendar
            </span>
          )}
        </div>
        <div className="text-xs text-muted-foreground tabular flex flex-wrap items-center gap-x-2.5 gap-y-0.5 mt-0.5">
          <span className="inline-flex items-center gap-1"><User className="h-3 w-3" />{t.assignee_name ?? "Me"}</span>
          {t.people_count > 0 && <span className="inline-flex items-center gap-1"><Users className="h-3 w-3" />{t.people_count}</span>}
          {t.subtask_total > 0 && <span className="inline-flex items-center gap-1"><ListTree className="h-3 w-3" />{t.subtask_done}/{t.subtask_total}</span>}
          {t.due_date && <span>due {t.due_date}{t.due_time ? ` · ${t.due_time.slice(0, 5)}` : ""}</span>}
          {t.source_kind && t.source_kind !== "manual" && t.source_kind !== "recurring" && <span>{t.source_kind}</span>}
        </div>
      </button>
      <button type="button" onClick={onDelete} title="Delete task"
        className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition-colors shrink-0">
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </li>
  );
}

export function CreateTaskDialog({
  open, onOpenChange, projects, defaults,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  projects: ProjectRow[];
  // due_date lets a day-scoped surface (Today) open the dialog already dated,
  // so a task created there lands where you created it instead of vanishing.
  defaults?: Partial<{ project_id: string; with_person_id: string; due_date: string }>;
}) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [project_id, setProjectId] = useState<string>(defaults?.project_id ?? "");
  const [due_date, setDueDate] = useState(defaults?.due_date ?? "");
  const [due_time, setDueTime] = useState("");
  const [due_dur, setDueDur] = useState(60);
  const [repeat, setRepeat] = useState<RepeatValue>(REPEAT_NONE);
  const [addToCal, setAddToCal] = useState(false);
  const [calValue, setCalValue] = useState("");
  const [calendars, setCalendars] = useState<CalendarOption[]>([]);
  const [participants, setParticipants] = useState<PersonRow[]>([]);
  const [busy, setBusy] = useState(false);

  // Load the connected calendars once the dialog opens.
  useEffect(() => {
    if (!open) return;
    api.listCalendars().then((r) => {
      const opts = calendarOptions(r);
      setCalendars(opts);
      setCalValue((prev) => prev
        || opts.find((o) => o.account === r.default && o.calendar_id === null)?.value
        || opts[0]?.value || "");
    }).catch(() => {});
  }, [open]);

  function reset() {
    setTitle(""); setProjectId(defaults?.project_id ?? ""); setDueDate(defaults?.due_date ?? "");
    setDueTime(""); setDueDur(60);
    setRepeat(REPEAT_NONE); setAddToCal(false); setParticipants([]);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    const rule = repeatToApi(repeat);
    if (rule && rule.freq === "weekly" && rule.byweekday.length === 0) {
      toast.error("Pick at least one day to repeat on");
      return;
    }
    if (rule && rule.at_time) {
      // Soft-warn if another routine already fires at the same time on an overlapping day
      // (scoped to the chosen project, or all routines when none is set).
      try {
        const existing = await api.listRoutines({ projectId: project_id || undefined });
        const clash = findRoutineConflict(rule, existing);
        if (clash && !confirm(`You already have a routine "${clash.title}" at ${rule.at_time}. Create this one too?`)) return;
      } catch { /* non-blocking — a failed check shouldn't stop creation */ }
    }
    setBusy(true);
    try {
      if (rule) {
        const r = await api.createRoutine({
          title: title.trim(),
          project_id: project_id || undefined,
          with_person_id: defaults?.with_person_id || undefined,
          freq: rule.freq, byweekday: rule.byweekday, at_time: rule.at_time,
          duration_min: rule.duration_min,
          participant_ids: participants.map((p) => p.person_id),
        });
        const calOpt = calendars.find((o) => o.value === calValue);
        if (addToCal && calOpt) {
          try { await api.addRoutineToCalendar(r.id, calOpt.account, calOpt.calendar_id); }
          catch (e) { toast.error(e instanceof Error ? e.message : "Routine made, but calendar sync failed"); }
        }
        const invited = addToCal ? participants.filter((p) => p.person_id).length : 0;
        toast.success(addToCal
          ? (invited ? "Routine created + invites sent" : "Routine created + added to calendar")
          : "Routine created — today's instance is on your list");
      } else {
        const created = await api.createTask({
          title: title.trim(),
          project_id: project_id || undefined,
          with_person_id: defaults?.with_person_id || undefined,
          due_date: due_date || undefined,
          due_time: (due_date && due_time) || undefined,
          duration_min: (due_date && due_time) ? due_dur : undefined,
        });
        const calOpt = calendars.find((o) => o.value === calValue);
        if (addToCal && due_date && calOpt) {
          try { await api.addTaskToCalendar(created.id, calOpt.account, calOpt.calendar_id); }
          catch (e) { toast.error(e instanceof Error ? e.message : "Added task, but calendar sync failed"); }
        }
        toast.success(addToCal && due_date ? "Task created + added to calendar" : "Task created");
      }
      reset();
      onOpenChange(false);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Create failed");
    } finally {
      setBusy(false);
    }
  }

  const repeating = repeat.kind !== "none";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New task</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          <div>
            <Label htmlFor="t_title">Title</Label>
            <Input id="t_title" value={title} autoFocus onChange={(e) => setTitle(e.target.value)} placeholder="Ship Phase 2 ingest" />
          </div>
          <div>
            <Label htmlFor="t_project">Project</Label>
            <select
              id="t_project" value={project_id}
              onChange={(e) => setProjectId(e.target.value)}
              className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background"
            >
              <option value="">(none)</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          {/* A one-off due date and a repeat rule are mutually exclusive — a
              recurring routine is scheduled by its rule, not a single date. */}
          {!repeating && (
            <div>
              <Label htmlFor="t_due">Due date &amp; time (optional)</Label>
              <div className="flex items-center gap-1.5 flex-wrap">
                <Input id="t_due" type="date" value={due_date} onChange={(e) => setDueDate(e.target.value)} className="w-fit" />
                <Input id="t_time" type="time" value={due_time} disabled={!due_date}
                  onChange={(e) => setDueTime(e.target.value)} className="w-fit [color-scheme:dark]" title={due_date ? "Time (optional)" : "Pick a date first"} />
                {due_date && due_time && (
                  <select value={due_dur} onChange={(e) => setDueDur(Number(e.target.value))}
                    title="How long it lasts (on the calendar)"
                    className="h-9 px-2 text-sm rounded-md border border-border bg-background">
                    {DURATION_OPTIONS.map((m) => <option key={m} value={m}>{durationLabel(m)}</option>)}
                  </select>
                )}
                <button type="button" onClick={() => setDueDate(ymd(0))}
                  className="h-7 px-2 text-[11px] rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-foreground">Today</button>
                <button type="button" onClick={() => setDueDate(ymd(1))}
                  className="h-7 px-2 text-[11px] rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-foreground">Tomorrow</button>
                {(due_date || due_time) && (
                  <button type="button" onClick={() => { setDueDate(""); setDueTime(""); }}
                    className="h-7 px-2 text-[11px] rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-foreground">Clear</button>
                )}
              </div>
              {/* Calendar sync — only meaningful with a date + a connected account. */}
              {due_date && calendars.length > 0 && (
                <div className="mt-2 flex items-center gap-2 flex-wrap">
                  <label className="inline-flex items-center gap-1.5 text-sm cursor-pointer">
                    <input type="checkbox" checked={addToCal} onChange={(e) => setAddToCal(e.target.checked)}
                      className="h-3.5 w-3.5 rounded border-border accent-primary" />
                    <CalendarPlus className="h-3.5 w-3.5 text-muted-foreground" /> Add to Google Calendar
                  </label>
                  {addToCal && (
                    <select value={calValue} onChange={(e) => setCalValue(e.target.value)}
                      className="h-8 px-2 text-sm rounded-md border border-border bg-background">
                      {calendars.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  )}
                </div>
              )}
            </div>
          )}
          <RepeatControl value={repeat} onChange={setRepeat} />
          {/* A routine syncs as a single recurring calendar event (RRULE). */}
          {repeating && calendars.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              <label className="inline-flex items-center gap-1.5 text-sm cursor-pointer">
                <input type="checkbox" checked={addToCal} onChange={(e) => setAddToCal(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-border accent-primary" />
                <CalendarPlus className="h-3.5 w-3.5 text-muted-foreground" /> Add to Google Calendar
              </label>
              {addToCal && (
                <select value={calValue} onChange={(e) => setCalValue(e.target.value)}
                  className="h-8 px-2 text-sm rounded-md border border-border bg-background">
                  {calendars.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              )}
            </div>
          )}
          {/* Participants get a Google Calendar invite when the routine is on a
              calendar — one invite for the whole series. */}
          {repeating && (
            <div>
              <Label>Participants (optional)</Label>
              <div className="flex items-center gap-1.5 flex-wrap">
                {participants.map((p) => {
                  const reason = p.sensitive ? "Sensitive contact — won't be invited"
                    : !p.email ? "No email on file — won't be invited" : p.email;
                  const blocked = p.sensitive || !p.email;
                  return (
                    <span key={p.person_id} title={reason ?? undefined}
                      className={cn("inline-flex items-center gap-1 h-7 pl-2 pr-1 rounded-full border bg-background text-sm",
                        blocked ? "border-amber-500/50 text-amber-500" : "border-border")}>
                      {p.display_name}
                      {blocked && <span className="text-[10px]">· not invited</span>}
                      <button type="button" title="Remove"
                        onClick={() => setParticipants((cur) => cur.filter((x) => x.person_id !== p.person_id))}
                        className="inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground">
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  );
                })}
                <PersonPicker
                  onPick={(p) => setParticipants((cur) =>
                    cur.some((x) => x.person_id === p.person_id) ? cur : [...cur, p])}
                  trigger={
                    <span className="inline-flex items-center gap-1 h-7 px-2 rounded-full border border-dashed border-border text-sm text-muted-foreground hover:text-foreground hover:border-foreground/40">
                      <UserPlus className="h-3.5 w-3.5" /> Add
                    </span>
                  }
                />
              </div>
              {participants.length > 0 && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {addToCal
                    ? "They'll be emailed a Google Calendar invite for the series."
                    : "Turn on “Add to Google Calendar” to send them invites."}
                </p>
              )}
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
            <Button type="submit" disabled={busy || !title.trim()}>
              {busy ? "Creating…" : repeating ? "Create routine" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
