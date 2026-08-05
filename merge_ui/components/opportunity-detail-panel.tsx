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
  // stage-change dialog
  const [pendingStage, setPendingStage] = useState<OpportunityStage | null>(null);
  // add-note form
  const [evNext, setEvNext] = useState("");
  const [evNote, setEvNote] = useState("");
  // add-task
  const [newTask, setNewTask] = useState("");

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
      });
      setNewTask("");
      await reload(); onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Add task failed");
    }
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
                <CompanyPicker onPick={(c) => patch({ company_id: c.id })}
                  trigger={<span className="text-[11px] text-muted-foreground hover:text-foreground">change</span>} />
                {opp.company_id && (
                  <button onClick={() => patch({ company_id: null })} className="text-[11px] text-muted-foreground hover:text-foreground">clear</button>
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
                <input
                  value={newTag}
                  onChange={(e) => setNewTag(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter") return;
                    e.preventDefault();
                    const t = newTag.trim().toLowerCase();
                    if (!t) return;
                    if (!(opp.tags ?? []).includes(t)) patch({ tags: [...(opp.tags ?? []), t] });
                    setNewTag("");
                  }}
                  placeholder="+ tag (job, consulting…)"
                  className="h-6 px-2 text-xs rounded-full border border-dashed border-border bg-transparent outline-none focus:border-foreground/40 w-36"
                />
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
                    <span className={cn("flex-1 text-sm", (t.status === "done" || t.status === "cancelled") && "line-through text-muted-foreground")}>
                      {t.title}
                    </span>
                    {t.due_date && <span className="text-[10px] text-muted-foreground tabular">due {t.due_date}</span>}
                  </li>
                ))}
              </ul>
              <div className="flex items-center gap-2 mt-2">
                <Input
                  value={newTask} onChange={(e) => setNewTask(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") addTask(); }}
                  placeholder="Add a task for this deal…" className="h-8 text-sm"
                />
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
