"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AtSign, ChevronDown, X } from "lucide-react";
import { api, type PersonSource } from "@/lib/api";
import { CHANNEL_ICONS, CHANNEL_COLORS, sourceDisplayName } from "@/lib/channels";
import { cn } from "@/lib/utils";
import { personsHref, type PersonsFilters } from "@/lib/persons-filters";

/** Source filter chip for the People list: "everyone I have on Telegram".
 *
 *  Filters on the identity SOURCE — which accounts a person has — because that
 *  is what the row icons already show, and what makes the list sortable by
 *  where someone lives. The URL carries it (/persons?source=telegram) so the
 *  view is shareable and bookmarkable, like ?circle=.
 *
 *  The other filters this page carries are passed in rather than read from
 *  useSearchParams (which would need a Suspense boundary in a server page), so
 *  picking a source KEEPS an active search, company, circle or sort instead of
 *  replacing it — combining filters is the point.
 *
 *  The source list is fetched lazily, only when the dropdown is first opened. */
export function PersonsSourceFilter({
  current, carry,
}: {
  current: string | null;
  carry: PersonsFilters;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<PersonSource[] | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || sources) return;
    api.listPersonSources().then(setSources).catch(() => setSources([]));
  }, [open, sources]);

  // Close on an outside click or Escape, like the other pickers.
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

  // No offset: a changed filter starts at the first page, or you land on an
  // empty page 4 of a list that now has two pages. personsHref enforces that.
  const go = (source: string | null) => router.push(personsHref(carry, { source }));

  if (current) {
    const Icon = CHANNEL_ICONS[current] ?? AtSign;
    return (
      <span className="inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border bg-accent/40 text-xs">
        <Icon className={cn("h-3.5 w-3.5", CHANNEL_COLORS[current] ?? "text-muted-foreground")} />
        <span className="font-medium max-w-[12rem] truncate">{sourceDisplayName(current)}</span>
        <button
          type="button"
          onClick={() => go(null)}
          className="ml-0.5 text-muted-foreground hover:text-foreground"
          title="Clear source filter"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </span>
    );
  }

  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-xs text-muted-foreground hover:bg-accent"
      >
        <AtSign className="h-3.5 w-3.5" />
        Filter by source
        <ChevronDown className="h-3 w-3" />
      </button>

      {open && (
        <div className="absolute right-0 z-20 mt-1 w-56 rounded-md border border-border bg-popover shadow-lg overflow-hidden">
          {sources === null ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">Loading…</p>
          ) : sources.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">No sources yet.</p>
          ) : (
            <ul className="max-h-72 overflow-y-auto divide-y divide-border">
              {sources.map((s) => {
                const Icon = CHANNEL_ICONS[s.source] ?? AtSign;
                return (
                  <li key={s.source}>
                    <button
                      type="button"
                      onClick={() => { setOpen(false); go(s.source); }}
                      className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs hover:bg-accent"
                    >
                      <Icon className={cn("h-3.5 w-3.5 shrink-0",
                        CHANNEL_COLORS[s.source] ?? "text-muted-foreground")} />
                      <span className="flex-1 truncate">{sourceDisplayName(s.source)}</span>
                      <span className="text-muted-foreground tabular">{s.people.toLocaleString()}</span>
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
