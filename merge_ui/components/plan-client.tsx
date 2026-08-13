"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { CheckSquare, Target, Folder, Plus, GripVertical } from "lucide-react";
import { StageBadge } from "@/components/stage-badge";
import { TaskDetailPanel } from "@/components/task-detail-panel";
import { OpportunityDetailPanel } from "@/components/opportunity-detail-panel";
import {
  api, type TaskRow, type OpportunityRow, type ProjectRow,
} from "@/lib/api";
import { taskTimeStatus, cn } from "@/lib/utils";
import { toast } from "sonner";
import { celebrateCompletion } from "@/lib/celebrate";

const TIME_TONE: Record<string, string> = {
  danger: "text-rose-400 border-rose-500/40",
  warn: "text-amber-400 border-amber-500/40",
  muted: "text-muted-foreground border-border",
};

function awardLabel(o: OpportunityRow): string | null {
  if (o.award_usd != null) {
    const n = o.award_usd;
    const s = n >= 1000 ? `$${(n / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })}k` : `$${n}`;
    return o.award_note ? `${s} · ${o.award_note}` : s;
  }
  return o.award_note || o.estimated_value || null;
}

type Group<T> = { project: ProjectRow | null; items: T[] };

// Render EVERY project (plus Unassigned) as a group so each is a drop target
// + quick-add slot, even when it currently holds no items.
function taskGroupsAll<T extends { project_id: string | null }>(items: T[], projects: ProjectRow[]): Group<T>[] {
  const buckets = new Map<string, T[]>();
  for (const it of items) {
    const key = it.project_id ?? "__none__";
    (buckets.get(key) ?? buckets.set(key, []).get(key)!).push(it);
  }
  const out: Group<T>[] = projects.map((p) => ({ project: p, items: buckets.get(p.id) ?? [] }));
  out.push({ project: null, items: buckets.get("__none__") ?? [] });
  return out;
}

function ProjectHeader({ project }: { project: ProjectRow | null }) {
  return (
    <div className="flex items-center gap-2 px-1 pt-3 pb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
      {project ? (
        <>
          <span className="h-2 w-2 rounded-full shrink-0" style={{ backgroundColor: project.color ?? "var(--color-muted-foreground)" }} />
          {project.name}
        </>
      ) : (
        <><Folder className="h-3.5 w-3.5" /> Unassigned</>
      )}
    </div>
  );
}

