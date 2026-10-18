"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtCompact } from "@/lib/format";
import { cn } from "@/lib/utils";
import { api, type FinBudget, type FinCategory } from "@/lib/api";
import { toast } from "sonner";

type Cur = "USD" | "THB";

export function BudgetsClient({
  initialBudgets, categories, usdThbRate,
}: { initialBudgets: FinBudget[]; categories: FinCategory[]; usdThbRate: number }) {
  const router = useRouter();
  const hasRate = usdThbRate > 0;
  const [cur, setCur] = useState<Cur>(hasRate ? "THB" : "USD");
  const [budgets, setBudgets] = useState<FinBudget[]>(initialBudgets);
  const [addCat, setAddCat] = useState("");
  const [addLimit, setAddLimit] = useState("");
  const [busy, setBusy] = useState(false);

  const conv = (usd: number) => (cur === "THB" ? usd * usdThbRate : usd);
  const toUsd = (v: number) => (cur === "THB" && hasRate ? v / usdThbRate : v);

  const budgetedKeys = new Set(budgets.map((b) => b.category_key));
  const expenseCats = categories.filter((c) => (c.kind === "expense" || c.kind === "both") && !budgetedKeys.has(c.key));

  async function setLimit(b: FinBudget, valueInCur: number) {
    const usd = toUsd(valueInCur);
    if (!Number.isFinite(usd) || usd < 0) return;
    try {
      const upd = await api.finance.upsertBudget(b.category_key, usd);
      setBudgets((prev) => prev.map((x) => (x.category_key === b.category_key ? { ...x, ...upd } : x)));
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
  }

  async function remove(b: FinBudget) {
    try {
      await api.finance.deleteBudget(b.category_key);
      setBudgets((prev) => prev.filter((x) => x.category_key !== b.category_key));
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const v = parseFloat(addLimit);
    if (!addCat || !Number.isFinite(v) || v <= 0) { toast.error("Pick a category and a limit"); return; }
    setBusy(true);
    try {
      const b = await api.finance.upsertBudget(addCat, toUsd(v));
      setBudgets((prev) => [...prev, b]);
      setAddCat(""); setAddLimit("");
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
    finally { setBusy(false); }
  }

  const totalLimit = budgets.reduce((a, b) => a + b.limit_usd, 0);
  const totalActual = budgets.reduce((a, b) => a + b.actual_usd, 0);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          This month · spent <span className="text-foreground tabular">{fmtCompact(conv(totalActual), cur)}</span> of{" "}
          <span className="text-foreground tabular">{fmtCompact(conv(totalLimit), cur)}</span>
        </div>
        {hasRate && (
          <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
            {(["THB", "USD"] as Cur[]).map((c) => (
              <button key={c} onClick={() => setCur(c)}
                className={cn("px-3 py-1.5", cur === c ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
                {c}
              </button>
            ))}
          </div>
        )}
      </div>

      {budgets.length === 0 ? (
        <div className="rounded-md border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
          No budgets yet. Set a monthly limit for a category below.
        </div>
      ) : (
        <div className="space-y-2.5">
          {budgets.map((b) => {
            const pct = b.limit_usd > 0 ? b.actual_usd / b.limit_usd : 0;
            const tone = pct > 1 ? "bg-rose-500" : pct > 0.8 ? "bg-amber-500" : "bg-emerald-500";
            return (
              <div key={b.category_key} className="rounded-lg border border-border bg-card/40 p-3 group">
                <div className="flex items-center justify-between gap-2 mb-1.5">
                  <span className="text-sm font-medium">{b.category_label}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground tabular">
                      {fmtCompact(conv(b.actual_usd), cur)} /
                    </span>
                    <Input type="number" inputMode="decimal" step="any"
                      defaultValue={Math.round(conv(b.limit_usd))}
                      onBlur={(e) => setLimit(b, parseFloat(e.target.value))}
                      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                      className="h-7 w-24 text-xs tabular text-right" />
                    <button onClick={() => remove(b)} title="Remove budget"
                      className="opacity-0 group-hover:opacity-60 hover:!opacity-100 grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition">
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
                <div className="h-2 rounded-full bg-secondary/40 overflow-hidden">
                  <div className={cn("h-full rounded-full transition-all", tone)}
                    style={{ width: `${Math.min(100, Math.max(1, pct * 100))}%` }} />
                </div>
                {pct > 1 && (
                  <div className="text-[11px] text-rose-400 mt-1 tabular">
                    over by {fmtCompact(conv(b.actual_usd - b.limit_usd), cur)}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <form onSubmit={add} className="flex items-center gap-2">
        <select value={addCat} onChange={(e) => setAddCat(e.target.value)}
          className="h-8 text-sm px-2 rounded-md border border-border bg-background flex-1">
          <option value="">Category…</option>
          {expenseCats.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
        <Input type="number" inputMode="decimal" step="any" value={addLimit}
          onChange={(e) => setAddLimit(e.target.value)} placeholder={`limit (${cur})`} className="h-8 text-sm w-32" disabled={busy} />
        <Button type="submit" size="sm" disabled={busy || !addCat || !addLimit}><Plus className="h-3.5 w-3.5 mr-1" /> Add</Button>
      </form>
    </div>
  );
}
