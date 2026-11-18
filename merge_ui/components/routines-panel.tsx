"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Repeat, Trash2, Power, CalendarCheck, Pencil, Users, X, UserPlus } from "lucide-react";
import { api, calendarOptions, type CalendarOption, type RecurringTaskRow, type ProjectRow } from "@/lib/api";
import { ProjectBadge } from "@/components/project-badge";
import { PersonPicker } from "@/components/person-picker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  RepeatControl, repeatToApi, apiToRepeat, findRoutineConflict, type RepeatValue,
} from "@/components/repeat-control";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

const DOW3 = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

/** Plain-English schedule for an existing routine row. */
function humanize(r: RecurringTaskRow): string {
  const at = r.at_time ? ` at ${r.at_time.slice(0, 5)}` : "";
  if (r.freq === "daily") return `Every day${at}`;
  if (r.freq === "weekly") {
    const wd = [...r.byweekday].sort((a, b) => a - b);
    if (wd.length === 5 && wd.every((d, i) => d === i)) return `Every weekday${at}`;
    if (!wd.length) return `Weekly${at}`;
    return `Every ${wd.map((d) => DOW3[d]).join(", ")}${at}`;
  }
  const d = new Date(r.anchor_date + "T00:00:00Z");
  if (r.freq === "monthly") return `Monthly on the ${ordinal(d.getUTCDate())}${at}`;
  return `Yearly on ${d.toLocaleString("en", { month: "short", day: "numeric", timeZone: "UTC" })}${at}`;
}

