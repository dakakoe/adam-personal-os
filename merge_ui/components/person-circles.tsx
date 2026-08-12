"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Users, Check } from "lucide-react";
import { api, type ContactCircle, type PersonCircleRef } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

/**
 * Assign a contact to circles (family, investors, …). Many per contact, since
 * real relationships overlap. Saving writes the whole set, so this is the one
 * place membership is decided for a person.
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
  const selected = new Set(circles.map((c) => c.id));

  useEffect(() => {
    if (!open || all) return;
    api.listCircles().then(setAll).catch(() => setAll([]));
  }, [open, all]);

  async function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    setBusy(true);
    try {
      await api.setPersonCircles(personId, [...next]);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Update failed");
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
            <div className="absolute z-20 left-0 top-7 w-56 rounded-md border border-border bg-popover shadow-lg p-1">
              {all === null ? (
                <p className="px-2 py-1.5 text-muted-foreground">Loading…</p>
              ) : all.length === 0 ? (
                <p className="px-2 py-1.5 text-muted-foreground">
                  No circles yet — create them on the Circles page.
                </p>
              ) : (
                all.map((c) => (
                  <button key={c.id} type="button" onClick={() => toggle(c.id)} disabled={busy}
                    className="w-full flex items-center gap-2 px-2 py-1.5 rounded hover:bg-accent text-left">
                    <Check className={cn("h-3 w-3", selected.has(c.id) ? "opacity-100 text-primary" : "opacity-0")} />
                    <span className="flex-1 truncate">{c.label}</span>
                    {c.cadence_days != null && (
                      <span className="text-[10px] text-muted-foreground">{c.cadence_days}d</span>
                    )}
                  </button>
                ))
              )}
            </div>
          </>
        )}
      </span>
    </div>
  );
}
