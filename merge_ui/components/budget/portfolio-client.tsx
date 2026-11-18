"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw, Plus, Trash2, Coins } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtMoney, fmtCompact } from "@/lib/format";
import { cn } from "@/lib/utils";
import { api, type FinHolding, type FinAccount, type FinAsset } from "@/lib/api";
import { AddAccountDialog } from "@/components/budget/budget-dashboard";
import { toast } from "sonner";

type Cur = "USD" | "THB";

const CHAIN_LABEL: Record<string, string> = {
  ethereum: "Ethereum", arbitrum: "Arbitrum", base: "Base", optimism: "Optimism",
  bsc: "BNB Chain", polygon: "Polygon", solana: "Solana", tron: "Tron",
};

export function PortfolioClient({
  initial, accounts, assets, usdThbRate,
}: { initial: FinHolding[]; accounts: FinAccount[]; assets: FinAsset[]; usdThbRate: number }) {
  const router = useRouter();
  const hasRate = usdThbRate > 0;
  const [cur, setCur] = useState<Cur>(hasRate ? "THB" : "USD");
  const [syncing, setSyncing] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const conv = (usd: number) => (cur === "THB" ? usd * usdThbRate : usd);

  // only investment-account holdings (accounts are already class=investment)
  const invIds = new Set(accounts.map((a) => a.id));
  const holdings = initial.filter((h) => invIds.has(h.account_id));
  // accounts that can hold positions (crypto + brokerage)
  const holdingAccounts = accounts.filter((a) => ["crypto_wallet", "cex", "dex", "brokerage"].includes(a.kind));
  const total = holdings.reduce((s, h) => s + h.usd_value, 0);
  const hasWallets = accounts.some((a) => a.wallet_address);

  // group holdings by account
  const byAccount = new Map<string, FinHolding[]>();
  for (const h of holdings) (byAccount.get(h.account_id) ?? byAccount.set(h.account_id, []).get(h.account_id)!).push(h);

  async function sync() {
    setSyncing(true);
    try {
      const r = await api.finance.syncWallets();
      const errs = r.results.filter((x) => x.error);
      if (errs.length) toast.error(`${errs.length} wallet(s) failed: ${errs[0].error}`);
      toast.success(`Synced ${r.synced}/${r.wallets} wallets`);
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Sync failed"); }
    finally { setSyncing(false); }
  }

  async function removeHolding(h: FinHolding) {
    if (!confirm(`Remove ${h.asset_code} from ${h.account_name}?`)) return;
    try { await api.finance.deleteHolding(h.id); toast.success("Removed"); router.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
  }

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-border bg-card/50 p-4 flex items-center justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1">Portfolio value</div>
          <div className="text-2xl font-semibold tabular leading-none">{fmtCompact(conv(total), cur)}</div>
        </div>
        <div className="flex items-center gap-2">
          {hasRate && (
            <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
              {(["THB", "USD"] as Cur[]).map((c) => (
                <button key={c} onClick={() => setCur(c)}
                  className={cn("px-3 py-1.5", cur === c ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>{c}</button>
              ))}
            </div>
          )}
          {hasWallets && (
            <Button size="sm" variant="outline" onClick={sync} disabled={syncing}>
              <RefreshCw className={cn("h-3.5 w-3.5 mr-1.5", syncing && "animate-spin")} />
              {syncing ? "Syncing…" : "Sync wallets"}
            </Button>
          )}
          <Button size="sm" onClick={() => setAddOpen(true)}><Plus className="h-3.5 w-3.5 mr-1" /> Account</Button>
        </div>
      </div>

      {accounts.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No investment accounts yet. <button onClick={() => setAddOpen(true)} className="text-primary hover:underline">Add a brokerage or investment crypto account</button> —
          these are your long-hold positions, kept separate from operational spending accounts.
        </p>
      )}

      {holdings.length === 0 ? (
        <div className="rounded-md border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
          No investment holdings yet.
        </div>
      ) : (
        [...byAccount.entries()].map(([accId, hs]) => {
          const acc = accounts.find((a) => a.id === accId);
          const accTotal = hs.reduce((s, h) => s + h.usd_value, 0);
          return (
            <div key={accId}>
              <div className="flex items-center justify-between px-1 mb-1.5">
                <span className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                  <Coins className="h-3.5 w-3.5" />{acc?.name ?? hs[0].account_name}
                </span>
                <span className="text-xs tabular text-muted-foreground">{fmtCompact(conv(accTotal), cur)}</span>
              </div>
              <ul className="rounded-lg border border-border bg-card/40 divide-y divide-border overflow-hidden">
                {hs.map((h) => {
                  const pnl = h.cost_basis_usd != null ? h.usd_value - h.cost_basis_usd : null;
                  return (
                    <li key={h.id} className="flex items-center gap-3 px-3 py-2 hover:bg-accent/20 transition-colors group">
                      <span className={cn("text-[10px] font-medium px-1.5 py-0.5 rounded border shrink-0",
                        h.asset_kind === "crypto" ? "text-amber-400 border-amber-500/40"
                          : h.asset_kind === "stock" ? "text-violet-400 border-violet-500/40" : "text-sky-400 border-sky-500/40")}>
                        {h.asset_code}
                      </span>
                      {h.chain && (
                        <span className="text-[10px] text-muted-foreground border border-border rounded px-1.5 py-0.5 shrink-0 capitalize">
                          {CHAIN_LABEL[h.chain] ?? h.chain}
                        </span>
                      )}
                      <span className="text-sm tabular flex-1">{fmtMoney(h.quantity, h.asset_code)}</span>
                      {pnl != null && (
                        <span className={cn("text-[11px] tabular", pnl >= 0 ? "text-emerald-400" : "text-rose-400")}>
                          {pnl >= 0 ? "+" : ""}{fmtCompact(conv(pnl), cur)}
                        </span>
                      )}
                      <span className="text-sm tabular text-right w-24">{fmtCompact(conv(h.usd_value), cur)}</span>
                      {h.source === "chain"
                        ? <span className="text-[10px] text-muted-foreground/60 w-12">chain</span>
                        : <button onClick={() => removeHolding(h)} title="Remove"
                            className="opacity-0 group-hover:opacity-60 hover:!opacity-100 grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition shrink-0">
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>}
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })
      )}

      {holdingAccounts.length > 0 && <AddHolding accounts={holdingAccounts} />}

      <AddAccountDialog open={addOpen} onClose={() => setAddOpen(false)} assets={assets}
        defaultClass="investment" defaultKind="brokerage" />
    </div>
  );
}

function AddHolding({ accounts }: { accounts: FinAccount[] }) {
  const router = useRouter();
  const [acct, setAcct] = useState("");
  const [code, setCode] = useState("");
  const [qty, setQty] = useState("");
  const [cost, setCost] = useState("");
  const [busy, setBusy] = useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const q = parseFloat(qty);
    if (!acct || !code.trim() || !Number.isFinite(q)) { toast.error("Account, ticker and quantity required"); return; }
    const kind = accounts.find((a) => a.id === acct)?.kind;
    setBusy(true);
    try {
      await api.finance.upsertHolding({
        account_id: acct, asset_code: code.trim().toUpperCase(),
        asset_kind: kind === "brokerage" ? "stock" : "crypto",
        quantity: q, cost_basis_usd: cost.trim() ? parseFloat(cost) : null,
      });
      toast.success("Holding saved");
      setCode(""); setQty(""); setCost("");
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
    finally { setBusy(false); }
  }

  return (
    <form onSubmit={add} className="flex flex-wrap items-end gap-2 rounded-lg border border-border bg-card/40 p-3">
      <div className="flex-1 min-w-[8rem]">
        <label className="text-[11px] text-muted-foreground">Account</label>
        <select value={acct} onChange={(e) => setAcct(e.target.value)}
          className="block w-full h-8 px-2 text-sm rounded-md border border-border bg-background">
          <option value="">Select…</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </div>
      <div className="w-24"><label className="text-[11px] text-muted-foreground">Ticker</label>
        <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="AAPL" className="h-8 text-sm" /></div>
      <div className="w-28"><label className="text-[11px] text-muted-foreground">Quantity</label>
        <Input type="number" step="any" value={qty} onChange={(e) => setQty(e.target.value)} placeholder="0" className="h-8 text-sm" /></div>
      <div className="w-28"><label className="text-[11px] text-muted-foreground">Cost (USD, opt)</label>
        <Input type="number" step="any" value={cost} onChange={(e) => setCost(e.target.value)} placeholder="—" className="h-8 text-sm" /></div>
      <Button type="submit" size="sm" disabled={busy}><Plus className="h-3.5 w-3.5 mr-1" /> Add</Button>
    </form>
  );
}
