"use client";

import { useCallback, useEffect, useState } from "react";
import {
  X, Plus, Trash2, Sparkles, UserPlus, User, CalendarClock, CalendarCheck, Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { PersonPicker } from "@/components/person-picker";
import { ProjectBadge } from "@/components/project-badge";
import {
  api, calendarOptions, type CalendarOption,
  type TaskDetail, type TaskStatus, type TaskRow, type ProjectRow,
} from "@/lib/api";
import { taskTimeStatus, formatRelativeDate, cn } from "@/lib/utils";
import { DURATION_OPTIONS, durationLabel } from "@/components/repeat-control";
import { toast } from "sonner";

/** Local YYYY-MM-DD for today + offsetDays (for the Today/Tomorrow buttons). */
function ymd(offsetDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const STATUS_OPTIONS: TaskStatus[] = ["open", "doing", "done", "cancelled"];
const TONE: Record<string, string> = {
  danger: "text-rose-400 border-rose-500/40",
  warn: "text-amber-400 border-amber-500/40",
  muted: "text-muted-foreground border-border",
};

export function TaskDetailPanel({
  taskId, onClose, onChanged, projects = [],
}: {
  taskId: string | null;
  onClose: () => void;
  onChanged: () => void;
  projects?: ProjectRow[];
}) {
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [newSub, setNewSub] = useState("");
  const [decomposeOpen, setDecomposeOpen] = useState(false);
  const [proposed, setProposed] = useState<string[]>([]);
  const [picked, setPicked] = useState<Record<number, boolean>>({});
  const [decomposing, setDecomposing] = useState(false);

  const open = taskId !== null;

  const reload = useCallback(async () => {
    if (!taskId) return;
    const d = await api.getTask(taskId);
    setTask(d);
    setTitle(d.title);
    setDesc(d.description ?? "");
  }, [taskId]);

  useEffect(() => {
    if (!taskId) { setTask(null); return; }
    setLoading(true);
    reload().catch((e) => toast.error(e instanceof Error ? e.message : "Load failed")).finally(() => setLoading(false));
  }, [taskId, reload]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    if (open) document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  async function patch(fields: Record<string, unknown>) {
    if (!taskId) return;
    try {
      await api.patchTask(taskId, fields);
      await reload();
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Update failed");
    }
  }

  const [calendars, setCalendars] = useState<CalendarOption[]>([]);
  useEffect(() => {
    if (open) api.listCalendars().then((r) => setCalendars(calendarOptions(r))).catch(() => {});
  }, [open]);

  async function addCal(value: string) {
    const o = calendars.find((c) => c.value === value);
    if (!taskId || !o) return;
    try { await api.addTaskToCalendar(taskId, o.account, o.calendar_id); await reload(); onChanged(); toast.success("Added to calendar"); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Calendar sync failed"); }
  }
  async function removeCal() {
    if (!taskId) return;
    try { await api.removeTaskFromCalendar(taskId); await reload(); onChanged(); toast.success("Removed from calendar"); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Remove failed"); }
  }

  async function saveTitle() {
    const t = title.trim();
    if (!task || !t || t === task.title) return;
    await patch({ title: t });
  }
  async function saveDesc() {
    if (!task || desc === (task.description ?? "")) return;
    await patch({ description: desc || null });
  }

  async function setAssignee(personId: string | null) {
    await patch({ assignee_person_id: personId });
  }

  async function addPerson(personId: string) {
    if (!taskId) return;
    try { await api.addTaskPerson(taskId, personId); await reload(); onChanged(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Add failed"); }
  }
  async function removePerson(personId: string) {
    if (!taskId) return;
    try { await api.removeTaskPerson(taskId, personId); await reload(); onChanged(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Remove failed"); }
  }

  async function toggleSub(sub: TaskRow) {
    const next = sub.status === "done" ? "open" : "done";
    try { await api.patchTask(sub.id, { status: next }); await reload(); onChanged(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
  }
  async function deleteSub(sub: TaskRow) {
    try { await api.deleteTask(sub.id); await reload(); onChanged(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }
  async function removeTask() {
    if (!taskId || !task) return;
    if (!confirm(`Delete task "${task.title}"? This can't be undone.`)) return;
    try {
      await api.deleteTask(taskId);
      toast.success("Task deleted");
      onClose(); onChanged();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }
  async function addSub() {
    const t = newSub.trim();
    if (!taskId || !t) return;
    try { await api.createSubtasks(taskId, [t]); setNewSub(""); await reload(); onChanged(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Add failed"); }
  }

  async function runDecompose() {
    if (!taskId) return;
    setDecomposing(true);
    try {
      const r = await api.decomposeTask(taskId);
      if (!r.subtasks.length) { toast.info("No subtasks proposed — looks atomic."); return; }
      setProposed(r.subtasks);
      setPicked(Object.fromEntries(r.subtasks.map((_, i) => [i, true])));
      setDecomposeOpen(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Decompose failed");
    } finally {
      setDecomposing(false);
    }
  }
  async function confirmDecompose() {
    if (!taskId) return;
    const titles = proposed.filter((_, i) => picked[i]);
    if (!titles.length) { setDecomposeOpen(false); return; }
    try {
      await api.createSubtasks(taskId, titles);
      toast.success(`Added ${titles.length} subtask${titles.length > 1 ? "s" : ""}`);
      setDecomposeOpen(false);
      await reload(); onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Create failed");
    }
  }

  const ts = task ? taskTimeStatus(task.due_date, task.status) : null;

  return (
    <>
      {/* Backdrop */}
      <div
        className={cn(
          "fixed inset-0 z-40 bg-background/70 backdrop-blur-sm transition-opacity",
          open ? "opacity-100" : "opacity-0 pointer-events-none",
        )}
        onClick={onClose}
        aria-hidden="true"
      />
      {/* Slide-over */}
      <aside
        className={cn(
          "fixed inset-y-0 right-0 z-50 w-full sm:w-[30rem] bg-card border-l border-border shadow-2xl",
          "transition-transform duration-200 overflow-y-auto",
          open ? "translate-x-0" : "translate-x-full",
        )}
        role="dialog"
        aria-label="Task detail"
      >
        {!task ? (
          <div className="grid place-items-center h-full text-muted-foreground">
            {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : null}
          </div>
        ) : (
          <div className="p-4 sm:p-5 space-y-5">
            {/* Header */}
            <div className="flex items-start gap-2">
              <div className="flex-1 min-w-0 space-y-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <select
                    value={task.status}
                    onChange={(e) => patch({ status: e.target.value })}
                    className={cn(
                      "h-6 text-[10px] font-mono uppercase px-1 rounded border border-border bg-secondary/40",
                      task.status === "done" && "text-emerald-400",
                      task.status === "doing" && "text-amber-400",
                      task.status === "cancelled" && "text-muted-foreground",
                    )}
                  >
                    {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                  {ts && (
                    <span className={cn("text-[10px] font-mono px-1.5 py-0.5 rounded border inline-flex items-center gap-1", TONE[ts.tone])}>
                      <CalendarClock className="h-3 w-3" /> {ts.label}
                    </span>
                  )}
                  <ProjectBadge slug={task.project_slug} name={task.project_name} color={task.project_color} />
                </div>
                <textarea
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  onBlur={saveTitle}
                  rows={2}
                  className="w-full bg-transparent text-lg font-semibold leading-snug outline-none resize-none focus:bg-accent/20 rounded px-1 -mx-1"
                />
              </div>
              <button onClick={onClose} className="grid place-items-center h-8 w-8 rounded-md hover:bg-accent text-muted-foreground shrink-0" aria-label="Close">
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Field grid */}
            <div className="grid grid-cols-[5.5rem_1fr] gap-y-3 gap-x-3 items-center text-sm">
              {/* Project */}
              <span className="text-xs text-muted-foreground">Project</span>
              <select
                value={task.project_id ?? ""}
                onChange={(e) => patch({ project_id: e.target.value || null })}
                className="h-8 px-2 text-sm rounded-md border border-border bg-background w-fit max-w-full"
              >
                <option value="">(none)</option>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>

              {/* Assignee */}
              <span className="text-xs text-muted-foreground">Assignee</span>
              <div className="flex items-center gap-2">
                <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border",
                  task.assignee_person_id ? "border-border" : "border-primary/40 text-primary")}>
                  <User className="h-3 w-3" />
                  {task.assignee_name ?? "Me"}
                </span>
                <PersonPicker
                  onPick={(p) => setAssignee(p.person_id)}
                  trigger={<span className="text-[11px] text-muted-foreground hover:text-foreground">change</span>}
                />
                {task.assignee_person_id && (
                  <button onClick={() => setAssignee(null)} className="text-[11px] text-muted-foreground hover:text-foreground">→ Me</button>
                )}
              </div>

              {/* Deadline */}
              <span className="text-xs text-muted-foreground">Deadline</span>
              <div className="flex items-center gap-1.5 flex-wrap">
                <input
                  type="date"
                  value={task.due_date ?? ""}
                  onChange={(e) => patch({ due_date: e.target.value || null })}
                  className="h-8 px-2 text-sm rounded-md border border-border bg-background w-fit"
                />
                <input
                  type="time"
                  value={task.due_time ? task.due_time.slice(0, 5) : ""}
                  disabled={!task.due_date}
                  title={task.due_date ? "Time (optional)" : "Set a date first"}
                  onChange={(e) => patch({ due_time: e.target.value || null })}
                  className="h-8 px-2 text-sm rounded-md border border-border bg-background w-fit [color-scheme:dark]"
                />
                {task.due_time && (
                  <select value={task.duration_min ?? 60} onChange={(e) => patch({ duration_min: Number(e.target.value) })}
                    title="How long it lasts (on the calendar)"
                    className="h-8 px-2 text-sm rounded-md border border-border bg-background">
                    {DURATION_OPTIONS.map((m) => <option key={m} value={m}>{durationLabel(m)}</option>)}
                  </select>
                )}
                <button type="button" onClick={() => patch({ due_date: ymd(0) })}
                  className="h-7 px-2 text-[11px] rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-foreground">Today</button>
                <button type="button" onClick={() => patch({ due_date: ymd(1) })}
                  className="h-7 px-2 text-[11px] rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-foreground">Tomorrow</button>
                {task.due_date && (
                  <button type="button" onClick={() => patch({ due_date: null, due_time: null })}
                    className="h-7 px-2 text-[11px] rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-foreground">Clear</button>
                )}
              </div>

              {/* Google Calendar sync */}
              <span className="text-xs text-muted-foreground">Calendar</span>
              <div className="flex items-center gap-2 flex-wrap">
                {task.gcal_account ? (
                  <>
                    <a href={task.gcal_html_link ?? "#"} target="_blank" rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-emerald-400 hover:underline">
                      <CalendarCheck className="h-3.5 w-3.5" /> On {
                        calendars.find((o) => o.account === task.gcal_account
                          && o.calendar_id === (task.gcal_calendar_id ?? null))?.label ?? task.gcal_account}
                    </a>
                    <button type="button" onClick={removeCal}
                      className="text-[11px] text-muted-foreground hover:text-foreground">Remove</button>
                  </>
                ) : !task.due_date ? (
                  <span className="text-xs text-muted-foreground/70">add a date to sync</span>
                ) : calendars.length > 0 ? (
                  <select defaultValue="" onChange={(e) => addCal(e.target.value)}
                    className="h-8 px-2 text-sm rounded-md border border-border bg-background">
                    <option value="">Add to calendar…</option>
                    {calendars.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                ) : (
                  <span className="text-xs text-muted-foreground/70">no connected calendars</span>
                )}
              </div>

              {/* People involved */}
              <span className="text-xs text-muted-foreground pt-1">People</span>
              <div className="flex flex-wrap items-center gap-1.5">
                {task.people.length === 0 && (
                  <span className="text-xs text-muted-foreground/70">none yet</span>
                )}
                {task.people.map((p) => (
                  <span key={p.person_id} className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-full text-xs border border-border bg-secondary/40">
                    {p.display_name}
                    <button onClick={() => removePerson(p.person_id)} className="grid place-items-center h-4 w-4 rounded-full hover:bg-destructive hover:text-destructive-foreground" aria-label="Remove">
                      <X className="h-2.5 w-2.5" />
                    </button>
                  </span>
                ))}
                <PersonPicker
                  onPick={(p) => addPerson(p.person_id)}
                  trigger={<span className="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground hover:text-foreground border border-dashed border-border rounded-full px-1.5 py-0.5"><UserPlus className="h-3 w-3" /> add</span>}
                />
              </div>
            </div>

            {/* Description */}
            <div>
              <div className="text-xs text-muted-foreground mb-1">Description</div>
              <textarea
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                onBlur={saveDesc}
                rows={3}
                placeholder="Add detail…"
                className="w-full text-sm rounded-md border border-border bg-background px-2 py-1.5 outline-none resize-y"
              />
            </div>

            {/* Subtasks */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs text-muted-foreground">
                  Subtasks {task.subtask_total > 0 && <span className="tabular">· {task.subtask_done}/{task.subtask_total}</span>}
                </div>
                <Button size="sm" variant="outline" className="h-7 px-2 text-xs" disabled={decomposing} onClick={runDecompose}>
                  {decomposing ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Sparkles className="h-3.5 w-3.5 mr-1" />}
                  Decompose with AI
                </Button>
              </div>
              <ul className="space-y-1">
                {task.subtasks.map((s) => (
                  <li key={s.id} className="flex items-center gap-2 group">
                    <input
                      type="checkbox"
                      checked={s.status === "done"}
                      onChange={() => toggleSub(s)}
                      className="h-3.5 w-3.5 rounded border-border accent-emerald-500"
                    />
                    <span className={cn("flex-1 text-sm", s.status === "done" && "line-through text-muted-foreground")}>{s.title}</span>
                    <button onClick={() => deleteSub(s)} className="opacity-0 group-hover:opacity-100 grid place-items-center h-6 w-6 rounded text-muted-foreground hover:text-destructive" aria-label="Delete subtask">
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
              <div className="flex items-center gap-2 mt-2">
                <Input
                  value={newSub}
                  onChange={(e) => setNewSub(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") addSub(); }}
                  placeholder="Add a subtask…"
                  className="h-8 text-sm"
                />
                <Button size="sm" variant="outline" className="h-8 px-2" disabled={!newSub.trim()} onClick={addSub}>
                  <Plus className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>

            {/* Meta */}
            <div className="flex items-center justify-between gap-2 border-t border-border pt-3">
              <span className="text-[11px] text-muted-foreground/60">
                created {formatRelativeDate(task.created_at)}
                {task.source_kind && task.source_kind !== "manual" && <> · {task.source_kind}</>}
              </span>
              <button onClick={removeTask}
                className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-destructive">
                <Trash2 className="h-3.5 w-3.5" /> Delete task
              </button>
            </div>
          </div>
        )}
      </aside>

      {/* Decompose proposal dialog */}
      <Dialog open={decomposeOpen} onOpenChange={setDecomposeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Proposed subtasks</DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">Pick the ones to add. Nothing is created until you confirm.</p>
          <ul className="space-y-1.5 max-h-72 overflow-auto">
            {proposed.map((t, i) => (
              <li key={i} className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={!!picked[i]}
                  onChange={(e) => setPicked((p) => ({ ...p, [i]: e.target.checked }))}
                  className="mt-1 h-3.5 w-3.5 rounded border-border accent-primary"
                />
                <span className="text-sm">{t}</span>
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDecomposeOpen(false)}>Cancel</Button>
            <Button onClick={confirmDecompose} disabled={!Object.values(picked).some(Boolean)}>
              Add {Object.values(picked).filter(Boolean).length} subtask{Object.values(picked).filter(Boolean).length === 1 ? "" : "s"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
