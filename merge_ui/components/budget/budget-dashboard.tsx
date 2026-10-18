"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Landmark, Wallet, Coins, Building, HandCoins, ArrowLeftRight, Pencil, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ChevronDown } from "lucide-react";
import { api, type NetWorth, type FinAccount, type FinAsset, type FinHolding } from "@/lib/api";
import { fmtCompact, fmtMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

const CHAIN_LABEL: Record<string, string> = {
  ethereum: "Ethereum", arbitrum: "Arbitrum", base: "Base", optimism: "Optimism",
  bsc: "BNB Chain", polygon: "Polygon", solana: "Solana", tron: "Tron",
};

type Cur = "USD" | "THB";

const KIND_ICON: Record<string, typeof Wallet> = {
  bank: Landmark, cash: Wallet, crypto_wallet: Coins, cex: ArrowLeftRight,
  dex: ArrowLeftRight, brokerage: Building, debt: HandCoins,
};
const KIND_LABEL: Record<string, string> = {
  bank: "Banks", cash: "Cash", crypto_wallet: "Crypto wallets",
  cex: "Exchanges", dex: "Exchanges", brokerage: "Brokerage", debt: "Debts",
};
const ACCOUNT_KINDS = ["bank", "cash", "crypto_wallet", "cex", "dex", "brokerage", "debt"] as const;
const GROUP_ORDER = ["Banks", "Cash", "Crypto wallets", "Exchanges", "Brokerage", "Debts"];
const CHAINS = [
  { v: "evm", l: "EVM — all chains (Metamask)" },
  { v: "solana", l: "Solana" },
  { v: "tron", l: "Tron" },
];

function ChainSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className="block h-9 px-2 text-sm rounded-md border border-border bg-background">
      <option value="">—</option>
      {CHAINS.map((c) => <option key={c.v} value={c.v}>{c.l}</option>)}
    </select>
  );
}

const GROUP_COLORS = ["bg-sky-500", "bg-violet-500", "bg-amber-500", "bg-emerald-500", "bg-rose-500", "bg-cyan-500", "bg-fuchsia-500"];

function conv(usd: number, cur: Cur, rate: number): number {
  return cur === "THB" ? usd * rate : usd;
}