export function PlanClient({
  tasks, opps, projects,
}: {
  tasks: TaskRow[];
  opps: OpportunityRow[];
  projects: ProjectRow[];
}) {
  const router = useRouter();
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [openOppId, setOpenOppId] = useState<string | null>(null);
  const [companyFilter, setCompanyFilter] = useState<string>("");
  // per-group quick-add drafts + drag-over highlight, keyed by a column-prefixed
  // group key ("t:<projid>" tasks, "d:<projid>" deals) so the two columns —
  // which share project ids — never cross-highlight or cross-drop.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [dragOver, setDragOver] = useState<string | null>(null);
  const [, startTransition] = useTransition();
  const refresh = () => startTransition(() => router.refresh());

  async function toggleDone(t: TaskRow, done: boolean) {
    try {
      await api.patchTask(t.id, { status: done ? "done" : "open" });
      if (done) void celebrateCompletion(async () => {
        try { await api.patchTask(t.id, { status: "open" }); refresh(); }
        catch (e) { toast.error(e instanceof Error ? e.message : "Undo failed"); }
      });
      refresh();
    }
    catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
  }

  const groupKey = (p: ProjectRow | null) => p?.id ?? "__none__";

  async function quickAdd(project: ProjectRow | null) {
    const key = `t:${groupKey(project)}`;
    const title = (drafts[key] ?? "").trim();
    if (!title) return;
    try {
      await api.createTask({ title, project_id: project?.id });
      setDrafts((d) => ({ ...d, [key]: "" }));
      refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Add failed"); }
  }

  async function quickAddDeal(project: ProjectRow | null) {
    const key = `d:${groupKey(project)}`;
    const title = (drafts[key] ?? "").trim();
    if (!title) return;
    try {
      await api.createOpportunity({ title, project_id: project?.id, stage: "intro" });
      setDrafts((d) => ({ ...d, [key]: "" }));
      refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Add failed"); }
  }

  // Drop a dragged task onto a project group → reassign its project.
  async function reassign(taskId: string, fromKey: string, project: ProjectRow | null) {
    if (fromKey === groupKey(project)) return;
    try {
      await api.patchTask(taskId, { project_id: project?.id ?? null });
      toast.success(project ? `Moved to ${project.name}` : "Unassigned");
      refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Move failed"); }
  }

  // Drop a dragged deal onto a project group → reassign its project.
  async function reassignDeal(oppId: string, fromKey: string, project: ProjectRow | null) {
    if (fromKey === groupKey(project)) return;
    try {
      await api.patchOpportunity(oppId, { project_id: project?.id ?? null });
      toast.success(project ? `Moved to ${project.name}` : "Unassigned");
      refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Move failed"); }
  }

  // Company filter narrows the deals column (tasks aren't company-scoped).
  const companies = Array.from(
    new Map(
      opps
        .filter((o) => o.company_id && o.company_name)
        .map((o) => [o.company_id as string, o.company_name as string]),
    ),
  ).sort((a, b) => a[1].localeCompare(b[1]));
  const filteredOpps = companyFilter ? opps.filter((o) => o.company_id === companyFilter) : opps;

  const taskGroups = taskGroupsAll(tasks, projects);
  const oppGroups = taskGroupsAll(filteredOpps, projects);
  const QUICK_BTN = "h-7 px-2 rounded-md border border-border text-xs text-muted-foreground hover:bg-accent/30 disabled:opacity-40 shrink-0";

  return (
    <>
      {companies.length > 0 && (
        <div className="mb-3 flex justify-end">
          <select
            value={companyFilter}
            onChange={(e) => setCompanyFilter(e.target.value)}
            title="Filter deals by company"
            className="h-7 text-xs px-1.5 rounded-md border border-border bg-secondary/40 text-muted-foreground">
            <option value="">All companies</option>
            {companies.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Tasks column */}
        <div>
          <h2 className="text-sm font-semibold flex items-center gap-1.5 mb-1">
            <CheckSquare className="h-4 w-4 text-muted-foreground" /> Tasks
            <span className="text-xs font-normal text-muted-foreground">({tasks.length})</span>
          </h2>
          {taskGroups.map((g) => {
            const pk = groupKey(g.project);
            const key = `t:${pk}`;
            return (
              <div key={key}
                onDragOver={(e) => { e.preventDefault(); setDragOver(key); }}
                onDragLeave={() => setDragOver((k) => (k === key ? null : k))}
                onDrop={(e) => {
                  e.preventDefault(); setDragOver(null);
                  const id = e.dataTransfer.getData("text/task");
                  const from = e.dataTransfer.getData("text/task-from");
                  if (id) reassign(id, from, g.project);
                }}
                className={cn("rounded-md transition-colors", dragOver === key && "ring-1 ring-primary bg-accent/10")}>
                <ProjectHeader project={g.project} />
                <ul className="rounded-md border border-border bg-card/40 divide-y divide-border overflow-hidden">
                  {g.items.map((t) => {
                    const ts = taskTimeStatus(t.due_date, t.status);
                    return (
                      <li key={t.id} draggable
                        onDragStart={(e) => {
                          e.dataTransfer.setData("text/task", t.id);
                          e.dataTransfer.setData("text/task-from", pk);
                          e.dataTransfer.effectAllowed = "move";
                        }}
                        className="group flex items-center gap-1.5 px-2 py-2 hover:bg-accent/20 transition-colors cursor-grab active:cursor-grabbing">
                        <GripVertical className="h-3.5 w-3.5 text-muted-foreground/30 group-hover:text-muted-foreground/70 shrink-0" />
                        <input type="checkbox" title="Mark done"
                          checked={t.status === "done"}
                          onChange={(e) => toggleDone(t, e.target.checked)}
                          className="h-3.5 w-3.5 rounded border-border accent-emerald-500 shrink-0" />
                        <button onClick={() => setOpenTaskId(t.id)} className="flex-1 min-w-0 text-left flex items-center gap-2">
                          <span className={cn("flex-1 min-w-0 truncate text-sm",
                            (t.status === "done" || t.status === "cancelled") && "line-through text-muted-foreground")}>{t.title}</span>
                          {t.opportunity_title && (
                            <span title={`Deal: ${t.opportunity_title}`}
                              className="text-[10px] px-1 rounded border border-violet-500/40 text-violet-400 inline-flex items-center gap-0.5 shrink-0 max-w-[8rem] truncate">
                              <Target className="h-2.5 w-2.5 shrink-0" /> {t.opportunity_title}
                            </span>
                          )}
                          {ts && <span className={cn("text-[10px] font-mono px-1 rounded border shrink-0", TIME_TONE[ts.tone])}>{ts.label}</span>}
                        </button>
                      </li>
                    );
                  })}
                  <li className="flex items-center gap-1.5 px-2 py-1.5">
                    <Plus className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                    <input
                      value={drafts[key] ?? ""}
                      onChange={(e) => setDrafts((d) => ({ ...d, [key]: e.target.value }))}
                      onKeyDown={(e) => { if (e.key === "Enter") quickAdd(g.project); }}
                      placeholder={`Add a task${g.project ? ` to ${g.project.name}` : ""}…`}
                      className="flex-1 min-w-0 h-7 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60" />
                    <button type="button" onClick={() => quickAdd(g.project)} disabled={!(drafts[key] ?? "").trim()} className={QUICK_BTN}>Add</button>
                  </li>
                </ul>
              </div>
            );
          })}
        </div>

        {/* Opportunities column */}
        <div>
          <h2 className="text-sm font-semibold flex items-center gap-1.5 mb-1">
            <Target className="h-4 w-4 text-muted-foreground" /> Opportunities
            <span className="text-xs font-normal text-muted-foreground">({filteredOpps.length})</span>
          </h2>
          {oppGroups.map((g) => {
            const pk = groupKey(g.project);
            const key = `d:${pk}`;
            return (
              <div key={key}
                onDragOver={(e) => { e.preventDefault(); setDragOver(key); }}
                onDragLeave={() => setDragOver((k) => (k === key ? null : k))}
                onDrop={(e) => {
                  e.preventDefault(); setDragOver(null);
                  const id = e.dataTransfer.getData("text/deal");
                  const from = e.dataTransfer.getData("text/deal-from");
                  if (id) reassignDeal(id, from, g.project);
                }}
                className={cn("rounded-md transition-colors", dragOver === key && "ring-1 ring-primary bg-accent/10")}>
                <ProjectHeader project={g.project} />
                <ul className="rounded-md border border-border bg-card/40 divide-y divide-border overflow-hidden">
                  {g.items.map((o) => {
                    const award = awardLabel(o);
                    return (
                      <li key={o.id} draggable
                        onDragStart={(e) => {
                          e.dataTransfer.setData("text/deal", o.id);
                          e.dataTransfer.setData("text/deal-from", pk);
                          e.dataTransfer.effectAllowed = "move";
                        }}
                        className="group flex items-start gap-1.5 px-2 py-2 hover:bg-accent/20 transition-colors cursor-grab active:cursor-grabbing">
                        <GripVertical className="h-3.5 w-3.5 mt-0.5 text-muted-foreground/30 group-hover:text-muted-foreground/70 shrink-0" />
                        <button onClick={() => setOpenOppId(o.id)} className="flex-1 min-w-0 text-left">
                          <div className="flex items-center gap-2">
                            <StageBadge stage={o.stage} />
                            <span className="flex-1 min-w-0 truncate text-sm">{o.title}</span>
                          </div>
                          <div className="text-xs text-muted-foreground mt-0.5 flex flex-wrap gap-x-2">
                            {o.counterparty_name && <span className="text-sky-400">{o.counterparty_name}</span>}
                            {(o.company_name || o.company) && <span>{o.company_name || o.company}</span>}
                            {award && <span className="text-foreground">{award}</span>}
                            {o.open_task_count > 0 && <span className="inline-flex items-center gap-0.5"><CheckSquare className="h-3 w-3" />{o.open_task_count}</span>}
                          </div>
                        </button>
                      </li>
                    );
                  })}
                  <li className="flex items-center gap-1.5 px-2 py-1.5">
                    <Plus className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                    <input
                      value={drafts[key] ?? ""}
                      onChange={(e) => setDrafts((d) => ({ ...d, [key]: e.target.value }))}
                      onKeyDown={(e) => { if (e.key === "Enter") quickAddDeal(g.project); }}
                      placeholder={`Add a deal${g.project ? ` to ${g.project.name}` : ""}…`}
                      className="flex-1 min-w-0 h-7 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60" />
                    <button type="button" onClick={() => quickAddDeal(g.project)} disabled={!(drafts[key] ?? "").trim()} className={QUICK_BTN}>Add</button>
                  </li>
                </ul>
              </div>
            );
          })}
        </div>
      </div>

      <TaskDetailPanel taskId={openTaskId} onClose={() => setOpenTaskId(null)} onChanged={refresh} projects={projects} />
      <OpportunityDetailPanel oppId={openOppId} onClose={() => setOpenOppId(null)} onChanged={refresh} projects={projects} />
    </>
  );
}
