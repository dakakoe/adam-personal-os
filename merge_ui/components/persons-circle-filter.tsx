"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Users, X, ChevronDown } from "lucide-react";
import { api, type ContactCircle } from "@/lib/api";

/** Circle filter chip for the People list. Picking a circle navigates to
 *  ?circle=<key> (server re-fetches the filtered slice); the × clears it.
 *
 *  Filters on the KEY rather than the id so the URL stays readable and
 *  shareable — /persons?circle=family. `currentLabel` is resolved server-side
 *  so the chip reads as a label even before this component has loaded.
 *
 *  The circle list is fetched lazily, only when the dropdown is first opened:
 *  the People page renders for everyone, and most visits never touch this. */
export function PersonsCircleFilter({
  currentKey, currentLabel,
}: {
  currentKey: string | null;
  currentLabel: string | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [circles, setCircles] = useState<ContactCircle[] | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || circles) return;
    api.listCircles().then(setCircles).catch(() => setCircles([]));
  }, [open, circles]);

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

  if (currentKey) {
    return (
      <span className="inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border bg-accent/40 text-xs">
        <Users className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="font-medium max-w-[12rem] truncate">{currentLabel ?? currentKey}</span>
        <button
          type="button"
          onClick={() => router.push("/persons")}
          className="ml-0.5 text-muted-foreground hover:text-foreground"
          title="Clear circle filter"
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
        <Users className="h-3.5 w-3.5" />
        Filter by circle
        <ChevronDown className="h-3 w-3" />
      </button>

      {open && (
        <div className="absolute right-0 z-20 mt-1 w-56 rounded-md border border-border bg-popover shadow-lg overflow-hidden">
          {circles === null ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">Loading…</p>
          ) : circles.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              No circles yet — make one on the Circles page.
            </p>
          ) : (
            <ul className="max-h-72 overflow-y-auto divide-y divide-border">
              {/* The untagged backlog — the point of tagging from this list. */}
              <li>
                <button
                  type="button"
                  onClick={() => { setOpen(false); router.push("/persons?circle=__none__"); }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs hover:bg-accent"
                >
                  <span className="flex-1 truncate text-muted-foreground">No circle</span>
                </button>
              </li>
              {circles.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setOpen(false);
                      router.push(`/persons?circle=${encodeURIComponent(c.key)}`);
                    }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs hover:bg-accent"
                  >
                    <span className="flex-1 truncate">{c.label}</span>
                    <span className="text-muted-foreground tabular">{c.member_count}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
