"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Tag, Check, Plus } from "lucide-react";
import { api, type ContactCircle, type PersonCircleRef } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

/** label → key: lowercase, non-alphanumerics to hyphens; empty (non-latin)
 *  falls back to a short random key so the slug is still valid. */
function slugify(label: string): string {
  const s = label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return s || `circle-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * Tag one contact with circles, inline in the People list.
 *
 * Curating 12k contacts one profile page at a time is the kind of chore nobody
 * finishes. Paired with the "No circle" filter this turns it into a single
 * pass down a list: filter to the untagged, tag, they drop out on refresh.
 *
 * The circle catalogue is fetched ONCE per page and handed down, rather than
 * each row fetching its own. Typing filters it; when nothing matches you can
 * create a circle right here — a new one is refreshed into every row's list.
 */
export function PersonCircleTag({
  personId, circles, catalogue,
}: {
  personId: string;
  circles: PersonCircleRef[];
  catalogue: ContactCircle[];
}) {
  const router = useRouter();
  const [mine, setMine] = useState<PersonCircleRef[]>(circles);
  // Local copy so a circle created from this row shows up immediately, before
  // the page refresh repopulates the shared catalogue.
  const [cat, setCat] = useState<ContactCircle[]>(catalogue);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setCat(catalogue); }, [catalogue]);

  useEffect(() => {
    if (!open) { setQuery(""); return; }
    requestAnimationFrame(() => inputRef.current?.focus());
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const q = query.trim().toLowerCase();
  const filtered = useMemo(
    () => cat.filter((c) => !q || c.label.toLowerCase().includes(q)),
    [cat, q],
  );
  const exact = cat.some((c) => c.label.trim().toLowerCase() === q);
  const canCreate = q.length > 0 && !exact;

  async function toggle(c: ContactCircle) {
    const has = mine.some((m) => m.id === c.id);
    const next = has ? mine.filter((m) => m.id !== c.id) : [...mine, c];
    setMine(next);                       // optimistic
    setBusy(true);
    try {
      await api.setPersonCircles(personId, next.map((m) => m.id));
    } catch (e) {
      setMine(mine);
      toast.error(e instanceof Error ? e.message : "Couldn't update circles");
    } finally { setBusy(false); }
  }

  async function createAndAdd() {
    const label = query.trim();
    if (!label) return;
    const key = slugify(label);
    const clash = cat.find((c) => c.key === key);
    if (clash) { void toggle(clash); setQuery(""); return; }
    setBusy(true);
    try {
      const created = await api.createCircle({ key, label });
      setCat((prev) => [...prev, created]);
      const next = [...mine, created];
      setMine(next);
      await api.setPersonCircles(personId, next.map((m) => m.id));
      setQuery("");
      router.refresh();   // so every row's catalogue picks up the new circle
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't create that circle");
    } finally { setBusy(false); }
  }

  return (
    <div className="relative shrink-0" ref={box}>
      <button
        type="button"
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen((v) => !v); }}
        disabled={busy}
        title={mine.length ? mine.map((m) => m.label).join(", ") : "Add to a circle"}
        className={cn(
          "inline-flex items-center gap-1 h-6 px-1.5 rounded border text-[11px] transition-colors",
          mine.length
            ? "border-primary/40 bg-primary/10 text-primary"
            : "border-border text-muted-foreground opacity-0 group-hover:opacity-100 focus:opacity-100",
        )}
      >
        <Tag className="h-3 w-3" />
        {mine.length > 0 && (
          <span className="max-w-[9rem] truncate">
            {mine[0].label}{mine.length > 1 ? ` +${mine.length - 1}` : ""}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-30 mt-1 w-56 rounded-md border border-border bg-popover shadow-lg overflow-hidden p-1">
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (filtered.length === 1 && !canCreate) void toggle(filtered[0]);
                else if (canCreate) void createAndAdd();
                else if (filtered.length === 1) void toggle(filtered[0]);
              }
            }}
            placeholder="Filter or create…"
            disabled={busy}
            className="w-full h-7 px-2 mb-1 rounded border border-border bg-background text-xs outline-none"
          />
          <div className="max-h-56 overflow-y-auto">
            {filtered.map((c) => {
              const has = mine.some((m) => m.id === c.id);
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggle(c); }}
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-accent rounded"
                >
                  <span className={cn(
                    "grid place-items-center h-3.5 w-3.5 rounded-sm border shrink-0",
                    has ? "bg-primary/20 border-primary/50 text-primary" : "border-border text-transparent",
                  )}>
                    <Check className="h-2.5 w-2.5" />
                  </span>
                  <span className="flex-1 truncate">{c.label}</span>
                  {c.cadence_days && (
                    <span className="text-muted-foreground tabular">{c.cadence_days}d</span>
                  )}
                </button>
              );
            })}
            {canCreate && (
              <button
                type="button"
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); void createAndAdd(); }}
                disabled={busy}
                className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-accent rounded text-primary"
              >
                <Plus className="h-3 w-3 shrink-0" />
                <span className="flex-1 truncate">Create &ldquo;{query.trim()}&rdquo;</span>
              </button>
            )}
            {filtered.length === 0 && !canCreate && (
              <p className="px-2.5 py-1.5 text-xs text-muted-foreground">
                {cat.length === 0 ? "No circles yet — type a name to create one." : "No match."}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
