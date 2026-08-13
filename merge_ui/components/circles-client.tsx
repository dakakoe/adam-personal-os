"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Plus, Trash2, Users, Clock, AlertCircle } from "lucide-react";
import { api, type ContactCircle, type CircleDueRow } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

/** A slug from a label: "Close friends" → "close-friends". */
function slugify(s: string): string {
  return s.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64);
}

export function CirclesClient() {
  const [circles, setCircles] = useState<ContactCircle[] | null>(null);
  const [due, setDue] = useState<CircleDueRow[]>([]);
  const [label, setLabel] = useState("");
  const [cadence, setCadence] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, d] = await Promise.all([api.listCircles(), api.circlesDue({ limit: 100 })]);
      setCircles(c); setDue(d);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to load circles");
      setCircles([]);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function create() {
    const l = label.trim();
    if (!l) return;
    const days = cadence.trim() ? Number(cadence) : null;
    if (days != null && (!Number.isFinite(days) || days <= 0)) {
      toast.error("Cadence must be a positive number of days");
      return;
    }
    setBusy(true);
    try {
      // Priority defaults to the end of the list; reorder by editing it.
      const nextPriority = ((circles ?? []).at(-1)?.priority ?? 0) + 10;
      await api.createCircle({ key: slugify(l), label: l, priority: nextPriority, cadence_days: days });
      setLabel(""); setCadence("");
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Create failed");
    } finally { setBusy(false); }
  }

  async function patch(c: ContactCircle, fields: Record<string, unknown>) {
    try { await api.patchCircle(c.id, fields); await load(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
  }

  async function remove(c: ContactCircle) {
    if (!confirm(`Delete the "${c.label}" circle?\n\nThe ${c.member_count} contact(s) in it stay — they just lose this label.`)) return;
    try { await api.deleteCircle(c.id); await load(); toast.success("Circle deleted"); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold inline-flex items-center gap-2">
          <Users className="h-5 w-5 text-primary" /> Circles
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Curated groups — family, investors, founders, friends. A contact can sit in several.
          Give a circle a <span className="text-foreground">cadence</span> and anyone you haven&apos;t
          spoken to in that long shows up on{" "}
          <Link href="/followups" className="text-foreground hover:underline">Follow-ups</Link>.
          Click a circle&apos;s member count to see
          <span className="text-foreground"> everyone</span> in it, not just who&apos;s overdue.
        </p>
      </div>

      {/* Create */}
      <div className="flex items-end gap-2 flex-wrap">
        <div>
          <label className="text-[11px] text-muted-foreground">New circle</label>
          <Input value={label} onChange={(e) => setLabel(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") create(); }}
            placeholder="Family" className="h-8 text-sm w-48" />
        </div>
        <div>
          <label className="text-[11px] text-muted-foreground">Cadence (days, optional)</label>
          <Input value={cadence} onChange={(e) => setCadence(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") create(); }}
            placeholder="14" inputMode="numeric" className="h-8 text-sm w-32" />
        </div>
        <Button size="sm" onClick={create} disabled={busy || !label.trim()} className="h-8">
          <Plus className="h-3.5 w-3.5 mr-1" /> Add
        </Button>
      </div>

      {/* Circles */}
      {circles === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : circles.length === 0 ? (
        <div className="rounded-lg border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
          No circles yet. Add one above — e.g. <span className="text-foreground">Family</span> with a
          14-day cadence, or <span className="text-foreground">Investors</span> with 90.
        </div>
      ) : (
        <ul className="rounded-lg border border-border overflow-hidden bg-card/40 divide-y divide-border">
          {circles.map((c) => (
            <li key={c.id} className="flex items-center gap-3 px-3 sm:px-4 py-2.5">
              <input
                type="number" value={c.priority} title="Priority — lower ranks higher"
                onChange={(e) => patch(c, { priority: Number(e.target.value) })}
                className="h-7 w-14 px-1 text-xs rounded border border-border bg-background text-muted-foreground tabular"
              />
              <span className="font-medium min-w-0 flex-1 truncate">{c.label}</span>
              <Link href={`/persons?circle=${encodeURIComponent(c.key)}`}
                title={`Show all ${c.member_count} people in ${c.label}`}
                className="text-xs text-muted-foreground hover:text-foreground hover:underline inline-flex items-center gap-1">
                <Users className="h-3 w-3" />{c.member_count}
              </Link>
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                <Clock className="h-3 w-3" />
                <input
                  type="number" value={c.cadence_days ?? ""} placeholder="—"
                  title="Stay in touch every N days (blank = no expectation)"
                  onChange={(e) => patch(c, { cadence_days: e.target.value ? Number(e.target.value) : null })}
                  className="h-7 w-16 px-1 text-xs rounded border border-border bg-background tabular"
                />d
              </span>
              <button type="button" onClick={() => remove(c)} title="Delete circle"
                className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* The overdue list used to live here. It answers the same question the
          Follow-ups page answers — "who am I overdue with" — and having it in
          two places meant two lists to check and no single place that knew
          about both. It moved; this points at it. */}
      <div>
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-1.5">
          <AlertCircle className="h-3.5 w-3.5" /> Due to reconnect
        </h2>
        <div className="rounded-lg border border-border bg-card/40 p-4 text-xs text-muted-foreground">
          {due.length > 0
            ? <>You&apos;re overdue with <span className="text-foreground font-medium">{due.length}</span> {due.length === 1 ? "contact" : "contacts"} by these cadences. </>
            : <>Nobody is overdue by cadence right now. </>}
          They show up on{" "}
          <Link href="/followups" className="text-primary hover:underline">Follow-ups</Link>,
          alongside the conversations you&apos;ve planned deliberately.
        </div>
      </div>
    </div>
  );
}
