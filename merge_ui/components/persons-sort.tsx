"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDownWideNarrow, ChevronDown, Check } from "lucide-react";
import type { PersonSort } from "@/lib/api";
import { cn } from "@/lib/utils";
import { personsHref, type PersonsFilters } from "@/lib/persons-filters";

/**
 * How the People list is ordered.
 *
 * Three answers to three different questions: who do I deal with most, who
 * have I spoken to lately, and who have I let go cold. The last one is the
 * reason this exists — a list ordered by volume can't tell you who you're
 * losing touch with.
 *
 * Like the other filters, the choice lives in the URL and carries whatever
 * else is set, so ordering doesn't drop your company or circle filter.
 */

export const SORT_OPTIONS: { key: PersonSort; label: string; hint: string }[] = [
  { key: "interactions", label: "Most contact", hint: "Who you deal with most" },
  { key: "recent", label: "Contacted recently", hint: "Newest conversation first" },
  { key: "oldest", label: "Contacted longest ago", hint: "Who's going cold" },
];

export function PersonsSort({
  current, carry,
}: {
  current: PersonSort | null;
  carry: PersonsFilters;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // Close on an outside click or Escape — same behaviour as the other chips.
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

  const active = SORT_OPTIONS.find((o) => o.key === current) ?? SORT_OPTIONS[0];

  // The default needs no parameter — a clean URL is the unsorted-looking one —
  // and personsHref drops the offset, since a reordered list makes page 4
  // meaningless.
  const go = (sort: PersonSort) =>
    router.push(personsHref(carry, { sort: sort === "interactions" ? null : sort }));

  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={active.hint}
        className={cn("inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-xs",
          current && current !== "interactions"
            ? "bg-accent/40 text-foreground"
            : "text-muted-foreground hover:bg-accent")}
      >
        <ArrowDownWideNarrow className="h-3.5 w-3.5" />
        {active.label}
        <ChevronDown className="h-3 w-3" />
      </button>

      {open && (
        <div className="absolute right-0 z-20 mt-1 w-60 rounded-md border border-border bg-popover shadow-lg overflow-hidden">
          <ul className="divide-y divide-border">
            {SORT_OPTIONS.map((o) => (
              <li key={o.key}>
                <button
                  type="button"
                  onClick={() => { setOpen(false); go(o.key); }}
                  className="w-full flex items-start gap-2 px-3 py-2 text-left hover:bg-accent"
                >
                  <Check className={cn("h-3.5 w-3.5 mt-0.5 shrink-0",
                    o.key === active.key ? "text-primary" : "text-transparent")} />
                  <span className="min-w-0">
                    <span className="block text-xs">{o.label}</span>
                    <span className="block text-[10px] text-muted-foreground">{o.hint}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
