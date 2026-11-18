"use client";

import { useState } from "react";
import { fmtCompact } from "@/lib/format";
import { cn } from "@/lib/utils";
import { api, type SpendingLine, type CashflowMonth, type NetWorth } from "@/lib/api";

type Cur = "USD" | "THB";

const PERIODS = [
  { key: "month", label: "This month" },
  { key: "30d", label: "Last 30 days" },
  { key: "year", label: "This year" },
];

function rangeFor(key: string): { from: string; to: string } {
  const now = new Date();
  const to = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(now);
  if (key === "month") return { from: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`, to };
  if (key === "year") return { from: `${now.getFullYear()}-01-01`, to };
  const d = new Date(now); d.setDate(d.getDate() - 30);
  return { from: new Intl.DateTimeFormat("en-CA").format(d), to };
}

const BARS = ["bg-sky-500", "bg-violet-500", "bg-amber-500", "bg-emerald-500", "bg-rose-500", "bg-cyan-500", "bg-fuchsia-500", "bg-lime-500"];

export function ReportsClient({
  initialSpending, cashflow, netWorth, usdThbRate,
}: {
  initialSpending: SpendingLine[]; cashflow: CashflowMonth[];
  netWorth: NetWorth | null; usdThbRate: number;
}) {
  const [cur, setCur] = useState<Cur>("THB");
  const [period, setPeriod] = useState("month");
  const [spending, setSpending] = useState<SpendingLine[]>(initialSpending);
  const [loading, setLoading] = useState(false);
  const conv = (usd: number) => (cur === "THB" ? usd * usdThbRate : usd);

  async function changePeriod(key: string) {
    setPeriod(key);
    setLoading(true);
    try {
      const { from, to } = rangeFor(key);
      setSpending(await api.finance.reportSpending(from, to));
    } catch { /* keep prior */ }
    finally { setLoading(false); }
  }

  const topSpend = spending.slice(0, 12);
  const maxSpend = Math.max(1, ...topSpend.map((s) => Math.abs(s.usd_total)));
  const totalSpend = spending.reduce((a, s) => a + s.usd_total, 0);
  const maxFlow = Math.max(1, ...cashflow.flatMap((m) => [m.income, m.expense]));

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2">
        <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
          {PERIODS.map((p) => (
            <button key={p.key} onClick={() => changePeriod(p.key)}
              className={cn("px-3 py-1.5", period === p.key ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
              {p.label}
            </button>
          ))}
        </div>
        <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
          {(["THB", "USD"] as Cur[]).map((c) => (
            <button key={c} onClick={() => setCur(c)}
              className={cn("px-3 py-1.5", cur === c ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
              {c}
            </button>
          ))}
        </div>
      </div>

      {/* Spending by category */}
      <div className="rounded-lg border border-border bg-card/40 p-4">
        <div className="flex items-baseline justify-between mb-3">
          <div className="text-sm font-semibold">Spending by category</div>
          <div className="text-xs text-muted-foreground tabular">total {fmtCompact(conv(totalSpend), cur)}</div>
        </div>
        {topSpend.length === 0 ? (
          <div className="text-center text-xs text-muted-foreground py-6">No spending in this period.</div>
        ) : (
          <div className={cn("space-y-2", loading && "opacity-50")}>
            {topSpend.map((s, i) => (
              <div key={s.category_key}>
                <div className="flex justify-between text-xs mb-0.5">
                  <span>{s.label} <span className="text-muted-foreground/60">· {s.txn_count}</span></span>
                  <span className="tabular text-muted-foreground">{fmtCompact(conv(s.usd_total), cur)}</span>
                </div>
                <div className="h-2 rounded-full bg-secondary/40 overflow-hidden">
                  <div className={cn("h-full rounded-full", BARS[i % BARS.length])}
                    style={{ width: `${Math.max(2, (Math.abs(s.usd_total) / maxSpend) * 100)}%` }} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Monthly cashflow */}
      <div className="rounded-lg border border-border bg-card/40 p-4">
        <div className="text-sm font-semibold mb-3">Income vs expense · last 6 months</div>
        {cashflow.length === 0 ? (
          <div className="text-center text-xs text-muted-foreground py-6">No data.</div>
        ) : (
          <div className="flex items-end justify-between gap-3 h-44">
            {cashflow.map((m) => (
              <div key={m.month} className="flex-1 flex flex-col items-center gap-1 h-full justify-end">
                <div className="flex items-end gap-1 h-full w-full justify-center">
                  <div className="w-1/2 max-w-[1.4rem] bg-emerald-500/70 rounded-t" title={`Income ${fmtCompact(conv(m.income), cur)}`}
                    style={{ height: `${(m.income / maxFlow) * 100}%` }} />
                  <div className="w-1/2 max-w-[1.4rem] bg-rose-500/70 rounded-t" title={`Expense ${fmtCompact(conv(m.expense), cur)}`}
                    style={{ height: `${(m.expense / maxFlow) * 100}%` }} />
                </div>
                <span className="text-[10px] text-muted-foreground">{m.month.slice(5)}</span>
              </div>
            ))}
          </div>
        )}
        <div className="flex items-center gap-4 mt-3 text-[11px] text-muted-foreground">
          <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm bg-emerald-500/70" /> Income</span>
          <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm bg-rose-500/70" /> Expense</span>
        </div>
      </div>

      {/* Net worth by asset */}
      {netWorth && netWorth.by_asset.length > 0 && (
        <div className="rounded-lg border border-border bg-card/40 p-4">
          <div className="text-sm font-semibold mb-3">Net worth by asset</div>
          <ul className="space-y-1.5">
            {netWorth.by_asset.map((a) => (
              <li key={a.asset_id} className="flex items-center justify-between text-sm">
                <span className={cn("text-[10px] font-medium px-1.5 py-0.5 rounded border",
                  a.asset_kind === "crypto" ? "text-amber-400 border-amber-500/40" : "text-sky-400 border-sky-500/40")}>
                  {a.asset_code}
                </span>
                <span className="tabular">{fmtCompact(conv(a.usd_value), cur)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
