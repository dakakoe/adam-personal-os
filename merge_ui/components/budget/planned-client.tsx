"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, ArrowUpRight, ArrowDownLeft, ArrowLeftRight, Trash2, Play, Pause, CalendarClock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, type FinPlanned, type FinAccount, type FinCategory } from "@/lib/api";
import { fmtMoney, fmtDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

type TxType = "expense" | "income" | "transfer";
type Freq = "daily" | "weekly" | "monthly" | "yearly";
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function todayISO(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(new Date());
}

function scheduleLabel(p: FinPlanned): string {
  if (p.freq === "daily") return "Every day";
  if (p.freq === "weekly")
    return p.byweekday.length ? `Weekly · ${p.byweekday.map((d) => DOW[d]).join(", ")}` : "Weekly";
  if (p.freq === "monthly") return `Monthly · day ${new Date(p.next_date).getUTCDate()}`;
  return `Yearly · ${new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(p.next_date))}`;
}

export function PlannedClient({
  initial, accounts, categories,
}: { initial: FinPlanned[]; accounts: FinAccount[]; categories: FinCategory[] }) {
  const router = useRouter();
  const [addOpen, setAddOpen] = useState(false);
  const [editing, setEditing] = useState<FinPlanned | null>(null);
  const today = todayISO();

  async function post(p: FinPlanned, e: React.MouseEvent) {
    e.stopPropagation();
    try { await api.finance.postPlanned(p.id); toast.success("Posted"); router.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
  }
  async function toggleActive(p: FinPlanned, e: React.MouseEvent) {
    e.stopPropagation();
    try { await api.finance.patchPlanned(p.id, { active: !p.active }); router.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
  }
  async function remove(p: FinPlanned, e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirm(`Delete "${p.name || "this plan"}"?`)) return;
    try { await api.finance.deletePlanned(p.id); toast.success("Deleted"); router.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
  }

  return (
    <>
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs text-muted-foreground">Recurring bills, salary, allowances, subscriptions. Auto-post creates the transaction on its date; reminders wait for you to post.</p>
        <Button size="sm" onClick={() => setAddOpen(true)}><Plus className="h-3.5 w-3.5 mr-1" /> Add</Button>
      </div>

      {initial.length === 0 ? (
        <div className="rounded-md border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
          No planned transactions yet.
        </div>
      ) : (
        <ul className="rounded-md border border-border bg-card/40 divide-y divide-border overflow-hidden">
          {initial.map((p) => {
            const amt = p.outflow_amount ?? p.inflow_amount;
            const code = p.outflow_asset_code ?? p.inflow_asset_code ?? "USD";
            const due = p.active && p.next_date <= today;
            return (
              <li key={p.id} onClick={() => setEditing(p)}
                className={cn("flex items-center gap-3 px-3 py-2.5 hover:bg-accent/20 transition-colors group cursor-pointer",
                  !p.active && "opacity-50")}>
                {p.txn_type === "income" ? <ArrowDownLeft className="h-4 w-4 text-emerald-400 shrink-0" />
                  : p.txn_type === "transfer" ? <ArrowLeftRight className="h-4 w-4 text-sky-400 shrink-0" />
                  : <ArrowUpRight className="h-4 w-4 text-rose-400 shrink-0" />}
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium truncate flex items-center gap-2">
                    {p.name || p.payee_text || p.category_label || "(unnamed)"}
                    {p.auto_post
                      ? <span className="text-[10px] text-emerald-400 border border-emerald-500/40 rounded px-1">auto</span>
                      : <span className="text-[10px] text-amber-400 border border-amber-500/40 rounded px-1">remind</span>}
                  </div>
                  <div className="text-[11px] text-muted-foreground tabular flex items-center gap-1.5">
                    <CalendarClock className="h-3 w-3" /> {scheduleLabel(p)} · next {fmtDate(p.next_date)}
                    {due && <span className="text-amber-400 font-medium">· due</span>}
                  </div>
                </div>
                <div className={cn("text-sm tabular text-right shrink-0",
                  p.txn_type === "expense" && "text-rose-400", p.txn_type === "income" && "text-emerald-400")}>
                  {fmtMoney(amt, code)}
                </div>
                {due && (
                  <button onClick={(e) => post(p, e)} title="Post now"
                    className="grid place-items-center h-7 px-2 rounded text-xs text-emerald-400 border border-emerald-500/40 hover:bg-emerald-500/10 transition shrink-0">
                    Post
                  </button>
                )}
                <button onClick={(e) => toggleActive(p, e)} title={p.active ? "Pause" : "Resume"}
                  className="opacity-0 group-hover:opacity-60 hover:!opacity-100 grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-accent transition shrink-0">
                  {p.active ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                </button>
                <button onClick={(e) => remove(p, e)} title="Delete"
                  className="opacity-0 group-hover:opacity-60 hover:!opacity-100 grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition shrink-0">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <PlannedDialog open={addOpen} onClose={() => setAddOpen(false)} accounts={accounts} categories={categories} />
      <PlannedDialog open={editing !== null} editing={editing} onClose={() => setEditing(null)} accounts={accounts} categories={categories} />
    </>
  );
}

function PlannedDialog({
  open, onClose, accounts, categories, editing = null,
}: {
  open: boolean; onClose: () => void; accounts: FinAccount[]; categories: FinCategory[]; editing?: FinPlanned | null;
}) {
  const router = useRouter();
  const [type, setType] = useState<TxType>("expense");
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [toAmount, setToAmount] = useState("");
  const [fromAcct, setFromAcct] = useState("");
  const [toAcct, setToAcct] = useState("");
  const [categoryKey, setCategoryKey] = useState("");
  const [freq, setFreq] = useState<Freq>("monthly");
  const [byweekday, setByweekday] = useState<number[]>([]);
  const [nextDate, setNextDate] = useState(todayISO());
  const [autoPost, setAutoPost] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (editing) {
      setType(editing.txn_type); setName(editing.name ?? "");
      setAmount(String(editing.outflow_amount ?? editing.inflow_amount ?? ""));
      setToAmount(editing.txn_type === "transfer" ? String(editing.inflow_amount ?? "") : "");
      setFromAcct(editing.outflow_account_id ?? ""); setToAcct(editing.inflow_account_id ?? "");
      setCategoryKey(editing.category_key ?? ""); setFreq(editing.freq);
      setByweekday(editing.byweekday ?? []); setNextDate(editing.next_date); setAutoPost(editing.auto_post);
    } else if (open) {
      setType("expense"); setName(""); setAmount(""); setToAmount(""); setFromAcct(""); setToAcct("");
      setCategoryKey(""); setFreq("monthly"); setByweekday([]); setNextDate(todayISO()); setAutoPost(true);
    }
  }, [editing, open]);

  const cats = categories.filter((c) => type !== "transfer" && (c.kind === type || c.kind === "both"));

  function toggleDay(d: number) {
    setByweekday((prev) => prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort());
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const amt = parseFloat(amount);
    if (!Number.isFinite(amt) || amt <= 0) { toast.error("Enter an amount"); return; }
    const body: Record<string, unknown> = {
      name: name.trim() || null, freq, byweekday: freq === "weekly" ? byweekday : [],
      next_date: nextDate, auto_post: autoPost, category_key: null,
      outflow_account_id: null, outflow_amount: null, inflow_account_id: null, inflow_amount: null,
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
      if (editing) await api.finance.patchPlanned(editing.id, body);
      else await api.finance.createPlanned(body);
      toast.success(editing ? "Saved" : "Planned");
      onClose(); router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Save failed"); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{editing ? "Edit planned transaction" : "New planned transaction"}</DialogTitle></DialogHeader>
        <div className="inline-flex rounded-md border border-border overflow-hidden text-xs self-start">
          {(["expense", "income", "transfer"] as TxType[]).map((t) => (
            <button key={t} type="button" onClick={() => setType(t)}
              className={cn("px-3 py-1.5 capitalize", type === t ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>{t}</button>
          ))}
        </div>
        <form onSubmit={submit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div><Label>Name</Label><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Rent, Netflix…" /></div>
            <div><Label>Amount</Label><Input type="number" inputMode="decimal" step="any" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.00" /></div>
          </div>
          {type !== "income" && (
            <div><Label>{type === "transfer" ? "From account" : "Account"}</Label>
              <select value={fromAcct} onChange={(e) => setFromAcct(e.target.value)} className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                <option value="">Select…</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency_code})</option>)}
              </select></div>
          )}
          {type !== "expense" && (
            <div><Label>{type === "transfer" ? "To account" : "Account"}</Label>
              <select value={toAcct} onChange={(e) => setToAcct(e.target.value)} className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                <option value="">Select…</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency_code})</option>)}
              </select></div>
          )}
          {type === "transfer" && (
            <div><Label>Amount received <span className="text-muted-foreground">(if different currency)</span></Label>
              <Input type="number" inputMode="decimal" step="any" value={toAmount} onChange={(e) => setToAmount(e.target.value)} placeholder="defaults to amount sent" /></div>
          )}
          {type !== "transfer" && (
            <div><Label>Category</Label>
              <select value={categoryKey} onChange={(e) => setCategoryKey(e.target.value)} className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                <option value="">Uncategorized</option>{cats.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
              </select></div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div><Label>Repeats</Label>
              <select value={freq} onChange={(e) => setFreq(e.target.value as Freq)} className="block w-full h-9 px-2 text-sm rounded-md border border-border bg-background">
                <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="yearly">Yearly</option>
              </select></div>
            <div><Label>{freq === "daily" || freq === "weekly" ? "Starting" : "Next date"}</Label>
              <Input type="date" value={nextDate} onChange={(e) => setNextDate(e.target.value)} /></div>
          </div>
          {freq === "weekly" && (
            <div><Label>On days</Label>
              <div className="flex gap-1 mt-1">
                {DOW.map((d, i) => (
                  <button key={d} type="button" onClick={() => toggleDay(i)}
                    className={cn("h-7 w-9 text-[11px] rounded border", byweekday.includes(i) ? "bg-accent text-foreground border-primary" : "border-border text-muted-foreground")}>{d}</button>
                ))}
              </div></div>
          )}
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={autoPost} onChange={(e) => setAutoPost(e.target.checked)} className="h-3.5 w-3.5 rounded border-border accent-primary" />
            Auto-post on the date <span className="text-muted-foreground">(off = just remind me)</span>
          </label>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
            <Button type="submit" disabled={busy}>{busy ? "Saving…" : editing ? "Save" : "Add"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
