"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowLeft, ArrowUp, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, type StageConfig } from "@/lib/api";
import { invalidateStages, paletteFor, STAGE_COLOR_NAMES } from "@/lib/stages";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

/** Self-serve deal-stage editor: rename inline, recolor, toggle flags,
 *  reorder with arrows, add and delete (guarded server-side). */
export function StagesManager({ initial }: { initial: StageConfig[] }) {
  const router = useRouter();
  const [stages, setStages] = useState<StageConfig[]>(initial);
  const [newLabel, setNewLabel] = useState("");
  const [newColor, setNewColor] = useState("teal");
  const [busy, setBusy] = useState(false);

  function applied(next: StageConfig[]) {
    setStages(next);
    invalidateStages();
    router.refresh();
  }

  async function mutate(fn: () => Promise<void>) {
    setBusy(true);
    try { await fn(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
    finally { setBusy(false); }
  }

  const rename = (s: StageConfig, label: string) => {
    if (!label.trim() || label === s.label) return;
    void mutate(async () => {
      const row = await api.patchStage(s.key, { label: label.trim() });
      applied(stages.map((x) => (x.key === s.key ? row : x)));
    });
  };
  const recolor = (s: StageConfig, color: string) =>
    void mutate(async () => {
      const row = await api.patchStage(s.key, { color });
      applied(stages.map((x) => (x.key === s.key ? row : x)));
    });
  const toggle = (s: StageConfig, field: "terminal" | "closes") =>
    void mutate(async () => {
      const row = await api.patchStage(s.key, { [field]: !s[field] });
      applied(stages.map((x) => (x.key === s.key ? row : x)));
    });
  const swap = (i: number, j: number) => {
    if (j < 0 || j >= stages.length) return;
    const keys = stages.map((s) => s.key);
    [keys[i], keys[j]] = [keys[j], keys[i]];
    void mutate(async () => applied(await api.reorderStages(keys)));
  };
  const remove = (s: StageConfig) => {
    if (!confirm(`Delete stage "${s.label}"?`)) return;
    void mutate(async () => {
      await api.deleteStage(s.key);
      applied(stages.filter((x) => x.key !== s.key));
      toast.success("Stage deleted");
    });
  };
  const add = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newLabel.trim()) return;
    void mutate(async () => {
      const row = await api.createStage({ label: newLabel.trim(), color: newColor });
      // new stages land before the terminal ones — mirror the server's sort
      applied([...stages.filter((s) => !s.terminal), row, ...stages.filter((s) => s.terminal)]);
      setNewLabel("");
      toast.success(`Stage "${row.label}" added`);
    });
  };

  return (
    <div className="space-y-4 max-w-2xl">
      <Link href="/opportunities" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" /> Back to opportunities
      </Link>

      <ul className="divide-y divide-border rounded-md border border-border bg-card/40">
        {stages.map((s, i) => {
          const pal = paletteFor(s.color);
          return (
            <li key={s.key} className="flex items-center gap-2 px-3 py-2">
              <div className="flex flex-col">
                <button disabled={busy || i === 0} onClick={() => swap(i, i - 1)}
                  className="text-muted-foreground hover:text-foreground disabled:opacity-25" aria-label="Move up">
                  <ArrowUp className="h-3.5 w-3.5" />
                </button>
                <button disabled={busy || i === stages.length - 1} onClick={() => swap(i, i + 1)}
                  className="text-muted-foreground hover:text-foreground disabled:opacity-25" aria-label="Move down">
                  <ArrowDown className="h-3.5 w-3.5" />
                </button>
              </div>

              <span className={cn("inline-flex items-center text-[10px] font-medium px-1.5 py-0.5 rounded border shrink-0", pal.badge)}>
                {s.label}
              </span>

              <Input defaultValue={s.label} disabled={busy}
                onBlur={(e) => rename(s, e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                className="h-7 text-sm flex-1 min-w-[8rem]" />

              <select value={s.color} disabled={busy} onChange={(e) => recolor(s, e.target.value)}
                title="Color"
                className="h-7 text-xs px-1 rounded border border-border bg-secondary/40">
                {STAGE_COLOR_NAMES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>

              <label className="flex items-center gap-1 text-[11px] text-muted-foreground" title="Entering this stage marks the deal closed (won or lost)">
                <input type="checkbox" checked={s.closes} disabled={busy} onChange={() => toggle(s, "closes")} /> closes
              </label>
              <label className="flex items-center gap-1 text-[11px] text-muted-foreground" title="Lost-like: muted column, excluded from pipeline totals">
                <input type="checkbox" checked={s.terminal} disabled={busy} onChange={() => toggle(s, "terminal")} /> lost-like
              </label>

              <span className="text-[11px] text-muted-foreground tabular w-14 text-right shrink-0">
                {s.in_use} deal{s.in_use === 1 ? "" : "s"}
              </span>

              <button disabled={busy || s.in_use > 0} onClick={() => remove(s)}
                title={s.in_use > 0 ? "Move its deals first" : "Delete stage"}
                className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition-colors disabled:opacity-25 shrink-0">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          );
        })}
      </ul>

      <form onSubmit={add} className="flex items-center gap-2">
        <Input value={newLabel} onChange={(e) => setNewLabel(e.target.value)}
          placeholder="New stage name…" className="h-8 text-sm flex-1" disabled={busy} />
        <select value={newColor} onChange={(e) => setNewColor(e.target.value)} disabled={busy}
          className="h-8 text-xs px-1.5 rounded border border-border bg-secondary/40">
          {STAGE_COLOR_NAMES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <Button type="submit" size="sm" disabled={busy || !newLabel.trim()}>
          <Plus className="h-3.5 w-3.5 mr-1" /> Add stage
        </Button>
      </form>

      <p className="text-xs text-muted-foreground">
        Stage <em>keys</em> are fixed at creation (renames are display-only), so history and
        integrations stay stable. A stage with deals can&apos;t be deleted — move them first.
      </p>
    </div>
  );
}