export function BudgetDashboard({ netWorth, accounts, assets, holdings = [] }: { netWorth: NetWorth | null; accounts: FinAccount[]; assets: FinAsset[]; holdings?: FinHolding[] }) {
  const [cur, setCur] = useState<Cur>("THB");
  const [editAcct, setEditAcct] = useState<FinAccount | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const rate = netWorth?.usd_thb_rate || 0;

  if (!netWorth || accounts.length === 0) {
    return (
      <>
        <div className="rounded-lg border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
          No operational accounts yet. <button onClick={() => setAddOpen(true)} className="text-primary hover:underline">Add an account</button> to
          get started, or head to <span className="text-foreground">Import</span> to bring in a statement.
        </div>
        <AddAccountDialog open={addOpen} onClose={() => setAddOpen(false)} assets={assets} />
      </>
    );
  }

  const holdingsByAccount = new Map<string, FinHolding[]>();
  for (const h of holdings) (holdingsByAccount.get(h.account_id) ?? holdingsByAccount.set(h.account_id, []).get(h.account_id)!).push(h);
  function toggleExpand(id: string) {
    setExpanded((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }

  const total = conv(netWorth.total_usd, cur, rate);
  const liveAccounts = accounts.filter((a) => !a.archived);
  // group accounts by explicit group, else by a friendly account-kind label
  const groups = new Map<string, FinAccount[]>();
  for (const a of liveAccounts) {
    const g = a.account_group || KIND_LABEL[a.kind] || "Other";
    (groups.get(g) ?? groups.set(g, []).get(g)!).push(a);
  }
  const orderedGroups = [...groups.entries()].sort((a, b) => {
    const ia = GROUP_ORDER.indexOf(a[0]), ib = GROUP_ORDER.indexOf(b[0]);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });
  const maxGroup = Math.max(1, ...netWorth.by_group.map((g) => Math.abs(g.usd_value)));

  function acctUsd(a: FinAccount): number {
    return a.balances.reduce((s, b) => s + (b.usd_value ?? 0), 0);
  }

  return (
    <div className="space-y-5">
      {/* Net worth hero (total / operational / investment) + toggle */}
      <div className="rounded-lg border border-border bg-card/50 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid grid-cols-3 gap-6">
            <div>
              <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1">Net worth</div>
              <div className="text-3xl font-semibold tabular leading-none">{fmtCompact(total, cur)}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1">Operational</div>
              <div className="text-xl font-semibold tabular leading-none mt-1.5">{fmtCompact(conv(netWorth.operational_usd, cur, rate), cur)}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1">Investment</div>
              <div className="text-xl font-semibold tabular leading-none mt-1.5">{fmtCompact(conv(netWorth.investment_usd, cur, rate), cur)}</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
              {(["THB", "USD"] as Cur[]).map((c) => (
                <button key={c} onClick={() => setCur(c)}
                  className={cn("px-3 py-1.5", cur === c ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
                  {c}
                </button>
              ))}
            </div>
            <Button size="sm" onClick={() => setAddOpen(true)}><Plus className="h-3.5 w-3.5 mr-1" /> Account</Button>
          </div>
        </div>
        <div className="text-[11px] text-muted-foreground mt-2">1 USD = {rate.toFixed(2)} ฿ · Investment lives in the Portfolio tab</div>
      </div>

      {/* By group + by asset breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-lg border border-border bg-card/40 p-4">
          <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">By group</div>
          <div className="space-y-2">
            {netWorth.by_group.map((g, i) => (
              <div key={g.group}>
                <div className="flex justify-between text-xs mb-0.5">
                  <span>{g.group}</span>
                  <span className="tabular text-muted-foreground">{fmtCompact(conv(g.usd_value, cur, rate), cur)}</span>
                </div>
                <div className="h-1.5 rounded-full bg-secondary/40 overflow-hidden">
                  <div className={cn("h-full rounded-full", GROUP_COLORS[i % GROUP_COLORS.length])}
                    style={{ width: `${Math.max(2, (Math.abs(g.usd_value) / maxGroup) * 100)}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-lg border border-border bg-card/40 p-4">
          <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">By asset</div>
          <ul className="space-y-1.5">
            {netWorth.by_asset.slice(0, 8).map((a) => (
              <li key={a.asset_id} className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2">
                  <span className={cn("text-[10px] font-medium px-1.5 py-0.5 rounded border",
                    a.asset_kind === "crypto" ? "text-amber-400 border-amber-500/40" : "text-sky-400 border-sky-500/40")}>
                    {a.asset_code}
                  </span>
                  <span className="tabular text-muted-foreground text-xs">{fmtMoney(a.balance, a.asset_code)}</span>
                </span>
                <span className="tabular">{fmtCompact(conv(a.usd_value, cur, rate), cur)}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* Accounts by group */}
      <div className="space-y-4">
        {orderedGroups.map(([group, accts]) => (
          <div key={group}>
            <div className="text-xs font-medium text-muted-foreground mb-1.5 px-1">{group}</div>
            <ul className="rounded-lg border border-border bg-card/40 divide-y divide-border overflow-hidden">
              {accts.map((a) => {
                const Icon = KIND_ICON[a.kind] ?? Wallet;
                const hs = holdingsByAccount.get(a.id) ?? [];
                const canExpand = hs.length > 0;
                const isOpen = expanded.has(a.id);
                return (
                  <li key={a.id} className="hover:bg-accent/10 transition-colors group">
                    <div className="flex items-center gap-2 pr-2">
                      {canExpand ? (
                        <button onClick={() => toggleExpand(a.id)} title="Show holdings"
                          className="grid place-items-center h-7 w-6 ml-1 text-muted-foreground hover:text-foreground shrink-0">
                          <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", isOpen && "rotate-180")} />
                        </button>
                      ) : <span className="w-6 ml-1 shrink-0" />}
                      <Link href={a.kind === "debt" ? "/budget/debts" : `/budget/transactions?account=${a.id}`} className="flex items-center gap-3 py-2.5 min-w-0 flex-1">
                        <Icon className="h-4 w-4 text-muted-foreground shrink-0" />
                        <div className="min-w-0 flex-1">
                          <div className="text-sm font-medium truncate flex items-center gap-2">
                            {a.name}
                            {a.owner !== "me" && <span className="text-[10px] text-violet-400 border border-violet-500/40 rounded px-1">{a.owner}</span>}
                            {a.visibility === "private" && <span className="text-[10px] text-amber-400 border border-amber-500/40 rounded px-1">private</span>}
                            {!a.include_in_net_worth && <span className="text-[10px] text-muted-foreground/60">off-books</span>}
                          </div>
                          <div className="text-[11px] text-muted-foreground tabular flex flex-wrap gap-x-2">
                            {a.balances.length === 0
                              ? <span>{fmtMoney(0, a.currency_code ?? "USD")}</span>
                              : a.balances.map((b) => <span key={b.asset_id}>{fmtMoney(b.balance, b.asset_code)}</span>)}
                          </div>
                        </div>
                        <div className="text-sm tabular text-right shrink-0">{fmtCompact(conv(acctUsd(a), cur, rate), cur)}</div>
                      </Link>
                      <button onClick={() => setEditAcct(a)} title="Edit account"
                        className="opacity-0 group-hover:opacity-60 hover:!opacity-100 grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-accent transition shrink-0">
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                    </div>
                    {isOpen && canExpand && (
                      <ul className="pl-12 pr-3 pb-2 space-y-1">
                        {hs.map((h) => (
                          <li key={h.id} className="flex items-center gap-2 text-[11px] text-muted-foreground">
                            <span className="font-medium text-amber-400/90">{h.asset_code}</span>
                            {h.chain && <span className="border border-border rounded px-1 capitalize">{CHAIN_LABEL[h.chain] ?? h.chain}</span>}
                            <span className="tabular">{fmtMoney(h.quantity, h.asset_code)}</span>
                            <span className="ml-auto tabular">{fmtCompact(conv(h.usd_value, cur, rate), cur)}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      <AccountEditDialog account={editAcct} onClose={() => setEditAcct(null)} />
      <AddAccountDialog open={addOpen} onClose={() => setAddOpen(false)} assets={assets} />
    </div>
  );
}

function AccountEditDialog({ account, onClose }: { account: FinAccount | null; onClose: () => void }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<string>("bank");
  const [accountClass, setAccountClass] = useState<string>("operational");
  const [owner, setOwner] = useState<string>("me");
  const [group, setGroup] = useState("");
  const [inNw, setInNw] = useState(true);
  const [archived, setArchived] = useState(false);
  const [wallet, setWallet] = useState("");
  const [chain, setChain] = useState("");
  const [visibility, setVisibility] = useState<string>("shared");
  const [busy, setBusy] = useState(false);

  // sync local state when a new account is opened
  const open = account !== null;
  useEffect(() => {
    if (!account) return;
    setName(account.name); setKind(account.kind); setAccountClass(account.account_class); setOwner(account.owner);
    setGroup(account.account_group ?? ""); setInNw(account.include_in_net_worth);
    setArchived(account.archived); setWallet(account.wallet_address ?? ""); setChain(account.chain ?? "");
    setVisibility(account.visibility ?? "shared");
  }, [account]);

  const isCrypto = ["crypto_wallet", "cex", "dex"].includes(kind);

  async function save() {
    if (!account) return;
    setBusy(true);
    try {
      await api.finance.patchAccount(account.id, {
        name: name.trim(), kind, account_class: accountClass, owner, include_in_net_worth: inNw, archived,
        account_group: group.trim() || null,
        wallet_address: wallet.trim() || null,
        chain: chain.trim() || null,
        visibility,
      });
      toast.success("Account updated");
      onClose();
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>Edit account</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div><Label>Name</Label><Input value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Type</Label>
              <select value={kind} onChange={(e) => setKind(e.target.value)}
                className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                {ACCOUNT_KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k] ?? k}</option>)}
              </select>
            </div>
            <div>
              <Label>Owner</Label>
              <select value={owner} onChange={(e) => setOwner(e.target.value)}
                className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                <option value="me">Me</option><option value="wife">Wife</option><option value="son">Son</option>
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Class</Label>
              <select value={accountClass} onChange={(e) => setAccountClass(e.target.value)}
                className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                <option value="operational">Operational (Overview)</option>
                <option value="investment">Investment (Portfolio)</option>
              </select>
            </div>
            <div>
              <Label>Sharing</Label>
              <select value={visibility} onChange={(e) => setVisibility(e.target.value)}
                className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                <option value="shared">Shared (all members)</option>
                <option value="private">Private (owner only)</option>
              </select>
            </div>
          </div>
          {visibility === "private" && account?.owner_member_name && (
            <p className="text-xs text-muted-foreground -mt-1">Private to {account.owner_member_name}.</p>
          )}
          <div>
            <Label>Group <span className="text-muted-foreground">(optional — overrides the type label)</span></Label>
            <Input value={group} onChange={(e) => setGroup(e.target.value)} placeholder="e.g. Wife's accounts" />
          </div>
          {isCrypto && (
            <div className="grid grid-cols-[1fr_auto] gap-3">
              <div>
                <Label>Wallet address <span className="text-muted-foreground">(for auto-sync)</span></Label>
                <Input value={wallet} onChange={(e) => setWallet(e.target.value)} placeholder="paste address…" className="font-mono text-xs" />
              </div>
              <div>
                <Label>Chain</Label>
<ChainSelect value={chain} onChange={setChain} />
              </div>
            </div>
          )}
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={inNw} onChange={(e) => setInNw(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-border accent-primary" />
            Count toward net worth
          </label>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-border accent-primary" />
            Archived <span className="text-muted-foreground">(hidden from the dashboard; history kept)</span>
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function AddAccountDialog({ open, onClose, assets, defaultClass = "operational", defaultKind = "bank" }: { open: boolean; onClose: () => void; assets: FinAsset[]; defaultClass?: string; defaultKind?: string }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<string>(defaultKind);
  const [currency, setCurrency] = useState("THB");
  const [owner, setOwner] = useState<string>("me");
  const [group, setGroup] = useState("");
  const [opening, setOpening] = useState("");
  const [wallet, setWallet] = useState("");
  const [chain, setChain] = useState("");
  const [inNw, setInNw] = useState(true);
  const [visibility, setVisibility] = useState<string>("shared");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) { setName(""); setKind(defaultKind); setCurrency("THB"); setOwner("me"); setGroup(""); setOpening(""); setWallet(""); setChain(""); setInNw(true); setVisibility("shared"); }
  }, [open, defaultKind]);

  const isCrypto = ["crypto_wallet", "cex", "dex"].includes(kind);
  // common currencies first, then the rest
  const codes = Array.from(new Set(["THB", "USD", "RUB", "EUR", "USDT", "USDC", "SOL", "BTC", "ETH", ...assets.map((a) => a.code)]));

  async function create() {
    if (!name.trim()) { toast.error("Name is required"); return; }
    setBusy(true);
    try {
      await api.finance.createAccount({
        name: name.trim(), kind, account_class: defaultClass, currency_code: currency, owner,
        account_group: group.trim() || undefined,
        opening_balance: opening.trim() ? parseFloat(opening) : 0,
        wallet_address: wallet.trim() || undefined, chain: chain.trim() || undefined,
        include_in_net_worth: inNw, visibility,
      });
      toast.success("Account added");
      onClose();
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Create failed"); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>Add account</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div><Label>Name</Label><Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Solana wallet, KBank savings" /></div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Type</Label>
              <select value={kind} onChange={(e) => setKind(e.target.value)}
                className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                {ACCOUNT_KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k] ?? k}</option>)}
              </select>
            </div>
            <div>
              <Label>Currency</Label>
              <select value={currency} onChange={(e) => setCurrency(e.target.value)}
                className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                {codes.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Owner</Label>
              <select value={owner} onChange={(e) => setOwner(e.target.value)}
                className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                <option value="me">Me</option><option value="wife">Wife</option><option value="son">Son</option>
              </select>
            </div>
            <div>
              <Label>Opening balance</Label>
              <Input type="number" inputMode="decimal" step="any" value={opening} onChange={(e) => setOpening(e.target.value)} placeholder="0" />
            </div>
          </div>
          {isCrypto && (
            <div className="grid grid-cols-[1fr_auto] gap-3">
              <div>
                <Label>Wallet address <span className="text-muted-foreground">(for auto-sync)</span></Label>
                <Input value={wallet} onChange={(e) => setWallet(e.target.value)} placeholder="paste address…" className="font-mono text-xs" />
              </div>
              <div>
                <Label>Chain</Label>
<ChainSelect value={chain} onChange={setChain} />
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Sharing</Label>
              <select value={visibility} onChange={(e) => setVisibility(e.target.value)}
                className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                <option value="shared">Shared (all members)</option>
                <option value="private">Private (owner only)</option>
              </select>
            </div>
            <div>
              <Label>Group <span className="text-muted-foreground">(optional)</span></Label>
              <Input value={group} onChange={(e) => setGroup(e.target.value)} placeholder="overrides the type label" />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={inNw} onChange={(e) => setInNw(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-border accent-primary" />
            Count toward net worth
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={create} disabled={busy || !name.trim()}>{busy ? "Adding…" : "Add account"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
