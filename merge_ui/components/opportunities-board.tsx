"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Building2, User, CheckSquare } from "lucide-react";
import { api, type OpportunityRow, type OpportunityStage, type ProjectRow } from "@/lib/api";
import { OpportunityDetailPanel } from "@/components/opportunity-detail-panel";
import { StageChangeDialog } from "@/components/stage-change-dialog";
import { useStages, paletteFor } from "@/lib/stages";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

function fmtUsd(n: number): string {
  if (!n) return "";
  if (n >= 1_000_000) return `$${(n / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 1 })}M`;
  if (n >= 1000) return `$${(n / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })}k`;
  return `$${n}`;
}
function awardLabel(o: OpportunityRow): string | null {
  if (o.award_usd != null) {
    const s = fmtUsd(o.award_usd);
    return o.award_note ? `${s} · ${o.award_note}` : s;
  }
  return o.award_note || o.estimated_value || null;
}

export function OpportunitiesBoard({
  initialOpps, projects,
}: {
  initialOpps: OpportunityRow[];
  projects: ProjectRow[];
}) {
  const router = useRouter();
  const stages = useStages();
  const live = stages.filter((s) => !s.terminal);
  const terminal = stages.filter((s) => s.terminal);
  const [openOppId, setOpenOppId] = useState<string | null>(null);
  const [dragOverStage, setDragOverStage] = useState<OpportunityStage | null>(null);
  // pending drag→drop move awaiting the next-step dialog
  const [move, setMove] = useState<{ id: string; title: string; to: OpportunityStage } | null>(null);
  const [, startTransition] = useTransition();
  const refresh = () => startTransition(() => router.refresh());

  const byStage = (s: OpportunityStage) => initialOpps.filter((o) => o.stage === s);
  const terminalKeys = new Set(terminal.map((s) => s.key));
  const accentFor = (key: string) =>
    terminalKeys.has(key) ? "border-border" : paletteFor(stages.find((s) => s.key === key)?.color ?? "slate").accent;

  function onDrop(e: React.DragEvent, to: OpportunityStage) {
    e.preventDefault();
    setDragOverStage(null);
    const id = e.dataTransfer.getData("text/opp");
    const opp = initialOpps.find((o) => o.id === id);
    if (!opp || opp.stage === to) return;
    setMove({ id, title: opp.title, to });
  }

  async function confirmMove(next_step?: string, note?: string) {
    if (!move) return;
    try {
      await api.changeOppStage(move.id, { stage: move.to, next_step, note });
      toast.success(`Moved to ${stages.find((s) => s.key === move.to)?.label ?? move.to}`);
      setMove(null);
      refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Move failed");
    }
  }

  return (
    <>
      {/* Desktop (lg+): columns flex to fill the width — no scroll. Below lg:
          readable fixed-width columns that swipe horizontally. */}
      <div className="flex items-start gap-2 pb-2 overflow-x-auto lg:overflow-x-visible">
        {/* Live columns from the config table; terminal (lost-like) columns
            render muted at the end — always present as drop targets. */}
        {[...live, ...terminal].map((sc) => {
          const items = byStage(sc.key);
          const total = items.reduce((a, o) => a + (o.award_usd ?? 0), 0);
          const pal = paletteFor(sc.color);
          return (
            <div
              key={sc.key}
              onDragOver={(e) => { e.preventDefault(); setDragOverStage(sc.key); }}
              onDragLeave={() => setDragOverStage((s) => (s === sc.key ? null : s))}
              onDrop={(e) => onDrop(e, sc.key)}
              className={cn(
                "w-[15rem] shrink-0 lg:w-auto lg:flex-1 lg:min-w-0 rounded-lg border bg-card/30 p-2",
                dragOverStage === sc.key ? "border-primary bg-accent/20" : "border-border",
              )}
            >
              <div className={cn("flex items-baseline justify-between gap-1.5 px-1 pb-2 mb-1 border-b-2",
                sc.terminal ? "border-border" : pal.accent)}>
                <span className={cn("min-w-0 truncate text-xs font-semibold uppercase tracking-wide",
                  sc.terminal ? "text-muted-foreground" : pal.text)}>{sc.label}</span>
                <span className="shrink-0 whitespace-nowrap text-[11px] text-muted-foreground tabular">
                  {items.length}{!sc.terminal && total > 0 && <> · <span className="text-foreground">{fmtUsd(total)}</span></>}
                </span>
              </div>
              <div className="space-y-2 min-h-[2rem]">
                {items.map((o) => (
                  <Card key={o.id} o={o} accent={accentFor(o.stage)}
                    muted={terminalKeys.has(o.stage)} onOpen={() => setOpenOppId(o.id)} />
                ))}
              </div>
            </div>
          );
        })}
      </div>

      <OpportunityDetailPanel oppId={openOppId} onClose={() => setOpenOppId(null)} onChanged={refresh} projects={projects} />
      <StageChangeDialog stage={move?.to ?? null} title={move?.title} onCancel={() => setMove(null)} onConfirm={confirmMove} />
    </>
  );
}

function Card({ o, accent, muted, onOpen }: {
  o: OpportunityRow; accent: string; muted: boolean; onOpen: () => void;
}) {
  const award = awardLabel(o);
  return (
    <button
      type="button"
      draggable
      onDragStart={(e) => { e.dataTransfer.setData("text/opp", o.id); e.dataTransfer.effectAllowed = "move"; }}
      onClick={onOpen}
      className={cn(
        "w-full text-left rounded-md border border-l-2 bg-background p-2 hover:bg-accent/30 transition-colors cursor-grab active:cursor-grabbing",
        accent,
      )}
    >
      <div className={cn("text-sm font-medium leading-snug", muted && "line-through text-muted-foreground")}>
        {o.title}
      </div>
      {(o.tags?.length ?? 0) > 0 && (
        <div className="flex flex-wrap gap-1 mt-1">
          {o.tags.map((t) => (
            <span key={t} className="text-[9px] uppercase tracking-wide px-1.5 py-px rounded-full border border-border/70 text-muted-foreground">
              {t}
            </span>
          ))}
        </div>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
        {o.counterparty_name && <span className="text-sky-400">{o.counterparty_name}</span>}
        {(o.company_name || o.company) && (
          <span className="inline-flex items-center gap-0.5"><Building2 className="h-3 w-3" />{o.company_name || o.company}</span>
        )}
        {award && <span className="text-foreground">{award}</span>}
        {o.responsible_name && <span className="inline-flex items-center gap-0.5"><User className="h-3 w-3" />{o.responsible_name}</span>}
        {o.open_task_count > 0 && (
          <span className="inline-flex items-center gap-0.5"><CheckSquare className="h-3 w-3" />{o.open_task_count}</span>
        )}
      </div>
    </button>
  );
}
