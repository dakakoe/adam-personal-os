"use client";

// Unified debts. Both debt accounts are the double-entry mechanism; here we net
// each person across them into ONE position per currency: for every leg sitting
// on a debt account, money INTO the debt account counts +, money OUT counts − —
// so positive = they owe you, negative = you owe them, zero = fully settled.

import { useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtMoney, fmtDate } from "@/lib/format";
import type { FinAccount, FinTransaction } from "@/lib/api";

const EPS = 0.01;

type Person = { key: string; name: string; nets: Record<string, number>; txns: FinTransaction[] };

export function DebtsClient({ accounts, txns }: { accounts: FinAccount[]; txns: FinTransaction[] }) {
  const debtIds = new Set(accounts.filter((a) => a.kind === "debt").map((a) => a.id));
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showSettled, setShowSettled] = useState(false);

  // the debt leg of a txn: the leg sitting on a debt account, with its sign
  function debtLeg(t: FinTransaction): { into: boolean; amount: number; code: string } | null {
    if (t.inflow_account_id && debtIds.has(t.inflow_account_id))
      return { into: true, amount: t.inflow_amount ?? 0, code: t.inflow_asset_code ?? "USD" };
    if (t.outflow_account_id && debtIds.has(t.outflow_account_id))
      return { into: false, amount: t.outflow_amount ?? 0, code: t.outflow_asset_code ?? "USD" };
    return null;
  }

  // aggregate by person (person_id, else payee_text)
  const byKey = new Map<string, Person>();
  for (const t of txns) {
    const leg = debtLeg(t);
    if (!leg) continue;
    const key = t.person_id ?? `name:${(t.payee_text ?? "").toLowerCase()}`;
    const name = t.person_name ?? t.payee_text ?? "(unknown)";
    let p = byKey.get(key);
    if (!p) { p = { key, name, nets: {}, txns: [] }; byKey.set(key, p); }
    p.nets[leg.code] = (p.nets[leg.code] ?? 0) + (leg.into ? leg.amount : -leg.amount);
    p.txns.push(t);
  }

  const people = Array.from(byKey.values()).map((p) => {
    const nets = Object.fromEntries(Object.entries(p.nets).filter(([, v]) => Math.abs(v) >= EPS));
    const mag = Math.max(0, ...Object.values(nets).map((v) => Math.abs(v)));
    return { ...p, nets, settled: Object.keys(nets).length === 0, mag };
  });
  people.sort((a, b) => Number(a.settled) - Number(b.settled) || b.mag - a.mag || a.name.localeCompare(b.name));

  const owedToYou: Record<string, number> = {};
  const youOwe: Record<string, number> = {};
  for (const p of people) for (const [c, v] of Object.entries(p.nets)) {
    if (v > 0) owedToYou[c] = (owedToYou[c] ?? 0) + v;
    else youOwe[c] = (youOwe[c] ?? 0) - v;
  }

  const settledCount = people.filter((p) => p.settled).length;
  const visible = showSettled ? people : people.filter((p) => !p.settled);

  function toggle(k: string) {
    setExpanded((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <TotalCard label="Owed to you" nets={owedToYou} tone="pos" />
        <TotalCard label="You owe" nets={youOwe} tone="neg" />
      </div>

      <div className="rounded-lg border border-border bg-card/40 divide-y divide-border overflow-hidden">
        {visible.length === 0 ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            {people.length === 0 ? "No debts yet." : "No open debts — everything's settled."}
          </div>
        ) : visible.map((p) => {
          const open = expanded.has(p.key);
          return (
            <div key={p.key}>
              <button type="button" onClick={() => toggle(p.key)}
                className="w-full text-left px-3 py-2.5 hover:bg-accent/30 transition-colors flex items-center gap-2.5">
                {open ? <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" />
                  : <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />}
                <span className="min-w-0 flex-1">
                  <span className="text-sm font-medium">{p.name}</span>
                  {p.settled && <span className="ml-2 text-xs text-muted-foreground">settled</span>}
                </span>
                <span className="flex flex-wrap justify-end gap-x-2 gap-y-0.5 text-sm tabular">
                  {p.settled ? <span className="text-muted-foreground">—</span>
                    : Object.entries(p.nets).map(([c, v]) => (
                      <span key={c} className={v >= 0 ? "text-emerald-400" : "text-rose-400"}>
                        {v < 0 ? "-" : ""}{fmtMoney(Math.abs(v), c)}
                      </span>
                    ))}
                </span>
              </button>
              {open && (
                <div className="bg-background/40 px-3 pb-2.5">
                  <div className="text-[11px] text-muted-foreground px-1 py-1.5">
                    {p.settled ? "Fully settled — loans and repayments net to zero."
                      : Object.entries(p.nets).map(([c, v]) =>
                        v >= 0 ? `They owe you ${fmtMoney(v, c)}` : `You owe ${fmtMoney(-v, c)}`).join(" · ")}
                  </div>
                  <ul className="divide-y divide-border/60">
                    {[...p.txns].sort((a, b) => (a.txn_date < b.txn_date ? 1 : -1)).map((t) => {
                      const leg = debtLeg(t);
                      return (
                        <li key={t.id} className="flex items-center gap-2 py-1.5 text-xs">
                          <span className="text-muted-foreground w-[4.5rem] shrink-0">{fmtDate(t.txn_date)}</span>
                          <span className="min-w-0 flex-1 text-muted-foreground truncate">
                            {t.outflow_account_name ?? "?"} → {t.inflow_account_name ?? "?"}
                          </span>
                          <span className="tabular shrink-0">{fmtMoney(leg?.amount ?? 0, leg?.code ?? "USD")}</span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {settledCount > 0 && (
        <button type="button" onClick={() => setShowSettled((s) => !s)}
          className="text-xs text-muted-foreground hover:text-foreground transition-colors">
          {showSettled ? "Hide" : "Show"} {settledCount} settled {settledCount === 1 ? "person" : "people"}
        </button>
      )}
    </div>
  );
}

function TotalCard({ label, nets, tone }: { label: string; nets: Record<string, number>; tone: "pos" | "neg" }) {
  const entries = Object.entries(nets).filter(([, v]) => Math.abs(v) >= EPS);
  return (
    <div className="rounded-lg border border-border bg-card/40 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-lg font-semibold tabular",
        tone === "pos" ? "text-emerald-400" : "text-rose-400")}>
        {entries.length === 0 ? <span className="text-muted-foreground text-base font-normal">—</span>
          : entries.map(([c, v]) => <span key={c}>{fmtMoney(v, c)}</span>)}
      </div>
    </div>
  );
}
