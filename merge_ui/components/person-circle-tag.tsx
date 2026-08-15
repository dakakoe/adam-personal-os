"use client";

import { useEffect, useRef, useState } from "react";
import { Tag, Check } from "lucide-react";
import { api, type ContactCircle, type PersonCircleRef } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

/**
 * Tag one contact with circles, inline in the People list.
 *
 * Curating 12k contacts one profile page at a time is the kind of chore nobody
 * finishes. Paired with the "No circle" filter this turns it into a single
 * pass down a list: filter to the untagged, tag, they drop out on refresh.
 *
 * The circle catalogue is fetched ONCE per page and handed down, rather than
 * each row fetching its own — 100 rows would otherwise mean 100 identical
 * requests the moment anyone opened a dropdown.
 */
export function PersonCircleTag({
  personId, circles, catalogue,
}: {
  personId: string;
  circles: PersonCircleRef[];
  catalogue: ContactCircle[];
}) {
  const [mine, setMine] = useState<PersonCircleRef[]>(circles);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
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

  async function toggle(c: ContactCircle) {
    const has = mine.some((m) => m.id === c.id);
    const next = has ? mine.filter((m) => m.id !== c.id) : [...mine, c];
    // Optimistic: tagging is the fast repetitive action, and waiting on a
    // round-trip per click makes a pass down the list feel like wading.
    setMine(next);
    setBusy(true);
    try {
      await api.setPersonCircles(personId, next.map((m) => m.id));
    } catch (e) {
      setMine(mine);   // put it back — the server is the truth
      toast.error(e instanceof Error ? e.message : "Couldn't update circles");
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
        <div className="absolute right-0 z-30 mt-1 w-52 rounded-md border border-border bg-popover shadow-lg overflow-hidden">
          {catalogue.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              No circles yet — make one on the Circles page.
            </p>
          ) : (
            <ul className="max-h-64 overflow-y-auto divide-y divide-border">
              {catalogue.map((c) => {
                const has = mine.some((m) => m.id === c.id);
                return (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggle(c); }}
                      className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-accent"
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
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
