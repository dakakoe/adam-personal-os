"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, type FinCategory } from "@/lib/api";
import { toast } from "sonner";

export function CategoriesClient({ initial }: { initial: FinCategory[] }) {
  const router = useRouter();
  const [cats, setCats] = useState<FinCategory[]>(initial);
  const [label, setLabel] = useState("");
  const [kind, setKind] = useState<"expense" | "income" | "both">("expense");
  const [busy, setBusy] = useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!label.trim()) return;
    setBusy(true);
    try {
      const c = await api.finance.createCategory({ label: label.trim(), kind });
      setCats((prev) => [...prev, c]);
      setLabel("");
      toast.success("Category added");
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
    finally { setBusy(false); }
  }

  async function remove(c: FinCategory) {
    if (c.in_use > 0) { toast.error("Reassign its transactions first"); return; }
    if (!confirm(`Delete "${c.label}"?`)) return;
    try {
      await api.finance.deleteCategory(c.key);
      setCats((prev) => prev.filter((x) => x.key !== c.key));
      toast.success("Deleted");
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }

  async function rename(c: FinCategory, label: string) {
    if (!label.trim() || label === c.label) return;
    try {
      const upd = await api.finance.patchCategory(c.key, { label: label.trim() });
      setCats((prev) => prev.map((x) => (x.key === c.key ? upd : x)));
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Rename failed"); }
  }

  async function setKindFor(c: FinCategory, k: string) {
    try {
      const upd = await api.finance.patchCategory(c.key, { kind: k });
      setCats((prev) => prev.map((x) => (x.key === c.key ? upd : x)));
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
  }

  // top-level first, children indented under their parent
  const byParent = new Map<string | null, FinCategory[]>();
  for (const c of cats) {
    const p = c.parent_key && cats.some((x) => x.key === c.parent_key) ? c.parent_key : null;
    (byParent.get(p) ?? byParent.set(p, []).get(p)!).push(c);
  }
  const roots = byParent.get(null) ?? [];

  function Row({ c, depth }: { c: FinCategory; depth: number }) {
    return (
      <>
        <li className="flex items-center gap-2 px-3 py-1.5 hover:bg-accent/20 transition-colors group" style={{ paddingLeft: 12 + depth * 18 }}>
          <input
            defaultValue={c.label}
            onBlur={(e) => rename(c, e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
            className="text-sm flex-1 min-w-0 bg-transparent rounded px-1 -mx-1 hover:bg-accent/30 focus:bg-accent/40 outline-none" />
          <select value={c.kind} onChange={(e) => setKindFor(c, e.target.value)}
            className="text-[11px] px-1 h-6 rounded border border-border bg-secondary/40 text-muted-foreground">
            <option value="expense">expense</option>
            <option value="income">income</option>
            <option value="both">both</option>
          </select>
          <span className="text-[11px] text-muted-foreground tabular w-14 text-right">{c.in_use} txns</span>
          <button onClick={() => remove(c)} disabled={c.in_use > 0}
            title={c.in_use > 0 ? "Has transactions" : "Delete"}
            className="opacity-0 group-hover:opacity-60 hover:!opacity-100 disabled:opacity-20 grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition shrink-0">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </li>
        {(byParent.get(c.key) ?? []).map((child) => <Row key={child.key} c={child} depth={depth + 1} />)}
      </>
    );
  }

  return (
    <div className="space-y-4">
      <ul className="rounded-md border border-border bg-card/40 divide-y divide-border overflow-hidden">
        {roots.length === 0 ? (
          <li className="px-3 py-8 text-center text-sm text-muted-foreground">No categories yet — sync ZenMoney or add one below.</li>
        ) : roots.map((c) => <Row key={c.key} c={c} depth={0} />)}
      </ul>

      <form onSubmit={add} className="flex items-center gap-2">
        <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="New category…" className="h-8 text-sm flex-1" disabled={busy} />
        <select value={kind} onChange={(e) => setKind(e.target.value as "expense" | "income" | "both")} disabled={busy}
          className="h-8 text-xs px-2 rounded-md border border-border bg-background">
          <option value="expense">Expense</option>
          <option value="income">Income</option>
          <option value="both">Both</option>
        </select>
        <Button type="submit" size="sm" disabled={busy || !label.trim()}><Plus className="h-3.5 w-3.5 mr-1" /> Add</Button>
      </form>
    </div>
  );
}
