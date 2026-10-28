"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Building2, X, Link2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, type FinPayee, type CompanyRow } from "@/lib/api";
import { toast } from "sonner";

export function PayeesClient({ initial }: { initial: FinPayee[] }) {
  const router = useRouter();
  const [payees, setPayees] = useState<FinPayee[]>(initial);
  const [q, setQ] = useState("");
  const [newName, setNewName] = useState("");
  const [linking, setLinking] = useState<FinPayee | null>(null);
  const [busy, setBusy] = useState(false);
  // budget-only role (member) can't see CRM companies — hide the company chip + link
  const [budgetOnly, setBudgetOnly] = useState(false);
  useEffect(() => {
    setBudgetOnly(/(?:^|;\s*)merge_role=budget(?:;|$)/.test(document.cookie));
  }, []);

  const shown = q.trim()
    ? payees.filter((p) => p.name.toLowerCase().includes(q.toLowerCase()))
    : payees;

  async function rename(p: FinPayee, name: string) {
    if (!name.trim() || name === p.name) return;
    try {
      const upd = await api.finance.patchPayee(p.id, { name: name.trim() });
      setPayees((prev) => prev.map((x) => (x.id === p.id ? upd : x)));
    } catch (e) { toast.error(e instanceof Error ? e.message : "Rename failed"); }
  }

  async function unlinkCompany(p: FinPayee) {
    try {
      const upd = await api.finance.patchPayee(p.id, { company_id: null });
      setPayees((prev) => prev.map((x) => (x.id === p.id ? upd : x)));
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setBusy(true);
    try {
      const p = await api.finance.createPayee({ name: newName.trim() });
      setPayees((prev) => [p, ...prev]);
      setNewName("");
      toast.success("Payee added");
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
    finally { setBusy(false); }
  }

  function onLinked(updated: FinPayee) {
    setPayees((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));
    setLinking(null);
    router.refresh();
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search payees…" className="h-8 text-sm flex-1" />
        <span className="text-xs text-muted-foreground">{shown.length}</span>
      </div>

      <ul className="rounded-md border border-border bg-card/40 divide-y divide-border overflow-hidden">
        {shown.length === 0 ? (
          <li className="px-3 py-8 text-center text-sm text-muted-foreground">No payees.</li>
        ) : shown.map((p) => (
          <li key={p.id} className="flex items-center gap-3 px-3 py-2 hover:bg-accent/20 transition-colors group">
            <input defaultValue={p.name}
              onBlur={(e) => rename(p, e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
              className="text-sm flex-1 min-w-0 bg-transparent rounded px-1 -mx-1 hover:bg-accent/30 focus:bg-accent/40 outline-none" />
            {budgetOnly ? null : p.company_name ? (
              <span className="inline-flex items-center gap-1 text-[11px] text-sky-400 border border-sky-500/40 rounded px-1.5 py-0.5">
                <Building2 className="h-3 w-3" />{p.company_name}
                <button onClick={() => unlinkCompany(p)} className="ml-0.5 hover:text-foreground" title="Unlink"><X className="h-3 w-3" /></button>
              </span>
            ) : (
              <button onClick={() => setLinking(p)}
                className="opacity-0 group-hover:opacity-70 hover:!opacity-100 inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition">
                <Link2 className="h-3 w-3" /> Link company
              </button>
            )}
            <span className="text-[11px] text-muted-foreground tabular w-14 text-right">{p.txn_count} txns</span>
          </li>
        ))}
      </ul>

      <form onSubmit={add} className="flex items-center gap-2">
        <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="New payee…" className="h-8 text-sm flex-1" disabled={busy} />
        <Button type="submit" size="sm" disabled={busy || !newName.trim()}><Plus className="h-3.5 w-3.5 mr-1" /> Add</Button>
      </form>

      <LinkCompanyDialog payee={linking} onClose={() => setLinking(null)} onLinked={onLinked} />
    </div>
  );
}

function LinkCompanyDialog({ payee, onClose, onLinked }: {
  payee: FinPayee | null; onClose: () => void; onLinked: (p: FinPayee) => void;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<CompanyRow[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (payee) setQ("");
  }, [payee]);

  useEffect(() => {
    if (!payee) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      try { setResults(await api.listCompanies({ q: q || undefined, limit: 20 })); }
      catch { setResults([]); }
    }, 200);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [q, payee]);

  async function pick(c: CompanyRow) {
    if (!payee) return;
    try {
      const upd = await api.finance.patchPayee(payee.id, { company_id: c.id });
      toast.success(`Linked to ${c.name}`);
      onLinked(upd);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Link failed"); }
  }

  return (
    <Dialog open={payee !== null} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>Link “{payee?.name}” to a company</DialogTitle></DialogHeader>
        <Input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search your companies…" />
        <ul className="max-h-72 overflow-y-auto rounded-md border border-border divide-y divide-border">
          {results.length === 0 ? (
            <li className="px-3 py-6 text-center text-xs text-muted-foreground">No matches.</li>
          ) : results.map((c) => (
            <li key={c.id}>
              <button onClick={() => pick(c)} className="w-full text-left px-3 py-2 text-sm hover:bg-accent/30 flex items-center gap-2">
                <Building2 className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="flex-1 truncate">{c.name}</span>
                {c.country && <span className="text-[11px] text-muted-foreground">{c.country}</span>}
              </button>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