export function RoutinesPanel({ projectId, projects = [] }: { projectId?: string; projects?: ProjectRow[] }) {
  const router = useRouter();
  const [rows, setRows] = useState<RecurringTaskRow[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [calendars, setCalendars] = useState<CalendarOption[]>([]);
  // Inline editor state (one routine at a time).
  const [editId, setEditId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editProjectId, setEditProjectId] = useState("");
  const [editRepeat, setEditRepeat] = useState<RepeatValue>({ kind: "daily", byweekday: [], time: null, duration: null });
  const [editParticipants, setEditParticipants] = useState<{ person_id: string; display_name: string; email: string | null; sensitive: boolean }[]>([]);

  async function load() {
    try { setRows(await api.listRoutines({ projectId })); }
    catch { setRows([]); }
  }
  useEffect(() => { load(); api.listCalendars().then((r) => setCalendars(calendarOptions(r))).catch(() => {}); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [projectId]);

  function startEdit(r: RecurringTaskRow) {
    setEditId(r.id);
    setEditTitle(r.title);
    setEditProjectId(r.project_id ?? "");
    setEditRepeat(apiToRepeat(r));
    setEditParticipants((r.participants ?? []).map((p) => ({ person_id: p.person_id, display_name: p.display_name, email: p.email, sensitive: p.sensitive })));
  }

  async function saveEdit(r: RecurringTaskRow) {
    const title = editTitle.trim();
    if (!title) { toast.error("Title can't be empty"); return; }
    const rule = repeatToApi(editRepeat);
    if (!rule) { toast.error("A routine has to repeat — pick a schedule"); return; }
    if (rule.freq === "weekly" && rule.byweekday.length === 0) { toast.error("Pick at least one day to repeat on"); return; }
    if (rule.at_time && rows) {
      const clash = findRoutineConflict(rule, rows, r.id);
      if (clash && !confirm(`You already have a routine "${clash.title}" at ${rule.at_time}. Keep this time anyway?`)) return;
    }
    setBusy(r.id);
    try {
      await api.patchRoutine(r.id, {
        title, freq: rule.freq, byweekday: rule.byweekday,
        at_time: rule.at_time, duration_min: rule.duration_min,
        // explicit null clears the project (the PATCH route is exclude_unset)
        project_id: editProjectId || null,
        participant_ids: editParticipants.map((p) => p.person_id),
      });
      setEditId(null);
      await load();
      router.refresh(); // schedule change may move today's generated instance
      toast.success("Routine updated");
    } catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
    finally { setBusy(null); }
  }

  async function addCal(r: RecurringTaskRow, value: string) {
    const o = calendars.find((c) => c.value === value);
    if (!o) return;
    setBusy(r.id);
    try { await api.addRoutineToCalendar(r.id, o.account, o.calendar_id); await load(); toast.success("Added to calendar"); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Calendar sync failed"); }
    finally { setBusy(null); }
  }
  async function removeCal(r: RecurringTaskRow) {
    setBusy(r.id);
    try { await api.removeRoutineFromCalendar(r.id); await load(); toast.success("Removed from calendar"); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Remove failed"); }
    finally { setBusy(null); }
  }

  async function toggle(r: RecurringTaskRow) {
    setBusy(r.id);
    try {
      await api.patchRoutine(r.id, { active: !r.active });
      await load();
      router.refresh(); // active→inactive stops future generation; refresh today's list
    } catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
    finally { setBusy(null); }
  }

  async function del(r: RecurringTaskRow) {
    if (!confirm(`Delete routine "${r.title}"? Tasks it already created stay on your list.`)) return;
    setBusy(r.id);
    try {
      await api.deleteRoutine(r.id);
      await load();
      toast.success("Routine deleted");
    } catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
    finally { setBusy(null); }
  }

  if (rows === null) {
    return <p className="text-sm text-muted-foreground px-1 py-6">Loading routines…</p>;
  }
  if (!rows.length) {
    return (
      <div className="rounded-md border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
        No routines {projectId ? "in this project " : ""}yet. Click <span className="text-foreground">New task</span>, then set <span className="text-foreground">Repeat</span> to make it recurring.
      </div>
    );
  }

  return (
    <ul className="divide-y divide-border rounded-md border border-border overflow-hidden bg-card/40">
      {rows.map((r) => (
        <li key={r.id} className={cn("px-3 sm:px-4 py-2.5", !r.active && editId !== r.id && "opacity-55")}>
          {editId === r.id ? (
            <div className="space-y-3 py-1">
              <Input value={editTitle} autoFocus onChange={(e) => setEditTitle(e.target.value)}
                placeholder="Routine title" className="h-8 text-sm" />
              {projects.length > 0 && (
                <select value={editProjectId} onChange={(e) => setEditProjectId(e.target.value)}
                  title="Project"
                  className="block w-full h-8 px-2 text-sm rounded-md border border-border bg-background text-foreground">
                  <option value="">(no project)</option>
                  {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              )}
              <RepeatControl value={editRepeat} onChange={setEditRepeat} />
              <div>
                <p className="text-[11px] text-muted-foreground mb-1">Participants (invited when the routine is on a calendar)</p>
                <div className="flex items-center gap-1.5 flex-wrap">
                  {editParticipants.map((p) => {
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
                          onClick={() => setEditParticipants((cur) => cur.filter((x) => x.person_id !== p.person_id))}
                          className="inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground">
                          <X className="h-3 w-3" />
                        </button>
                      </span>
                    );
                  })}
                  <PersonPicker
                    onPick={(p) => setEditParticipants((cur) =>
                      cur.some((x) => x.person_id === p.person_id) ? cur : [...cur, { person_id: p.person_id, display_name: p.display_name, email: p.email, sensitive: p.sensitive }])}
                    trigger={
                      <span className="inline-flex items-center gap-1 h-7 px-2 rounded-full border border-dashed border-border text-sm text-muted-foreground hover:text-foreground hover:border-foreground/40">
                        <UserPlus className="h-3.5 w-3.5" /> Add
                      </span>
                    }
                  />
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button size="sm" onClick={() => saveEdit(r)} disabled={busy === r.id}>
                  {busy === r.id ? "Saving…" : "Save"}
                </Button>
                <Button size="sm" variant="outline" onClick={() => setEditId(null)} disabled={busy === r.id}>Cancel</Button>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <Repeat className="h-4 w-4 text-sky-400 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className={cn("font-medium truncate", !r.active && "line-through")}>{r.title}</span>
                  {!projectId && <ProjectBadge slug={r.project_slug} name={r.project_name} color={r.project_color} />}
                  {!r.active && <span className="text-[10px] uppercase tracking-wide text-muted-foreground">paused</span>}
                </div>
                <div className="text-xs text-muted-foreground mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-1">
                  <span>{humanize(r)}</span>
                  {r.with_person_name && <span>· with {r.with_person_name}</span>}
                  {r.gcal_account ? (
                    <span className="inline-flex items-center gap-1">
                      <a href={r.gcal_html_link ?? "#"} target="_blank" rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-emerald-400 hover:underline">
                        <CalendarCheck className="h-3 w-3" /> {
                          calendars.find((o) => o.account === r.gcal_account
                            && o.calendar_id === (r.gcal_calendar_id ?? null))?.label ?? r.gcal_account}
                      </a>
                      <button type="button" onClick={() => removeCal(r)} disabled={busy === r.id}
                        className="text-muted-foreground/70 hover:text-foreground">✕</button>
                    </span>
                  ) : calendars.length > 0 ? (
                    <select defaultValue="" disabled={busy === r.id} onChange={(e) => addCal(r, e.target.value)}
                      className="h-6 text-[11px] px-1 rounded border border-border bg-secondary/40 text-muted-foreground">
                      <option value="">+ Add to calendar</option>
                      {calendars.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  ) : null}
                  {r.participants.length > 0 && (() => {
                    const invitable = r.participants.filter((p) => p.email && !p.sensitive).length;
                    const title = r.participants.map((p) =>
                      p.display_name + (p.sensitive ? " (sensitive — not invited)" : !p.email ? " (no email — not invited)" : "")).join(", ");
                    return (
                      <span className="inline-flex items-center gap-1" title={title}>
                        <Users className="h-3 w-3" />
                        {r.gcal_account
                          ? `${invitable}/${r.participants.length} invited`
                          : `${r.participants.length} participant${r.participants.length > 1 ? "s" : ""}`}
                        {r.gcal_account && invitable < r.participants.length && (
                          <span className="text-amber-400"> · some not invitable</span>
                        )}
                      </span>
                    );
                  })()}
                </div>
              </div>
              <button type="button" onClick={() => startEdit(r)} disabled={busy === r.id} title="Edit routine"
                className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors shrink-0">
                <Pencil className="h-3.5 w-3.5" />
              </button>
              <button type="button" onClick={() => toggle(r)} disabled={busy === r.id}
                title={r.active ? "Pause — stop generating new tasks" : "Resume"}
                className={cn("grid place-items-center h-7 w-7 rounded hover:bg-accent transition-colors shrink-0",
                  r.active ? "text-emerald-400" : "text-muted-foreground")}>
                <Power className="h-3.5 w-3.5" />
              </button>
              <button type="button" onClick={() => del(r)} disabled={busy === r.id} title="Delete routine"
                className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition-colors shrink-0">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
