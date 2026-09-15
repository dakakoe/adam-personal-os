"use client";

// Multi-wallet aggregation (crypto-sync++). Holdings rolled up per asset across
// every on-chain wallet, a per-wallet breakdown with gas spend + transfer
// counts, and each wallet's latest sync health. Presentational: fed by
// server-fetched data, so the Portfolio "Sync wallets" button's router.refresh()
// repopulates it for free.

import { useState } from "react";
import { ChevronRight, Layers, Fuel, AlertTriangle } from "lucide-react";
import { fmtMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { WalletsSummary } from "@/lib/api";

const usd = (n: number | null | undefined) => (n == null ? "—" : fmtMoney(n, "USD"));

const CHAIN_LABEL: Record<string, string> = {
  ethereum: "Ethereum", arbitrum: "Arbitrum", base: "Base", optimism: "Optimism",
  bsc: "BSC", polygon: "Polygon", solana: "Solana", tron: "Tron", evm: "EVM",
};

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function StatusDot({ status }: { status: "ok" | "partial" | "error" | null }) {
  const color =
    status === "ok" ? "bg-emerald-400" :
    status === "partial" ? "bg-amber-400" :
    status === "error" ? "bg-rose-400" : "bg-muted-foreground/40";
  return <span className={cn("inline-block h-1.5 w-1.5 rounded-full shrink-0", color)} />;
}

export function WalletsSummarySection({ summary }: { summary: WalletsSummary | null }) {
  const [expanded, setExpanded] = useState<string | null>(null);

  if (!summary || (summary.by_wallet.length === 0 && summary.by_asset.length === 0)) {
    return null; // nothing on-chain to aggregate
  }

  return (
    <section className="mt-8">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-muted-foreground flex items-center gap-1.5">
          <Layers className="h-4 w-4" /> Wallets (aggregated)
        </h2>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {summary.total_gas_usd > 0 && (
            <span className="inline-flex items-center gap-1">
              <Fuel className="h-3.5 w-3.5" /> {usd(summary.total_gas_usd)} gas
            </span>
          )}
          <span className="tabular text-foreground font-medium">{usd(summary.total_usd)}</span>
        </div>
      </div>

      {/* per-wallet health + activity */}
      {summary.by_wallet.length > 0 && (
        <div className="rounded-lg border border-border bg-card/40 divide-y divide-border mb-3">
          {summary.by_wallet.map((w) => (
            <div key={w.account_id} className="p-3 flex items-center gap-3 text-sm">
              <StatusDot status={w.sync_status} />
              <div className="min-w-0 flex-1">
                <div className="font-medium truncate">{w.account_name ?? "—"}</div>
                <div className="text-xs text-muted-foreground flex items-center gap-2">
                  {w.chain && <span>{CHAIN_LABEL[w.chain] ?? w.chain}</span>}
                  <span>synced {timeAgo(w.last_synced_at)}</span>
                  {w.transfers > 0 && <span>· {w.transfers} tx</span>}
                </div>
                {w.sync_error && (
                  <div className="text-xs text-rose-400/90 flex items-center gap-1 mt-0.5">
                    <AlertTriangle className="h-3 w-3 shrink-0" />
                    <span className="truncate">{w.sync_error}</span>
                  </div>
                )}
              </div>
              {w.gas_usd > 0 && (
                <div className="text-right text-xs text-muted-foreground w-20 hidden sm:block">
                  {usd(w.gas_usd)} gas
                </div>
              )}
              <div className="text-right tabular w-24">{usd(w.usd_value)}</div>
            </div>
          ))}
        </div>
      )}

      {/* per-asset rollup across wallets */}
      {summary.by_asset.length > 0 && (
        <div className="rounded-lg border border-border bg-card/40 divide-y divide-border">
          {summary.by_asset.map((a) => {
            const open = expanded === a.asset_code;
            const multi = a.wallets.length > 1;
            return (
              <div key={a.asset_code} className="p-3">
                <div className="flex items-center gap-3 text-sm">
                  <button
                    type="button"
                    onClick={() => multi && setExpanded(open ? null : a.asset_code)}
                    className={cn("flex items-center gap-1.5 min-w-0 flex-1 text-left", !multi && "cursor-default")}
                  >
                    {multi ? (
                      <ChevronRight className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-90")} />
                    ) : (
                      <span className="w-3.5" />
                    )}
                    <span className="font-medium">{a.asset_code}</span>
                    {multi && <span className="text-xs text-muted-foreground">· {a.wallets.length} wallets</span>}
                  </button>
                  <div className="text-right text-xs text-muted-foreground w-28 hidden sm:block tabular">
                    {a.quantity.toLocaleString(undefined, { maximumFractionDigits: 6 })}
                  </div>
                  <div className="text-right tabular w-24">{usd(a.usd_value)}</div>
                </div>
                {open && multi && (
                  <div className="mt-2 ml-5 space-y-1 text-xs">
                    {a.wallets.map((wl, i) => (
                      <div key={`${wl.account_id}:${wl.chain}:${i}`} className="flex justify-between tabular text-muted-foreground">
                        <span className="truncate">
                          {wl.account_name ?? "—"}{wl.chain ? ` · ${CHAIN_LABEL[wl.chain] ?? wl.chain}` : ""}
                        </span>
                        <span>{wl.quantity.toLocaleString(undefined, { maximumFractionDigits: 6 })} · {usd(wl.usd_value)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
