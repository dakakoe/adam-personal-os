"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Plus, ChevronDown, X } from "lucide-react";
import { cn } from "@/lib/utils";

export type ComboOption = { value: string; label: string };

/** Searchable select with optional inline "create new". Picking commits the
 *  value; typing filters; if onCreate is given and the query matches nothing,
 *  a "Create «query»" row appears. */
export function ComboBox({
  value, options, onSelect, onCreate, placeholder = "Select…", createLabel = "Create",
  allowClear = true,
}: {
  value: string;
  options: ComboOption[];
  onSelect: (value: string, label: string) => void;
  onCreate?: (label: string) => Promise<ComboOption | null>;
  placeholder?: string;
  createLabel?: string;
  allowClear?: boolean;
}) {
  const selected = options.find((o) => o.value === value);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  // remembers a just-picked/created label so it shows even before `options` refreshes
  const [committedLabel, setCommittedLabel] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const displayLabel = selected?.label ?? (value ? committedLabel : null) ?? "";

  function commit(v: string, l: string) {
    setCommittedLabel(v ? l : null);
    onSelect(v, l);
    setOpen(false);
    setQuery("");
  }

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) { setOpen(false); setQuery(""); }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const q = query.trim().toLowerCase();
  const filtered = (q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options).slice(0, 60);
  const exact = options.some((o) => o.label.trim().toLowerCase() === q);
  const canCreate = !!onCreate && q.length > 0 && !exact;

  async function create() {
    if (!onCreate || !query.trim()) return;
    setBusy(true);
    try {
      const o = await onCreate(query.trim());
      if (o) commit(o.value, o.label);
      else { setOpen(false); setQuery(""); }
    } finally { setBusy(false); }
  }

  return (
    <div className="relative" ref={ref}>
      <div className="relative">
        <input
          value={open ? query : displayLabel}
          placeholder={placeholder}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => { setOpen(true); setQuery(""); }}
          className="block w-full h-9 pl-2 pr-14 text-sm rounded-md border border-border bg-background outline-none focus:border-primary"
        />
        <div className="absolute inset-y-0 right-1.5 flex items-center gap-0.5 text-muted-foreground">
          {allowClear && value && !open && (
            <button type="button" onClick={() => commit("", "")} title="Clear"
              className="grid place-items-center h-5 w-5 rounded hover:text-foreground"><X className="h-3.5 w-3.5" /></button>
          )}
          <ChevronDown className="h-3.5 w-3.5" />
        </div>
      </div>
      {open && (
        <div className="absolute z-50 mt-1 w-full max-h-60 overflow-y-auto rounded-md border bg-popover text-popover-foreground shadow-lg">
          {filtered.map((o) => (
            <button type="button" key={o.value}
              onClick={() => commit(o.value, o.label)}
              className="w-full text-left px-2 py-1.5 text-sm hover:bg-accent flex items-center gap-2">
              {o.value === value ? <Check className="h-3.5 w-3.5 text-primary shrink-0" /> : <span className="w-3.5 shrink-0" />}
              <span className="truncate">{o.label}</span>
            </button>
          ))}
          {filtered.length === 0 && !canCreate && (
            <div className="px-2 py-2 text-xs text-muted-foreground">No matches.</div>
          )}
          {canCreate && (
            <button type="button" onClick={create} disabled={busy}
              className={cn("w-full text-left px-2 py-1.5 text-sm hover:bg-accent flex items-center gap-2 text-primary",
                filtered.length > 0 && "border-t border-border")}>
              <Plus className="h-3.5 w-3.5 shrink-0" /> {busy ? "Creating…" : `${createLabel} “${query.trim()}”`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
