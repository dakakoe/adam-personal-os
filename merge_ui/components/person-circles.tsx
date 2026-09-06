"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Users, Check, Plus } from "lucide-react";
import { api, type ContactCircle, type PersonCircleRef } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

/** label → key: lowercase, non-alphanumerics to hyphens. Empty (e.g. a
 *  non-latin label) falls back to a short random key so the circle still has
 *  a valid slug; the label is what you see either way. */
function slugify(label: string): string {
  const s = label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return s || `circle-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * Assign a contact to circles (family, investors, …). Many per contact, since
 * real relationships overlap. The dropdown filters as you type and offers to
 * create a new circle when nothing matches — so tagging never sends you off to
 * the Circles page mid-thought.
 */
export function PersonCircles({
  personId, circles,
}: {
  personId: string;
  circles: PersonCircleRef[];
}) {
  const router = useRouter();
  const [all, setAll] = useState<ContactCircle[] | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const selected = new Set(circles.map((c) => c.id));

  useEffect(() => {
    if (!open) { setQuery(""); return; }
    if (!all) api.listCircles().then(setAll).catch(() => setAll([]));
    // Focus the filter as soon as the menu opens.
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [open, all]);

  const q = query.trim().toLowerCase();
  const filtered = useMemo(
    () => (all ?? []).filter((c) => !q || c.label.toLowerCase().includes(q)),
    [all, q],
  );
  // Only offer "Create" when the typed name isn't already a circle (by label).
  const exact = (all ?? []).some((c) => c.label.trim().toLowerCase() === q);
  const canCreate = q.length > 0 && !exact;

  async function setMembership(next: Set<string>) {
    setBusy(true);
    try {
      await api.setPersonCircles(personId, [...next]);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Update failed");
    } finally { setBusy(false); }
  }

  function toggle(id: string) {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    void setMembership(next);
  }

  async function createAndAdd() {
    const label = query.trim();
    if (!label) return;
    // If the slug collides with an existing circle (e.g. "VIP" vs "vip"), just
    // add that one instead of failing on the unique key.
    const key = slugify(label);
    const clash = (all ?? []).find((c) => c.key === key);
    if (clash) { toggle(clash.id); setQuery(""); return; }
    setBusy(true);
    try {
      const created = await api.createCircle({ key, label });
      setAll((prev) => [...(prev ?? []), created]);
      await api.setPersonCircles(personId, [...selected, created.id]);
      setQuery("");
      router.refresh();
      toast.success(`Added to "${created.label}"`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't create that circle");
    } finally { setBusy(false); }
  }

  return (
    <div className="flex items-center gap-2 flex-wrap text-xs">
      <span className="inline-flex items-center gap-1.5 text-muted-foreground">
        <Users className="h-3.5 w-3.5" /> Circles
      </span>
      {circles.map((c) => (
        <span key={c.id} className="inline-flex items-center h-6 px-2 rounded-full border border-primary/40 bg-primary/10 text-primary">
          {c.label}
          {c.cadence_days != null && <span className="ml-1 opacity-70">·{c.cadence_days}d</span>}
        </span>
      ))}
      <span className="relative inline-block">
        <button type="button" onClick={() => setOpen((o) => !o)} disabled={busy}
          className="inline-flex items-center h-6 px-2 rounded-full border border-dashed border-border text-muted-foreground hover:text-foreground hover:border-foreground/40">
          {circles.length ? "Edit" : "+ Add to a circle"}
        </button>
        {open && (
          <>
            <span className="fixed inset-0 z-10" onClick={() => setOpen(false)} aria-hidden />
            <div className="absolute z-20 left-0 top-7 w-60 rounded-md border border-border bg-popover shadow-lg p-1">
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  // Enter creates when nothing matches, else picks the only
                  // remaining option — the fast path for "tag and move on".
                  if (e.key === "Enter") {
                    e.preventDefault();
                    if (canCreate && filtered.length === 0) void createAndAdd();
                    else if (filtered.length === 1) toggle(filtered[0].id);
                    else if (canCreate) void createAndAdd();
                  }
                  if (e.key === "Escape") setOpen(false);
                }}
                placeholder="Filter or create…"
                disabled={busy}
                className="w-full h-7 px-2 mb-1 rounded border border-border bg-background text-xs outline-none"
              />
              {all === null ? (
                <p className="px-2 py-1.5 text-muted-foreground">Loading…</p>
              ) : (
                <div className="max-h-56 overflow-y-auto">
                  {filtered.map((c) => (
                    <button key={c.id} type="button" onClick={() => toggle(c.id)} disabled={busy}
                      className="w-full flex items-center gap-2 px-2 py-1.5 rounded hover:bg-accent text-left">
                      <Check className={cn("h-3 w-3 shrink-0", selected.has(c.id) ? "opacity-100 text-primary" : "opacity-0")} />
                      <span className="flex-1 truncate">{c.label}</span>
                      {c.cadence_days != null && (
                        <span className="text-[10px] text-muted-foreground">{c.cadence_days}d</span>
                      )}
                    </button>
                  ))}
                  {canCreate && (
                    <button type="button" onClick={() => void createAndAdd()} disabled={busy}
                      className="w-full flex items-center gap-2 px-2 py-1.5 rounded hover:bg-accent text-left text-primary">
                      <Plus className="h-3 w-3 shrink-0" />
                      <span className="flex-1 truncate">Create &ldquo;{query.trim()}&rdquo;</span>
                    </button>
                  )}
                  {filtered.length === 0 && !canCreate && (
                    <p className="px-2 py-1.5 text-muted-foreground">
                      {all.length === 0 ? "No circles yet — type a name to create one." : "No match."}
                    </p>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </span>
    </div>
  );
}
