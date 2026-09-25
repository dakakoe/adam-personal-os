"use client";

// Investments / FIFO cost-lot P&L (Phase 2). A self-contained section under the
// Portfolio tab: real realized + unrealized gain per position, open lots, and
// "Add buy" / "Record sale" actions. Fetches its own data client-side so the
// page barely changes.

import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2, ChevronRight, TrendingUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import { api, type FinAccount, type FinPosition } from "@/lib/api";
import { toast } from "sonner";

const INV_KINDS = ["brokerage", "crypto_wallet", "cex", "dex"];

function gain(n: number | null) {
  return cn("tabular", n == null ? "text-muted-foreground" : n >= 0 ? "text-emerald-400" : "text-rose-400");
}
const usd = (n: number | null | undefined) =>
  n == null ? "—" : fmtMoney(n, "USD");

export function PositionsSection({ accounts }: { accounts: FinAccount[] }) {
  const invAccounts = accounts.filter((a) => INV_KINDS.includes(a.kind));
  const acctKey = invAccounts.map((a) => a.id).join(",");

  const [positions, setPositions] = useState<FinPosition[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [sellFor, setSellFor] = useState<FinPosition | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const all = await Promise.all(
        acctKey ? acctKey.split(",").map((id) => api.finance.getPositions({ account_id: id })) : [],
      );
      setPositions(all.flat());
    } catch {
      toast.error("Couldn't load positions");
    }
    setLoading(false);
  }, [acctKey]);

  useEffect(() => {
    reload();
  }, [reload]);

  const acctName = (id: string) => invAccounts.find((a) => a.id === id)?.name ?? "—";
  const totalRealized = positions.reduce((s, p) => s + p.realized_gain_usd, 0);
  const totalUnreal = positions.reduce((s, p) => s + (p.unrealized_gain_usd ?? 0), 0);
  const totalValue = positions.reduce((s, p) => s + (p.market_value_usd ?? 0), 0);

  return (
    <section className="mt-8">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-muted-foreground flex items-center gap-1.5">
          <TrendingUp className="h-4 w-4" /> Positions (cost-lot P&amp;L)
        </h2>
        <Button size="sm" variant="outline" onClick={() => setAddOpen(true)}>
          <Plus className="h-3.5 w-3.5 mr-1" /> Add buy
        </Button>
      </div>

      {positions.length > 0 && (
        <div className="grid grid-cols-3 gap-3 mb-3 text-sm">
          <Stat label="Market value" value={usd(totalValue)} />
          <Stat label="Unrealized" value={usd(totalUnreal)} cls={gain(totalUnreal)} />
          <Stat label="Realized" value={usd(totalRealized)} cls={gain(totalRealized)} />
        </div>
      )}

      {loading ? (
        <div className="rounded-md border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
          Loading positions…
        </div>
      ) : positions.length === 0 ? (
        <div className="rounded-md border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
          No cost-lot positions yet. Add a buy to start tracking real P&amp;L.
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-card/40 divide-y divide-border">
          {positions.map((p) => {
            const key = `${p.account_id}:${p.asset_id}`;
            const open = expanded === key;
            return (
              <div key={key} className="p-3">
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => setExpanded(open ? null : key)}
                    className="flex items-center gap-1.5 min-w-0 flex-1 text-left"
                  >
                    <ChevronRight className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-90")} />
                    <span className="font-medium">{p.asset_code}</span>
                    <span className="text-xs text-muted-foreground truncate">· {acctName(p.account_id)}</span>
                  </button>
                  <div className="text-right text-xs text-muted-foreground w-28 hidden sm:block">
                    {p.remaining_quantity} @ {usd(p.avg_cost_per_unit_usd)}
                  </div>
                  <div className="text-right tabular w-24">{usd(p.market_value_usd)}</div>
                  <div className={cn("text-right w-24", gain(p.unrealized_gain_usd))}>{usd(p.unrealized_gain_usd)}</div>
                  <Button size="sm" variant="ghost" className="text-xs"
                          disabled={p.remaining_quantity <= 0}
                          onClick={() => setSellFor(p)}>Sell</Button>
                </div>

                {open && (
                  <div className="mt-2 ml-5 space-y-1 text-xs">
                    <div className="flex justify-between text-muted-foreground">
                      <span>Realized P&amp;L</span><span className={gain(p.realized_gain_usd)}>{usd(p.realized_gain_usd)}</span>
                    </div>
                    <div className="flex justify-between text-muted-foreground">
                      <span>Current price</span><span className="tabular">{usd(p.current_price_usd)}</span>
                    </div>
                    <div className="pt-1 text-muted-foreground">Open lots:</div>
                    {p.lots.filter((l) => l.remaining_quantity > 0).map((l) => (
                      <div key={l.id} className="flex justify-between tabular">
                        <span>{l.open_date} · {l.remaining_quantity}/{l.quantity}</span>
                        <span>@ {usd(l.cost_per_unit_usd)}</span>
                      </div>
                    ))}
                    {p.lots.every((l) => l.remaining_quantity <= 0) && (
                      <div className="text-muted-foreground italic">all lots sold</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {addOpen && (
        <AddBuyDialog accounts={invAccounts} onClose={() => setAddOpen(false)} onDone={() => { setAddOpen(false); reload(); }} />
      )}
      {sellFor && (
        <RecordSaleDialog position={sellFor} onClose={() => setSellFor(null)} onDone={() => { setSellFor(null); reload(); }} />
      )}
    </section>
  );
}

function Stat({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="rounded-md border border-border bg-card/40 p-2.5">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("text-base font-semibold mt-0.5", cls)}>{value}</div>
    </div>
  );
}

function AddBuyDialog({ accounts, onClose, onDone }: { accounts: FinAccount[]; onClose: () => void; onDone: () => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const [acct, setAcct] = useState(accounts[0]?.id ?? "");
  const [code, setCode] = useState("");
  const [date, setDate] = useState(today);
  const [qty, setQty] = useState("");
  const [cost, setCost] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = parseFloat(qty), c = parseFloat(cost);
    if (!acct || !code.trim() || !(q > 0) || !(c >= 0)) { toast.error("Fill account, asset, quantity, cost"); return; }
    setBusy(true);
    try {
      await api.finance.createLot({
        account_id: acct, asset_code: code.trim().toUpperCase(), asset_kind: "stock",
        open_date: date, quantity: q, cost_per_unit_usd: c,
      });
      toast.success("Buy lot added");
      onDone();
    } catch (err) { toast.error(err instanceof Error ? err.message : "Failed to add lot"); }
    setBusy(false);
  }

  return (
    <DialogShell title="Add buy" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <select value={acct} onChange={(e) => setAcct(e.target.value)} className="w-full rounded-md border border-border bg-background p-2 text-sm">
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <Input placeholder="Asset (e.g. AAPL, SOL)" value={code} onChange={(e) => setCode(e.target.value)} />
        <div className="grid grid-cols-3 gap-2">
          <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          <Input placeholder="Quantity" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />
          <Input placeholder="Cost/unit USD" inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} />
        </div>
        <DialogActions busy={busy} onClose={onClose} label="Add buy" />
      </form>
    </DialogShell>
  );
}

function RecordSaleDialog({ position, onClose, onDone }: { position: FinPosition; onClose: () => void; onDone: () => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(today);
  const [qty, setQty] = useState("");
  const [px, setPx] = useState(position.current_price_usd != null ? String(position.current_price_usd) : "");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = parseFloat(qty), p = parseFloat(px);
    if (!(q > 0) || !(p >= 0)) { toast.error("Enter quantity and price"); return; }
    if (q > position.remaining_quantity) { toast.error(`Only ${position.remaining_quantity} open`); return; }
    setBusy(true);
    try {
      await api.finance.createSale({
        account_id: position.account_id, asset_id: position.asset_id,
        sale_date: date, quantity: q, proceeds_per_unit_usd: p,
      });
      toast.success("Sale recorded");
      onDone();
    } catch (err) { toast.error(err instanceof Error ? err.message : "Failed to record sale"); }
    setBusy(false);
  }

  return (
    <DialogShell title={`Sell ${position.asset_code}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-xs text-muted-foreground">{position.remaining_quantity} open · avg cost {usd(position.avg_cost_per_unit_usd)}</p>
        <div className="grid grid-cols-3 gap-2">
          <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          <Input placeholder="Quantity" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />
          <Input placeholder="Price/unit USD" inputMode="decimal" value={px} onChange={(e) => setPx(e.target.value)} />
        </div>
        <DialogActions busy={busy} onClose={onClose} label="Record sale" />
      </form>
    </DialogShell>
  );
}

function DialogShell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-background/70 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="w-full max-w-sm rounded-lg border border-border bg-card p-4" onClick={(e) => e.stopPropagation()}>
        <h3 className="font-medium mb-3">{title}</h3>
        {children}
      </div>
    </div>
  );
}

function DialogActions({ busy, onClose, label }: { busy: boolean; onClose: () => void; label: string }) {
  return (
    <div className="flex justify-end gap-2 pt-1">
      <Button type="button" variant="ghost" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
      <Button type="submit" size="sm" disabled={busy}>{busy ? "Saving…" : label}</Button>
    </div>
  );
}
