"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, ArrowDownLeft, ArrowUpRight, ArrowLeftRight, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, type FinTransaction, type FinAccount, type FinCategory, type FinPayee } from "@/lib/api";
import { ComboBox } from "@/components/budget/combobox";
import { fmtMoney, fmtDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

type TxType = "expense" | "income" | "transfer";
type Freq = "daily" | "weekly" | "monthly" | "yearly";
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// on-chain transfers carry their chain as a tag — render a friendly badge
const CHAIN_LABEL: Record<string, string> = {
  ethereum: "Ethereum", arbitrum: "Arbitrum", base: "Base", optimism: "Optimism",
  bsc: "BNB Chain", polygon: "Polygon", solana: "Solana", tron: "Tron",
};
function chainOf(t: FinTransaction): string | null {
  if (t.source_kind !== "chain_tx") return null;
  const c = (t.tags || []).find((x) => x in CHAIN_LABEL);
  return c ? CHAIN_LABEL[c] : null;
}

// captures logged by a household member via the bot carry an owner tag
const OWNER_LABEL: Record<string, string> = { wife: "Partner", son: "Child" };
function ownerOf(t: FinTransaction): string | null {
  const o = (t.tags || []).find((x) => x in OWNER_LABEL);
  return o ? OWNER_LABEL[o] : null;
}

function todayISO(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(new Date());
}

export function TransactionsClient({
  initialTxns, accounts, categories, payees, initialAccount = "",
}: { initialTxns: FinTransaction[]; accounts: FinAccount[]; categories: FinCategory[]; payees: FinPayee[]; initialAccount?: string }) {
  const router = useRouter();
  const [addOpen, setAddOpen] = useState(false);
  const [editing, setEditing] = useState<FinTransaction | null>(null);
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [acctFilter, setAcctFilter] = useState<string>(initialAccount);
  const [q, setQ] = useState("");

  let txns = initialTxns;
  if (typeFilter) txns = txns.filter((t) => t.txn_type === typeFilter);
  if (acctFilter) txns = txns.filter((t) => t.outflow_account_id === acctFilter || t.inflow_account_id === acctFilter);
  if (q.trim()) {
    const s = q.toLowerCase();
    txns = txns.filter((t) =>
      (t.payee_text ?? "").toLowerCase().includes(s) ||
      (t.note ?? "").toLowerCase().includes(s) ||
      (t.category_label ?? "").toLowerCase().includes(s));
  }

  const acctName = accounts.find((a) => a.id === acctFilter)?.name;

  async function onDelete(t: FinTransaction, e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirm("Delete this transaction?")) return;
    try {
      const res = await api.finance.deleteTransaction(t.id);
      if (res && typeof res === "object" && "status" in res && res.status === "pending_approval") {
        toast.success("Sent to an owner for approval");
      } else {
        toast.success("Deleted");
      }
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}
          className="h-8 text-xs px-2 rounded-md border border-border bg-background text-muted-foreground">
          <option value="">All types</option>
          <option value="expense">Expense</option>
          <option value="income">Income</option>
          <option value="transfer">Transfer</option>
        </select>
        <select value={acctFilter}
          onChange={(e) => {
            // Server-refetch scoped to the account (?account=) rather than filtering
            // the pre-loaded recent window in the browser — otherwise older rows
            // (e.g. backfilled chain transfers) fall outside the initial slice.
            const id = e.target.value;
            setAcctFilter(id);
            router.push(id ? `/budget/transactions?account=${id}` : "/budget/transactions");
          }}
          className="h-8 text-xs px-2 rounded-md border border-border bg-background text-muted-foreground">
          <option value="">All accounts</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…" className="h-8 text-xs w-44" />
        <div className="ml-auto">
          <Button size="sm" onClick={() => setAddOpen(true)}><Plus className="h-3.5 w-3.5 mr-1" /> Add</Button>
        </div>
      </div>

      {acctFilter && (
        <div className="mb-2 text-xs text-muted-foreground">
          Showing <span className="text-foreground">{acctName}</span> · {txns.length} transactions
        </div>
      )}

      {txns.length === 0 ? (
        <div className="rounded-md border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
          No transactions match.
        </div>
      ) : (
        <ul className="rounded-md border border-border bg-card/40 divide-y divide-border overflow-hidden">
          {txns.map((t) => (
            <li key={t.id} onClick={() => setEditing(t)}
              className="flex items-center gap-3 px-3 py-2 hover:bg-accent/20 transition-colors group cursor-pointer">
              <TxIcon type={t.txn_type} />
              <div className="min-w-0 flex-1">
                <div className="text-sm truncate flex items-center gap-2">
                  <span className="font-medium">{t.payee_text || t.category_label || t.note || "(no description)"}</span>
                  {t.category_label && <span className="text-[10px] text-muted-foreground border border-border rounded px-1">{t.category_label}</span>}
                  {chainOf(t) && <span className="text-[10px] text-sky-400 border border-sky-400/30 bg-sky-400/10 rounded px-1 shrink-0">{chainOf(t)}</span>}
                  {ownerOf(t) && <span className="text-[10px] text-violet-400 border border-violet-400/30 bg-violet-400/10 rounded px-1 shrink-0">{ownerOf(t)}</span>}
                </div>
                <div className="text-[11px] text-muted-foreground tabular">
                  {fmtDate(t.txn_date)}
                  {t.txn_type === "transfer"
                    ? ` · ${t.outflow_account_name} → ${t.inflow_account_name}`
                    : ` · ${t.outflow_account_name || t.inflow_account_name || ""}`}
                </div>
              </div>
              <div className={cn("text-sm tabular text-right shrink-0",
                t.txn_type === "expense" && "text-rose-400",
                t.txn_type === "income" && "text-emerald-400",
                t.txn_type === "transfer" && "text-muted-foreground")}>
                {t.txn_type === "income"
                  ? `+${fmtMoney(t.inflow_amount, t.inflow_asset_code ?? "USD")}`
                  : t.txn_type === "expense"
                    ? `-${fmtMoney(t.outflow_amount, t.outflow_asset_code ?? "USD")}`
                    : fmtMoney(t.outflow_amount, t.outflow_asset_code ?? "USD")}
              </div>
              <button onClick={(e) => onDelete(t, e)} title="Delete"
                className="opacity-0 group-hover:opacity-60 hover:!opacity-100 grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <TransactionDialog open={addOpen} onClose={() => setAddOpen(false)} accounts={accounts} categories={categories} payees={payees} />
      <TransactionDialog open={editing !== null} editing={editing} onClose={() => setEditing(null)} accounts={accounts} categories={categories} payees={payees} />
    </>
  );
}

function TxIcon({ type }: { type: TxType }) {
  if (type === "income") return <ArrowDownLeft className="h-4 w-4 text-emerald-400 shrink-0" />;
  if (type === "transfer") return <ArrowLeftRight className="h-4 w-4 text-sky-400 shrink-0" />;
  return <ArrowUpRight className="h-4 w-4 text-rose-400 shrink-0" />;
}

function TransactionDialog({
  open, onClose, accounts, categories, payees, editing = null,
}: {
  open: boolean; onClose: () => void; accounts: FinAccount[]; categories: FinCategory[];
  payees: FinPayee[]; editing?: FinTransaction | null;
}) {
  const router = useRouter();
  const [type, setType] = useState<TxType>("expense");
  const [date, setDate] = useState(todayISO());
  const [fromAcct, setFromAcct] = useState("");
  const [toAcct, setToAcct] = useState("");
  const [amount, setAmount] = useState("");
  const [toAmount, setToAmount] = useState("");
  const [categoryKey, setCategoryKey] = useState("");
  const [payeeId, setPayeeId] = useState("");
  const [payeeText, setPayeeText] = useState("");
  const [note, setNote] = useState("");
  const [recurring, setRecurring] = useState(false);
  const [freq, setFreq] = useState<Freq>("monthly");
  const [byweekday, setByweekday] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  // Chain-imported rows (source_kind='chain_tx'): amount/date/wallet are locked
  // facts of the chain; only classification + an optional counter-leg (reconcile
  // / settle a debt) are editable. These drive that mode.
  const [reconcileAcct, setReconcileAcct] = useState("");
  const [settleAmount, setSettleAmount] = useState("");
  const [settleCurrency, setSettleCurrency] = useState("USD");

  const isChain = editing?.source_kind === "chain_tx";
  const acctKind = (id: string | null) => accounts.find((a) => a.id === id)?.kind;
  // the on-chain leg is the one on the crypto wallet (fallback: the populated leg)
  const onSide: "outflow" | "inflow" =
    editing && acctKind(editing.outflow_account_id) === "crypto_wallet" ? "outflow"
      : editing && acctKind(editing.inflow_account_id) === "crypto_wallet" ? "inflow"
        : editing?.outflow_account_id ? "outflow" : "inflow";
  const counterSide: "outflow" | "inflow" = onSide === "outflow" ? "inflow" : "outflow";
  const onAmount = onSide === "outflow" ? editing?.outflow_amount : editing?.inflow_amount;
  const onAsset = onSide === "outflow" ? editing?.outflow_asset_code : editing?.inflow_asset_code;
  const onAcctId = onSide === "outflow" ? editing?.outflow_account_id : editing?.inflow_account_id;
  const onAcctName = onSide === "outflow" ? editing?.outflow_account_name : editing?.inflow_account_name;
  const currencyCodes = Array.from(new Set(
    ["USD", onAsset, ...accounts.map((a) => a.currency_code)].filter(Boolean))) as string[];

  // prefill from the edited transaction (or reset for add)
  useEffect(() => {
    if (editing) {
      setType(editing.txn_type);
      setDate(editing.txn_date);
      setFromAcct(editing.outflow_account_id ?? "");
      setToAcct(editing.inflow_account_id ?? "");
      setAmount(String(editing.outflow_amount ?? editing.inflow_amount ?? ""));
      setToAmount(editing.txn_type === "transfer" ? String(editing.inflow_amount ?? "") : "");
      setCategoryKey(editing.category_key ?? "");
      setPayeeId(editing.payee_id ?? "");
      setPayeeText(editing.payee_text ?? "");
      setNote(editing.note ?? "");
      // chain reconcile prefill (counter leg = the non-wallet side)
      const cAcct = counterSide === "outflow" ? editing.outflow_account_id : editing.inflow_account_id;
      const cAmt = counterSide === "outflow" ? editing.outflow_amount : editing.inflow_amount;
      const cCode = counterSide === "outflow" ? editing.outflow_asset_code : editing.inflow_asset_code;
      setReconcileAcct(cAcct ?? "");
      setSettleAmount(String(cAmt ?? editing.usd_value ?? editing.outflow_amount ?? editing.inflow_amount ?? ""));
      setSettleCurrency(cCode ?? "USD");
    } else if (open) {
      setType("expense"); setDate(todayISO()); setFromAcct(""); setToAcct("");
      setAmount(""); setToAmount(""); setCategoryKey(""); setPayeeId(""); setPayeeText(""); setNote("");
      setRecurring(false); setFreq("monthly"); setByweekday([]);
      setReconcileAcct(""); setSettleAmount(""); setSettleCurrency("USD");
    }
  }, [editing, open, counterSide]);

  const cats = categories.filter((c) => type === "transfer" ? false : (c.kind === type || c.kind === "both"));
  const catOptions = cats.map((c) => ({ value: c.key, label: c.label }));
  const payeeOptions = payees.map((p) => ({ value: p.id, label: p.name }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();

    // Chain-imported row: patch only classification + the optional counter-leg.
    // The on-chain leg + date are never sent (and the server strips them anyway).
    if (isChain && editing) {
      const body: Record<string, unknown> = {
        payee_id: payeeId || null, payee_text: payeeText.trim() || null, note: note.trim() || null,
      };
      if (reconcileAcct) {
        const sa = parseFloat(settleAmount);
        body[`${counterSide}_account_id`] = reconcileAcct;
        body[`${counterSide}_amount`] = Number.isFinite(sa) && sa > 0 ? sa : onAmount;
        body[`${counterSide}_asset_code`] = settleCurrency || null;
        body.category_key = null;                 // reconciled → it's a transfer
      } else {
        body.category_key = categoryKey || null;   // stays single-leg, just classified
        body[`${counterSide}_account_id`] = null;
        body[`${counterSide}_amount`] = null;
        body[`${counterSide}_asset_id`] = null;
      }
      setBusy(true);
      try {
        const res = await api.finance.patchTransaction(editing.id, body);
        if (res && typeof res === "object" && "status" in res && (res as { status?: string }).status === "pending_approval") {
          toast.success("Sent to an owner for approval");
        } else {
          toast.success("Saved");
        }
        onClose();
        router.refresh();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Save failed");
      } finally { setBusy(false); }
      return;
    }

    const amt = parseFloat(amount);
    if (!Number.isFinite(amt) || amt <= 0) { toast.error("Enter an amount"); return; }
    // build the full leg set for the chosen type (nulls clear unused legs on edit)
    const body: Record<string, unknown> = {
      txn_date: date, note: note.trim() || null,
      payee_id: payeeId || null, payee_text: payeeText.trim() || null,
      outflow_account_id: null, outflow_amount: null,
      inflow_account_id: null, inflow_amount: null,
      category_key: null,
    };
    if (type === "expense") {
      if (!fromAcct) { toast.error("Pick an account"); return; }
      body.outflow_account_id = fromAcct; body.outflow_amount = amt; body.category_key = categoryKey || null;
    } else if (type === "income") {
      if (!toAcct) { toast.error("Pick an account"); return; }
      body.inflow_account_id = toAcct; body.inflow_amount = amt; body.category_key = categoryKey || null;
    } else {
      if (!fromAcct || !toAcct) { toast.error("Pick both accounts"); return; }
      const ta = parseFloat(toAmount);
      body.outflow_account_id = fromAcct; body.outflow_amount = amt;
      body.inflow_account_id = toAcct; body.inflow_amount = Number.isFinite(ta) && ta > 0 ? ta : amt;
    }
    setBusy(true);
    try {
      if (editing) {
        const res = await api.finance.patchTransaction(editing.id, body);
        if (res && typeof res === "object" && "status" in res && res.status === "pending_approval") {
          toast.success("Sent to an owner for approval");
        } else {
          toast.success("Saved");
        }
      } else if (recurring) {
        // create the recurring template (carries the same legs) starting on `date`
        if (freq === "weekly" && byweekday.length === 0) { toast.error("Pick at least one weekday"); setBusy(false); return; }
        const planned = await api.finance.createPlanned({
          ...body, name: (payeeText || note || "Recurring").trim(),
          freq, byweekday: freq === "weekly" ? byweekday : [], next_date: date, auto_post: true,
        });
        // record the first occurrence immediately if its date is today or past
        if (date <= todayISO()) await api.finance.postPlanned(planned.id);
        toast.success(date <= todayISO() ? "Added + scheduled to repeat" : "Scheduled to repeat");
      } else {
        await api.finance.createTransaction(body);
        toast.success("Added");
      }
      onClose();
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally { setBusy(false); }
  }

  function toggleDay(d: number) {
    setByweekday((prev) => prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort());
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{isChain ? "Classify on-chain transaction" : editing ? "Edit transaction" : "Add transaction"}</DialogTitle></DialogHeader>
        {!isChain && (
          <div className="inline-flex rounded-md border border-border overflow-hidden text-xs mb-1 self-start">
            {(["expense", "income", "transfer"] as TxType[]).map((t) => (
              <button key={t} type="button" onClick={() => setType(t)}
                className={cn("px-3 py-1.5 capitalize", type === t ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
                {t}
              </button>
            ))}
          </div>
        )}
        <form onSubmit={submit} className="space-y-3">
          {isChain ? (
            <>
              {/* on-chain facts — locked (amount/date/wallet come from the chain) */}
              <div className="rounded-md border border-border bg-muted/30 p-2.5">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border border-border text-muted-foreground">
                    On-chain{editing?.tags?.[0] ? ` · ${editing.tags[0]}` : ""}
                  </span>
                  <span className="text-sm font-medium">
                    {onSide === "outflow" ? "Sent" : "Received"} {onAmount} {onAsset}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  {editing?.txn_date} · {onAcctName}
                  {editing?.usd_value != null ? ` · ≈ $${editing.usd_value.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : ""}
                </div>
                <div className="text-[11px] text-muted-foreground mt-1.5">Amount, date and wallet are locked from the chain. Only the details below are editable.</div>
              </div>

              {/* Transfer / settle to another account (optional counter-leg). The
                  on-chain leg stays locked; this records where the money actually
                  went — a bank/exchange transfer (e.g. USDC → Binance → THB in SCB)
                  or a debt settle. Cross-currency: enter what landed in that account. */}
              <div className="rounded-md border border-border p-2.5 space-y-2">
                <Label>Transfer / settle to <span className="text-muted-foreground">(optional — a bank, exchange, wallet, or a debt)</span></Label>
                <select value={reconcileAcct}
                  onChange={(e) => {
                    const id = e.target.value;
                    setReconcileAcct(id);
                    const acc = accounts.find((a) => a.id === id);
                    if (acc?.currency_code) {
                      setSettleCurrency(acc.currency_code);
                      // cross-currency (e.g. USDC sent → THB received) → clear the USD
                      // estimate so you type the amount that actually arrived
                      if (acc.currency_code !== onAsset) setSettleAmount("");
                    }
                  }}
                  className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                  <option value="">Not a transfer — just categorize</option>
                  {accounts.filter((a) => a.id !== onAcctId).map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}{a.currency_code ? ` (${a.currency_code})` : ""}{a.kind === "debt" ? " · debt" : ""}
                    </option>
                  ))}
                </select>
                {reconcileAcct && (
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <Label>Amount received</Label>
                      <Input type="number" inputMode="decimal" step="any" value={settleAmount}
                        onChange={(e) => setSettleAmount(e.target.value)} placeholder="0.00" />
                    </div>
                    <div>
                      <Label>Currency</Label>
                      <select value={settleCurrency} onChange={(e) => setSettleCurrency(e.target.value)}
                        className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                        {currencyCodes.map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </div>
                  </div>
                )}
              </div>

              {/* classification: category only when it stays single-leg */}
              {!reconcileAcct && (
                <div>
                  <Label>Category</Label>
                  <ComboBox value={categoryKey}
                    options={categories.filter((c) => c.kind === (onSide === "outflow" ? "expense" : "income") || c.kind === "both").map((c) => ({ value: c.key, label: c.label }))}
                    onSelect={(v) => setCategoryKey(v)}
                    onCreate={async (label) => { const c = await api.finance.createCategory({ label, kind: onSide === "outflow" ? "expense" : "income" }); return { value: c.key, label: c.label }; }}
                    placeholder="Search or add a category…" createLabel="Add category" />
                </div>
              )}
              <div>
                <Label>Payee</Label>
                <ComboBox value={payeeId} options={payeeOptions}
                  onSelect={(v, l) => { setPayeeId(v); setPayeeText(l); }}
                  onCreate={async (name) => { const p = await api.finance.createPayee({ name }); return { value: p.id, label: p.name }; }}
                  placeholder="Search or add a payee…" createLabel="Add payee" />
              </div>
              <div>
                <Label>Note</Label>
                <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="optional" />
              </div>
            </>
          ) : (
          <>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Date</Label>
              <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </div>
            <div>
              <Label>{type === "income" ? "Amount in" : "Amount out"}</Label>
              <Input type="number" inputMode="decimal" step="any" value={amount} autoFocus
                onChange={(e) => setAmount(e.target.value)} placeholder="0.00" />
            </div>
          </div>

          {type !== "income" && (
            <div>
              <Label>{type === "transfer" ? "From account" : "Account"}</Label>
              <select value={fromAcct} onChange={(e) => setFromAcct(e.target.value)}
                className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                <option value="">Select…</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency_code})</option>)}
              </select>
            </div>
          )}
          {type !== "expense" && (
            <div>
              <Label>{type === "transfer" ? "To account" : "Account"}</Label>
              <select value={toAcct} onChange={(e) => setToAcct(e.target.value)}
                className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                <option value="">Select…</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency_code})</option>)}
              </select>
            </div>
          )}
          {type === "transfer" && (
            <div>
              <Label>Amount received <span className="text-muted-foreground">(if different currency)</span></Label>
              <Input type="number" inputMode="decimal" step="any" value={toAmount}
                onChange={(e) => setToAmount(e.target.value)} placeholder="defaults to amount sent" />
            </div>
          )}
          {type !== "transfer" && (
            <div>
              <Label>Category</Label>
              <ComboBox
                value={categoryKey}
                options={catOptions}
                onSelect={(v) => setCategoryKey(v)}
                onCreate={async (label) => {
                  const c = await api.finance.createCategory({ label, kind: type });
                  return { value: c.key, label: c.label };
                }}
                placeholder="Search or add a category…"
                createLabel="Add category"
              />
            </div>
          )}
          <div>
            <Label>Payee</Label>
            <ComboBox
              value={payeeId}
              options={payeeOptions}
              onSelect={(v, l) => { setPayeeId(v); setPayeeText(l); }}
              onCreate={async (name) => {
                const p = await api.finance.createPayee({ name });
                return { value: p.id, label: p.name };
              }}
              placeholder="Search or add a payee…"
              createLabel="Add payee"
            />
          </div>
          <div>
            <Label>Note</Label>
            <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="optional" />
          </div>

          {/* Recurring — add mode only; hands off to a planned transaction */}
          {!editing && (
            <div className="rounded-md border border-border p-2.5 space-y-2">
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={recurring} onChange={(e) => setRecurring(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-border accent-primary" />
                🔁 Repeat this <span className="text-muted-foreground">(creates a planned transaction)</span>
              </label>
              {recurring && (
                <div className="space-y-2 pl-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground w-12">Every</span>
                    <select value={freq} onChange={(e) => setFreq(e.target.value as Freq)}
                      className="h-8 text-sm px-2 rounded-md border border-border bg-background flex-1">
                      <option value="daily">Day</option>
                      <option value="weekly">Week</option>
                      <option value="monthly">Month</option>
                      <option value="yearly">Year</option>
                    </select>
                  </div>
                  {freq === "weekly" && (
                    <div className="flex gap-1">
                      {DOW.map((d, i) => (
                        <button key={d} type="button" onClick={() => toggleDay(i)}
                          className={cn("h-7 w-9 text-[11px] rounded border", byweekday.includes(i) ? "bg-accent text-foreground border-primary" : "border-border text-muted-foreground")}>{d}</button>
                      ))}
                    </div>
                  )}
                  <p className="text-[11px] text-muted-foreground">Starts {date} · auto-posts each period. Manage in the Planned tab.</p>
                </div>
              )}
            </div>
          )}
          </>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
            <Button type="submit" disabled={busy}>{busy ? "Saving…" : editing ? "Save" : "Add"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
