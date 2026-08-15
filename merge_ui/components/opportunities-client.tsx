"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { api, type OpportunityRow, type OpportunityStage, type ProjectRow } from "@/lib/api";
import { ProjectBadge } from "@/components/project-badge";
import { OpportunityDetailPanel } from "@/components/opportunity-detail-panel";
import { OpportunitiesBoard } from "@/components/opportunities-board";
import { useStages, stageLabel, isTerminal } from "@/lib/stages";
import { Building2, Settings2 } from "lucide-react";
import { formatRelativeDate, cn } from "@/lib/utils";
import { toast } from "sonner";

function awardLabel(o: OpportunityRow): string | null {
  if (o.award_usd != null) {
    const n = o.award_usd;
    const s = n >= 1000 ? `$${(n / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })}k` : `$${n}`;
    return o.award_note ? `${s} · ${o.award_note}` : s;
  }
  return o.award_note || o.estimated_value || null;
}

// Stage change from the list goes through the history-logging endpoint
// (records the transition); use the slide-over to add a next-step note.
function StageSelect({ opp, onChanged }: { opp: OpportunityRow; onChanged: () => void }) {
  const stages = useStages();
  const [busy, setBusy] = useState(false);
  async function onChange(e: React.ChangeEvent<HTMLSelectElement>) {
    setBusy(true);
    try {
      await api.changeOppStage(opp.id, { stage: e.target.value as OpportunityStage });
      onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Update failed");
    } finally { setBusy(false); }
  }
  return (
    <select value={opp.stage} onChange={onChange} disabled={busy}
      onClick={(e) => e.stopPropagation()}
      className="h-7 text-xs px-1.5 rounded border border-border bg-secondary/40">
      {stages.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
    </select>
  );
}

export function OpportunitiesClient({
  initialOpps, projects, currentStage, view = "board",
}: {
  initialOpps: OpportunityRow[];
  projects: ProjectRow[];
  currentStage: string | undefined;
  view?: "board" | "list";
}) {
  const router = useRouter();
  const stages = useStages();
  const [createOpen, setCreateOpen] = useState(false);
  const [openOppId, setOpenOppId] = useState<string | null>(null);
  const [companyFilter, setCompanyFilter] = useState<string>("");
  const [tagFilter, setTagFilter] = useState<string[]>([]);
  const [, startTransition] = useTransition();
  const refresh = () => startTransition(() => router.refresh());

  // Companies present among the loaded deals — powers the filter dropdown.
  const companies = Array.from(
    new Map(
      initialOpps
        .filter((o) => o.company_id && o.company_name)
        .map((o) => [o.company_id as string, o.company_name as string]),
    ),
  ).sort((a, b) => a[1].localeCompare(b[1]));
  // Tag vocabulary present in the loaded deals — the stream filter (job hunt /
  // consulting / …). Applies to BOTH views so the board narrows to one stream too.
  const allTags = Array.from(new Set(initialOpps.flatMap((o) => o.tags ?? []))).sort();
  const byTag = (o: OpportunityRow) =>
    tagFilter.length === 0 || (o.tags ?? []).some((t) => tagFilter.includes(t));

  // List view: filter by the stage chip (client-side; the page now loads all)
  // + the company dropdown. Board view shows every stage as a column.
  let visibleOpps = initialOpps.filter(byTag);
  if (currentStage) visibleOpps = visibleOpps.filter((o) => o.stage === currentStage);
  if (companyFilter) visibleOpps = visibleOpps.filter((o) => o.company_id === companyFilter);

  async function onDelete(o: OpportunityRow) {
    if (!confirm(`Delete opportunity "${o.title}"?`)) return;
    try {
      await api.deleteOpportunity(o.id);
      toast.success("Deleted");
      refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }

  return (
    <>
      {/* View toggle + New opportunity */}
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
          {(["board", "list"] as const).map((v) => (
            <Link key={v} href={`/opportunities?view=${v}`}
              className={cn("px-3 py-1 capitalize", view === v ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
              {v}
            </Link>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <Link href="/opportunities/stages" title="Manage stages"
            className="grid place-items-center h-8 w-8 rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-accent/40 transition-colors">
            <Settings2 className="h-3.5 w-3.5" />
          </Link>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3.5 w-3.5 mr-1" />
            New opportunity
          </Button>
        </div>
      </div>

      {/* Stream filter — one board can hold unrelated pipelines (BD, job hunt,
          a specific venture); tags narrow it to one. Applies to both views. */}
      {allTags.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted-foreground mr-0.5">Streams:</span>
          <button type="button" onClick={() => setTagFilter([])}
            className={cn("inline-flex items-center h-7 px-2.5 rounded-full border",
              tagFilter.length === 0
                ? "border-primary text-foreground bg-accent/40"
                : "border-border text-muted-foreground hover:text-foreground")}>
            All
          </button>
          {allTags.map((t) => {
            const on = tagFilter.includes(t);
            return (
              <button key={t} type="button"
                onClick={() => setTagFilter((cur) => on ? cur.filter((x) => x !== t) : [...cur, t])}
                className={cn("inline-flex items-center h-7 px-2.5 rounded-full border",
                  on ? "border-primary text-foreground bg-accent/40"
                     : "border-border text-muted-foreground hover:text-foreground")}>
                {t}
              </button>
            );
          })}
          {tagFilter.length > 0 && (
            <span className="text-muted-foreground ml-1">
              {visibleOpps.length} shown
            </span>
          )}
        </div>
      )}

      {view === "board" ? (
        <OpportunitiesBoard initialOpps={initialOpps.filter(byTag)} projects={projects} />
      ) : (
      <>
      {/* Stage filter chips */}
      <div className="mb-3 flex flex-wrap gap-2 text-xs items-center">
        <Link href={`/opportunities?view=list`}
          className={cn("inline-flex items-center h-7 px-2.5 rounded-md border",
            !currentStage ? "border-primary text-foreground bg-accent/40" : "border-border text-muted-foreground hover:text-foreground")}>
          All
        </Link>
        {stages.map((s) => (
          <Link key={s.key} href={`/opportunities?view=list&stage=${s.key}`}
            className={cn("inline-flex items-center h-7 px-2.5 rounded-md border",
              currentStage === s.key ? "border-primary text-foreground bg-accent/40" : "border-border text-muted-foreground hover:text-foreground")}>
            {s.label}
          </Link>
        ))}
        {companies.length > 0 && (
          <select
            value={companyFilter}
            onChange={(e) => setCompanyFilter(e.target.value)}
            title="Filter by company"
            className="h-7 text-xs px-1.5 rounded-md border border-border bg-secondary/40 text-muted-foreground">
            <option value="">All companies</option>
            {companies.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        )}
      </div>

      {visibleOpps.length === 0 ? (
        <div className="rounded-md border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
          No opportunities match. Create one to track a deal or potential partnership.
        </div>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border overflow-hidden bg-card/40">
          {visibleOpps.map((o) => {
            const award = awardLabel(o);
            return (
            <li key={o.id} className="flex items-center gap-3 px-3 sm:px-4 py-2.5 hover:bg-accent/20 transition-colors">
              <StageSelect opp={o} onChanged={refresh} />
              <button type="button" onClick={() => setOpenOppId(o.id)} className="min-w-0 flex-1 text-left">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className={cn("font-medium truncate", isTerminal(stages, o.stage) && "line-through text-muted-foreground")}>
                    {o.title}
                  </span>
                  <ProjectBadge slug={o.project_slug} name={o.project_name} color={o.project_color} />
                </div>
                <div className="text-xs text-muted-foreground tabular flex flex-wrap items-center gap-x-2.5 gap-y-0.5 mt-0.5">
                  {o.counterparty_name && (
                    <span className="text-sky-400">{o.counterparty_name}</span>
                  )}
                  {(o.company_name || o.company) && <span className="inline-flex items-center gap-1"><Building2 className="h-3 w-3" />{o.company_name || o.company}</span>}
                  {award && <span className="text-foreground">{award}</span>}
                  {o.responsible_person_id && o.responsible_name && <span>resp: {o.responsible_name}</span>}
                  <span>updated {formatRelativeDate(o.updated_at)}</span>
                </div>
              </button>
              <button type="button" onClick={() => onDelete(o)}
                title="Delete opportunity"
                className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition-colors shrink-0">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
            );
          })}
        </ul>
      )}
      </>
      )}

      <CreateOpportunityDialog
        open={createOpen} onOpenChange={setCreateOpen}
        projects={projects}
      />

      <OpportunityDetailPanel
        oppId={openOppId}
        onClose={() => setOpenOppId(null)}
        onChanged={refresh}
        projects={projects}
      />
    </>
  );
}

export function CreateOpportunityDialog({
  open, onOpenChange, projects, defaults,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  projects: ProjectRow[];
  defaults?: Partial<{
    project_id: string;
    counterparty_id: string;
    /** Counterparty label, shown so it's obvious who the deal is with. */
    counterparty_name: string;
    /** Pre-linked company: id keeps the real record, name pre-fills the input. */
    company_id: string;
    company: string;
  }>;
}) {
  const router = useRouter();
  const stages = useStages();
  const liveStages = stages.filter((s) => !s.terminal);
  const [title, setTitle] = useState("");
  const [project_id, setProjectId] = useState(defaults?.project_id ?? "");
  const [stage, setStage] = useState<OpportunityStage>("");
  const [company, setCompany] = useState(defaults?.company ?? "");
  const [award_usd, setAwardUsd] = useState("");
  const [award_note, setAwardNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    const usd = award_usd.trim() ? Number(award_usd) : undefined;
    setBusy(true);
    try {
      await api.createOpportunity({
        title: title.trim(),
        project_id: project_id || undefined,
        counterparty_id: defaults?.counterparty_id || undefined,
        company: company.trim() || undefined,
        // Keep the real company link when we opened from a contact whose
        // company is known — free-text alone would lose the association.
        company_id: defaults?.company_id && company.trim() === (defaults?.company ?? "")
          ? defaults.company_id : undefined,
        stage: stage || liveStages[0]?.key || "intro",
        award_usd: usd != null && !Number.isNaN(usd) ? usd : undefined,
        award_note: award_note.trim() || undefined,
      });
      toast.success("Opportunity created");
      setTitle(""); setProjectId(""); setStage(""); setCompany(""); setAwardUsd(""); setAwardNote("");
      onOpenChange(false);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Create failed");
    } finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New opportunity</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          {/* Opened from a contact page: show who this deal is with, so the
              pre-filled counterparty isn't invisible state. */}
          {defaults?.counterparty_name && (
            <p className="text-xs text-muted-foreground">
              With <span className="text-sky-400">{defaults.counterparty_name}</span>
              {defaults.company && <> · <span className="text-foreground">{defaults.company}</span></>}
            </p>
          )}
          <div>
            <Label htmlFor="o_title">Title</Label>
            <Input id="o_title" value={title} autoFocus onChange={(e) => setTitle(e.target.value)} placeholder="Sponsor swap with ADI for Token2049" />
          </div>
          <div>
            <Label htmlFor="o_project">Project</Label>
            <select id="o_project" value={project_id} onChange={(e) => setProjectId(e.target.value)}
              className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
              <option value="">(none)</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          <div>
            <Label htmlFor="o_stage">Stage</Label>
            <select id="o_stage" value={stage || liveStages[0]?.key || ""} onChange={(e) => setStage(e.target.value)}
              className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
              {stages.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </div>
          <div>
            <Label htmlFor="o_company">Company (optional)</Label>
            <Input id="o_company" value={company} onChange={(e) => setCompany(e.target.value)} placeholder="Acme Corp" />
          </div>
          <div>
            <Label htmlFor="o_award_usd">Award (optional)</Label>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">$</span>
              <Input id="o_award_usd" type="number" inputMode="numeric" min="0" step="1000"
                value={award_usd} onChange={(e) => setAwardUsd(e.target.value)}
                placeholder="14000" className="w-32" />
              <Input id="o_award_note" value={award_note} onChange={(e) => setAwardNote(e.target.value)}
                placeholder="or outcome (e.g. MoU signed)" className="flex-1" />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
            <Button type="submit" disabled={busy || !title.trim()}>
              {busy ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
