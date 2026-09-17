"use client";

import { useEffect, useState } from "react";
import { Link2 } from "lucide-react";
import { CompanyLogo } from "@/components/company-logo";
import { Input } from "@/components/ui/input";
import { api, type CompanyRow } from "@/lib/api";
import { cn } from "@/lib/utils";

export type CompanyValue = { name: string; companyId: string | null };

/** Company field that autocompletes from the companies you already have.
 *
 *  Picking a suggestion links the real company record (companyId), so the deal
 *  shows up on that company's page. Typing anything else keeps it as free text,
 *  so a company you haven't added yet never blocks creating the deal. Editing a
 *  linked name unlinks it: the link must always match what the field says. */
export function CompanyCombobox({
  id, value, onChange, placeholder = "Acme Corp",
}: {
  id?: string;
  value: CompanyValue;
  onChange: (v: CompanyValue) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<CompanyRow[]>([]);
  const [hi, setHi] = useState(0);
  const q = value.name.trim();

  useEffect(() => {
    // Nothing to search for, or already linked: no suggestions.
    if (!open || !q || value.companyId) { setResults([]); return; }
    let cancel = false;
    const t = setTimeout(async () => {
      try {
        const rows = await api.listCompanies({ q, limit: 8 });
        if (!cancel) { setResults(rows); setHi(0); }
      } catch { if (!cancel) setResults([]); }
    }, 200);
    return () => { cancel = true; clearTimeout(t); };
  }, [q, open, value.companyId]);

  const pick = (c: CompanyRow) => { onChange({ name: c.name, companyId: c.id }); setOpen(false); };

  return (
    <div className="relative">
      <Input
        id={id}
        value={value.name}
        autoComplete="off"
        onChange={(e) => { onChange({ name: e.target.value, companyId: null }); setOpen(true); }}
        onFocus={() => setOpen(true)}
        // Delayed so a click on a suggestion lands before the list closes.
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          const listed = open && results.length > 0;
          if (e.key === "ArrowDown" && listed) {
            e.preventDefault(); setHi((i) => (i + 1) % results.length); return;
          }
          if (e.key === "ArrowUp" && listed) {
            e.preventDefault(); setHi((i) => (i - 1 + results.length) % results.length); return;
          }
          if (e.key === "Escape" && listed) { e.preventDefault(); setOpen(false); return; }
          // Enter picks the highlighted company while the list is showing;
          // otherwise it behaves as normal and submits the form.
          if (e.key === "Enter" && listed && results[hi]) { e.preventDefault(); pick(results[hi]); }
        }}
        placeholder={placeholder}
        className={cn(value.companyId && "pr-16")}
      />
      {value.companyId && (
        <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 inline-flex items-center gap-1 text-[10px] text-emerald-400"
          title="Linked to the company record">
          <Link2 className="h-3 w-3" /> linked
        </span>
      )}
      {open && results.length > 0 && (
        <div className="absolute z-30 left-0 right-0 mt-1 max-h-56 overflow-auto rounded-md border border-border bg-popover shadow-lg p-1">
          {results.map((c, i) => (
            <button
              key={c.id} type="button"
              onMouseDown={(e) => { e.preventDefault(); pick(c); }}
              onMouseEnter={() => setHi(i)}
              className={cn("w-full text-left px-2 py-1.5 rounded text-sm flex items-center gap-2",
                i === hi ? "bg-accent" : "hover:bg-accent/60")}
            >
              <CompanyLogo domain={c.domain} name={c.name} size={18} />
              <span className="truncate flex-1">{c.name}</span>
              <span className="text-[10px] text-muted-foreground shrink-0">{c.people_count}p</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
