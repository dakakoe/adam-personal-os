"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  X, User, Building2, Loader2, ArrowRight, History, Plus, CheckSquare, Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StageChangeDialog } from "@/components/stage-change-dialog";
import { PersonPicker } from "@/components/person-picker";
import { CompanyPicker } from "@/components/company-picker";
import { ProjectBadge } from "@/components/project-badge";
import { StageBadge } from "@/components/stage-badge";
import { useStages } from "@/lib/stages";
import {
  api, type OpportunityDetail, type OpportunityStage, type ProjectRow,
} from "@/lib/api";
import { formatRelativeDate, cn } from "@/lib/utils";
import { toast } from "sonner";

export function OpportunityDetailPanel({
  oppId, onClose, onChanged, projects = [],
}: {
  oppId: string | null;
  onClose: () => void;
  onChanged: () => void;
  projects?: ProjectRow[];
}) {
  const stages = useStages();
  const [opp, setOpp] = useState<OpportunityDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [awardUsd, setAwardUsd] = useState("");
  const [awardNote, setAwardNote] = useState("");
  const [newTag, setNewTag] = useState("");
  // Tag vocabulary already in use across all deals — powers autocomplete so the
  // same stream doesn't end up as 'job', 'Jobs', 'job-hunt'.
  const [knownTags, setKnownTags] = useState<string[]>([]);
  const [tagOpen, setTagOpen] = useState(false);
  const [tagHi, setTagHi] = useState(0);
  // stage-change dialog
  const [pendingStage, setPendingStage] = useState<OpportunityStage | null>(null);
  // add-note form
  const [evNext, setEvNext] = useState("");
  const [evNote, setEvNote] = useState("");
  // add-task
  const [newTask, setNewTask] = useState("");
  const [newTaskDate, setNewTaskDate] = useState("");
  const [newTaskTime, setNewTaskTime] = useState("");

  const open = oppId !== null;

  const reload = useCallback(async () => {
    if (!oppId) return;
    const d = await api.getOpportunity(oppId);
    setOpp(d);
    setTitle(d.title);
    setDesc(d.description ?? "");
    setAwardUsd(d.award_usd != null ? String(d.award_usd) : "");
    setAwardNote(d.award_note ?? "");
  }, [oppId]);

  useEffect(() => {
    if (!oppId) return;
    api.listOpportunityTags().then(setKnownTags).catch(() => { /* free text still works */ });
  }, [oppId]);

  useEffect(() => {
    if (!oppId) { setOpp(null); return; }
    setLoading(true);
    reload().catch((e) => toast.error(e instanceof Error ? e.message : "Load failed")).finally(() => setLoading(false));
  }, [oppId, reload]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    if (open) document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  async function patch(fields: Record<string, unknown>) {
    if (!oppId) return;
    try { await api.patchOpportunity(oppId, fields); await reload(); onChanged(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
  }

  async function confirmStage(next_step?: string, note?: string) {
    if (!oppId || !pendingStage) return;
    try {
      await api.changeOppStage(oppId, { stage: pendingStage, next_step, note });
      toast.success(`Moved to ${pendingStage}`);
      setPendingStage(null);
      await reload(); onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Stage change failed");
    }
  }

  async function logEvent() {
    if (!oppId || (!evNext.trim() && !evNote.trim())) return;
    try {
      await api.addOppEvent(oppId, { next_step: evNext.trim() || undefined, note: evNote.trim() || undefined });
      setEvNext(""); setEvNote("");
      await reload(); onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Log failed");
    }
  }

  async function addTask() {
    const t = newTask.trim();
    if (!oppId || !opp || !t) return;
    try {
      await api.createTask({
        title: t,
        opportunity_id: oppId,
        project_id: opp.project_id ?? undefined,
        with_person_id: opp.counterparty_id ?? undefined,
        due_date: newTaskDate || undefined,
        // A time is only meaningful with a date (and is what makes the task
        // syncable to a calendar slot rather than an all-day reminder).
        due_time: (newTaskDate && newTaskTime) ? newTaskTime : undefined,
      });
      setNewTask(""); setNewTaskDate(""); setNewTaskTime("");
      await reload(); onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Add task failed");
    }
  }
  async function patchTask(id: string, fields: Record<string, unknown>) {
    try { await api.patchTask(id, fields); await reload(); onChanged(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
  }
  async function toggleTask(id: string, done: boolean) {
    try { await api.patchTask(id, { status: done ? "done" : "open" }); await reload(); onChanged(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
  }
  async function removeOpp() {
    if (!oppId || !opp) return;
    if (!confirm(`Delete opportunity "${opp.title}"? This can't be undone.`)) return;
    try {
      await api.deleteOpportunity(oppId);
      toast.success("Opportunity deleted");
      onClose(); onChanged();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }

  return (
    <>
      <div
        className={cn(
          "fixed inset-0 z-40 bg-background/70 backdrop-blur-sm transition-opacity",
          open ? "opacity-100" : "opacity-0 pointer-events-none",
        )}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        className={cn(
          "fixed inset-y-0 right-0 z-50 w-full sm:w-[31rem] bg-card border-l border-border shadow-2xl",
          "transition-transform duration-200 overflow-y-auto",
          open ? "translate-x-0" : "translate-x-full",
        )}
        role="dialog" aria-label="Opportunity detail"
      >
        {!opp ? (
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
                    value={opp.stage}
                    onChange={(e) => setPendingStage(e.target.value as OpportunityStage)}
                    className="h-7 text-xs px-1.5 rounded border border-border bg-secondary/40"
                  >
                    {stages.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                  </select>
                  <ProjectBadge slug={opp.project_slug} name={opp.project_name} color={opp.project_color} />
                  {opp.closed_at && <span className="text-[10px] font-mono text-muted-foreground">closed</span>}
                </div>
                <textarea
                  value={title} onChange={(e) => setTitle(e.target.value)}
                  onBlur={() => { const t = title.trim(); if (t && t !== opp.title) patch({ title: t }); }}
                  rows={2}
                  className="w-full bg-transparent text-lg font-semibold leading-snug outline-none resize-none focus:bg-accent/20 rounded px-1 -mx-1"
                />
              </div>
              <button onClick={onClose} className="grid place-items-center h-8 w-8 rounded-md hover:bg-accent text-muted-foreground shrink-0" aria-label="Close">
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Field grid */}
            <div className="grid grid-cols-[6rem_1fr] gap-y-3 gap-x-3 items-center text-sm">
              <span className="text-xs text-muted-foreground">Project</span>
              <select
                value={opp.project_id ?? ""}
                onChange={(e) => patch({ project_id: e.target.value || null })}
                className="h-8 px-2 text-sm rounded-md border border-border bg-background w-fit max-w-full"
              >
                <option value="">(none)</option>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>

              <span className="text-xs text-muted-foreground">Contact</span>
              <div className="flex items-center gap-2 flex-wrap">
                {opp.counterparty_id && opp.counterparty_name ? (
                  <Link href={`/persons/${opp.counterparty_id}`} className="text-sky-400 hover:text-sky-300 text-sm">
                    {opp.counterparty_name}
                  </Link>
                ) : (
                  <span className="text-xs text-muted-foreground/70">none</span>
                )}
                <PersonPicker
                  onPick={(p) => patch({ counterparty_id: p.person_id })}
                  trigger={<span className="text-[11px] text-muted-foreground hover:text-foreground">change</span>}
                />
                {opp.counterparty_id && (
                  <button onClick={() => patch({ counterparty_id: null })} className="text-[11px] text-muted-foreground hover:text-foreground">clear</button>
                )}
              </div>

              <span className="text-xs text-muted-foreground">Company</span>
              <div className="flex items-center gap-2 flex-wrap">
                <Building2 className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                {opp.company_id && opp.company_name ? (
                  <Link href={`/companies/${opp.company_id}`} className="text-sky-400 hover:text-sky-300 text-sm">{opp.company_name}</Link>
                ) : opp.company ? (
                  <span className="text-sm text-muted-foreground">{opp.company} <span className="text-[10px]">(unlinked)</span></span>
                ) : (
                  <span className="text-xs text-muted-foreground/70">none</span>
                )}
                {/* Set BOTH halves. An opportunity carries a company_id (the
                    real link) and a free-text `company` (what a capture or an
                    import called it). Writing only the id leaves the text
                    behind to reappear as a ghost the moment the link goes. */}
                <CompanyPicker onPick={(c) => patch({ company_id: c.id, company: c.name })}
                  trigger={<span className="text-[11px] text-muted-foreground hover:text-foreground">change</span>} />
                {/* Offered whenever there's ANYTHING to clear — including a
                    stranded name with no link, which previously had no clear
                    button at all and so could not be removed from the UI. */}
                {(opp.company_id || opp.company) && (
                  <button onClick={() => patch({ company_id: null, company: null })}
                    className="text-[11px] text-muted-foreground hover:text-foreground">clear</button>
                )}
              </div>

              <span className="text-xs text-muted-foreground">Award</span>
              <div className="flex items-center gap-2">
                <div className="flex items-center gap-1">
                  <span className="text-muted-foreground text-sm">$</span>
                  <Input
                    type="number" value={awardUsd} onChange={(e) => setAwardUsd(e.target.value)}
                    onBlur={() => {
                      const v = awardUsd.trim() === "" ? null : Number(awardUsd);
                      if (v !== (opp.award_usd ?? null)) patch({ award_usd: v });
                    }}
                    placeholder="amount" className="h-8 text-sm w-28"
                  />
                </div>
                <Input
                  value={awardNote} onChange={(e) => setAwardNote(e.target.value)}
                  onBlur={() => { if (awardNote !== (opp.award_note ?? "")) patch({ award_note: awardNote.trim() || null }); }}
                  placeholder="or outcome (e.g. MoU signed)" className="h-8 text-sm flex-1"
                />
              </div>

              {/* Streams — free-form tags that separate unrelated pipelines
                  (BD vs job hunt vs a venture) on one shared board. */}
              <span className="text-xs text-muted-foreground">Streams</span>
              <div className="flex items-center gap-1.5 flex-wrap">
                {(opp.tags ?? []).map((t) => (
                  <span key={t} className="inline-flex items-center gap-1 h-6 pl-2 pr-1 rounded-full border border-border text-xs">
                    {t}
                    <button type="button" title={`Remove "${t}"`}
                      onClick={() => patch({ tags: (opp.tags ?? []).filter((x) => x !== t) })}
                      className="inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground">
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
                {(() => {
                  const applied = opp.tags ?? [];
                  const q = newTag.trim().toLowerCase();
                  // Suggest tags in use elsewhere, minus the ones already on
                  // this deal; prefix matches first, then substring.
                  const matches = knownTags
                    .filter((t) => !applied.includes(t) && (!q || t.includes(q)))
                    .sort((a, b) => Number(b.startsWith(q)) - Number(a.startsWith(q)))
                    .slice(0, 8);
                  const add = (t: string) => {
                    const v = t.trim().toLowerCase();
                    if (!v) return;
                    if (!applied.includes(v)) patch({ tags: [...applied, v] });
                    setNewTag(""); setTagOpen(false); setTagHi(0);
                  };
                  return (
                    <span className="relative inline-block">
                      <input
                        value={newTag}
                        onChange={(e) => { setNewTag(e.target.value); setTagOpen(true); setTagHi(0); }}
                        onFocus={() => setTagOpen(true)}
                        // Blur is delayed so a click on a suggestion still registers.
                        onBlur={() => setTimeout(() => setTagOpen(false), 120)}
                        onKeyDown={(e) => {
                          if (e.key === "ArrowDown" && matches.length) {
                            e.preventDefault(); setTagOpen(true);
                            setTagHi((i) => (i + 1) % matches.length); return;
                          }
                          if (e.key === "ArrowUp" && matches.length) {
                            e.preventDefault();
                            setTagHi((i) => (i - 1 + matches.length) % matches.length); return;
                          }
                          if (e.key === "Escape") { setTagOpen(false); return; }
                          if (e.key !== "Enter") return;
                          e.preventDefault();
                          // Enter takes the highlighted suggestion when the list
                          // is open, otherwise whatever was typed (new tags stay possible).
                          add(tagOpen && matches[tagHi] && q ? matches[tagHi] : newTag);
                        }}
                        placeholder="+ tag (job, consulting…)"
                        className="h-6 px-2 text-xs rounded-full border border-dashed border-border bg-transparent outline-none focus:border-foreground/40 w-36"
                      />
                      {tagOpen && matches.length > 0 && (
                        <div className="absolute z-20 left-0 top-7 w-44 rounded-md border border-border bg-popover shadow-lg p-1">
                          {matches.map((t, i) => (
                            <button
                              key={t} type="button"
                              onMouseDown={(e) => { e.preventDefault(); add(t); }}
                              onMouseEnter={() => setTagHi(i)}
                              className={cn("w-full text-left px-2 py-1 rounded text-xs",
                                i === tagHi ? "bg-accent text-foreground" : "hover:bg-accent/60")}
                            >
                              {t}
                            </button>
                          ))}
                        </div>
                      )}
                    </span>
                  );
                })()}
              </div>

              <span className="text-xs text-muted-foreground">Responsible</span>
              <div className="flex items-center gap-2">
                <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border",
                  opp.responsible_person_id ? "border-border" : "border-primary/40 text-primary")}>
                  <User className="h-3 w-3" />{opp.responsible_name ?? "Me"}
                </span>
                <PersonPicker
                  onPick={(p) => patch({ responsible_person_id: p.person_id })}
                  trigger={<span className="text-[11px] text-muted-foreground hover:text-foreground">change</span>}
                />
                {opp.responsible_person_id && (
                  <button onClick={() => patch({ responsible_person_id: null })} className="text-[11px] text-muted-foreground hover:text-foreground">→ Me</button>
                )}
              </div>
            </div>

            {/* Description */}
            <div>
              <div className="text-xs text-muted-foreground mb-1">Description</div>
              <textarea
                value={desc} onChange={(e) => setDesc(e.target.value)}
                onBlur={() => { if (desc !== (opp.description ?? "")) patch({ description: desc || null }); }}
                rows={3} placeholder="Add detail…"
                className="w-full text-sm rounded-md border border-border bg-background px-2 py-1.5 outline-none resize-y"
              />
            </div>

            {/* Linked tasks */}
            <div>
              <div className="text-xs text-muted-foreground mb-2 flex items-center gap-1.5">
                <CheckSquare className="h-3.5 w-3.5" /> Tasks
              </div>
              <ul className="space-y-1">
                {opp.tasks.map((t) => (
                  <li key={t.id} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={t.status === "done"}
                      onChange={(e) => toggleTask(t.id, e.target.checked)}
                      className="h-3.5 w-3.5 rounded border-border accent-emerald-500"
                    />
                    <span className={cn("flex-1 text-sm min-w-0 truncate", (t.status === "done" || t.status === "cancelled") && "line-through text-muted-foreground")}>
                      {t.title}
                    </span>
                    {/* Due date/time edit in place — deal tasks are usually
                        scheduled right after they're captured. */}
                    <input
                      type="date" value={t.due_date ?? ""}
                      onChange={(e) => patchTask(t.id, { due_date: e.target.value || null })}
                      title="Due date"
                      className="h-6 px-1 text-[10px] rounded border border-border bg-transparent text-muted-foreground [color-scheme:dark] shrink-0"
                    />
                    {t.due_date && (
                      <input
                        type="time" value={(t.due_time ?? "").slice(0, 5)}
                        onChange={(e) => patchTask(t.id, { due_time: e.target.value || null })}
                        title="Due time (optional)"
                        className="h-6 px-1 text-[10px] rounded border border-border bg-transparent text-muted-foreground [color-scheme:dark] shrink-0"
                      />
                    )}
                  </li>
                ))}
              </ul>
              <div className="flex items-center gap-2 mt-2">
                <Input
                  value={newTask} onChange={(e) => setNewTask(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") addTask(); }}
                  placeholder="Add a task for this deal…" className="h-8 text-sm"
                />
                <input
                  type="date" value={newTaskDate} onChange={(e) => setNewTaskDate(e.target.value)}
                  title="Due date (optional)"
                  className="h-8 px-1 text-xs rounded-md border border-border bg-background text-muted-foreground [color-scheme:dark] shrink-0"
                />
                {newTaskDate && (
                  <input
                    type="time" value={newTaskTime} onChange={(e) => setNewTaskTime(e.target.value)}
                    title="Due time (optional)"
                    className="h-8 px-1 text-xs rounded-md border border-border bg-background text-muted-foreground [color-scheme:dark] shrink-0"
                  />
                )}
                <Button size="sm" variant="outline" className="h-8 px-2" disabled={!newTask.trim()} onClick={addTask}>
                  <Plus className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>

            {/* Timeline */}
            <div>
              <div className="text-xs text-muted-foreground mb-2 flex items-center gap-1.5">
                <History className="h-3.5 w-3.5" /> Timeline
              </div>
              {/* Add note / next step */}
              <div className="rounded-md border border-border bg-background p-2 mb-3 space-y-2">
                <Input value={evNext} onChange={(e) => setEvNext(e.target.value)} placeholder="Next step…" className="h-8 text-sm" />
                <div className="flex items-center gap-2">
                  <Input value={evNote} onChange={(e) => setEvNote(e.target.value)} placeholder="Note (optional)" className="h-8 text-sm flex-1" />
                  <Button size="sm" variant="outline" className="h-8 px-2" disabled={!evNext.trim() && !evNote.trim()} onClick={logEvent}>
                    <Plus className="h-3.5 w-3.5 mr-1" /> Log
                  </Button>
                </div>
              </div>
              <ol className="space-y-3">
                {opp.events.map((ev) => (
                  <li key={ev.id} className="relative pl-4 border-l border-border">
                    <span className="absolute -left-[5px] top-1 h-2 w-2 rounded-full bg-primary/70" />
                    <div className="flex items-center gap-2 flex-wrap text-xs">
                      {ev.kind === "stage_change" ? (
                        <span className="inline-flex items-center gap-1.5">
                          {ev.from_stage && <StageBadge stage={ev.from_stage} />}
                          {ev.from_stage && <ArrowRight className="h-3 w-3 text-muted-foreground" />}
                          {ev.to_stage && <StageBadge stage={ev.to_stage} />}
                        </span>
                      ) : (
                        <span className="text-[10px] font-mono uppercase text-muted-foreground">note</span>
                      )}
                      <span className="text-muted-foreground/60">{formatRelativeDate(ev.created_at)}</span>
                    </div>
                    {ev.next_step && <div className="text-sm mt-1"><span className="text-muted-foreground">Next:</span> {ev.next_step}</div>}
                    {ev.note && <div className="text-sm text-muted-foreground mt-0.5">{ev.note}</div>}
                  </li>
                ))}
              </ol>
            </div>

            <div className="flex items-center justify-between gap-2 border-t border-border pt-3">
              <span className="text-[11px] text-muted-foreground/60">
                created {formatRelativeDate(opp.created_at)}
                {opp.source_kind && opp.source_kind !== "manual" && <> · {opp.source_kind}</>}
              </span>
              <button onClick={removeOpp}
                className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-destructive">
                <Trash2 className="h-3.5 w-3.5" /> Delete opportunity
              </button>
            </div>
          </div>
        )}
      </aside>

      {/* Stage-change dialog: capture the next step */}
      <StageChangeDialog
        stage={pendingStage}
        title={opp?.title}
        onCancel={() => setPendingStage(null)}
        onConfirm={confirmStage}
      />
    </>
  );
}
