"use client";

import { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import { CompanyLogo } from "@/components/company-logo";
import { api, type CompanyRow } from "@/lib/api";

/** Debounced company-search dropdown. Renders `trigger`; picking a result
 *  calls onPick and closes. Reused for the opportunity company link + merge. */
export function CompanyPicker({
  trigger, onPick, align = "left", excludeId,
}: {
  trigger: React.ReactNode;
  onPick: (c: CompanyRow) => void | Promise<void>;
  align?: "left" | "right";
  excludeId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<CompanyRow[]>([]);
  const [busy, setBusy] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancel = false;
    const t = setTimeout(async () => {
      try {
        const rows = await api.listCompanies({ q: q.trim() || undefined, limit: 10 });
        if (!cancel) setResults(rows.filter((r) => r.id !== excludeId).slice(0, 8));
      } catch { /* ignore */ }
    }, 200);
    return () => { cancel = true; clearTimeout(t); };
  }, [q, open, excludeId]);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) { if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false); }
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  async function pick(c: CompanyRow) {
    setBusy(true);
    try { await onPick(c); setOpen(false); setQ(""); } finally { setBusy(false); }
  }

  return (
    <span className="relative inline-block" ref={boxRef}>
      <button type="button" onClick={() => setOpen((o) => !o)} className="inline-flex">{trigger}</button>
      {open && (
        <div className={`absolute z-30 mt-1 ${align === "right" ? "right-0" : "left-0"} w-64 rounded-md border border-border bg-popover shadow-lg p-2`}>
          <div className="flex items-center gap-1.5 px-1.5 py-1 rounded border border-border bg-background">
            <Search className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search companies…" className="bg-transparent outline-none text-sm w-full" />
          </div>
          <div className="mt-1 max-h-56 overflow-auto">
            {results.length === 0 ? (
              <p className="text-[11px] text-muted-foreground px-1.5 py-1.5">No matches.</p>
            ) : results.map((c) => (
              <button key={c.id} type="button" disabled={busy} onClick={() => pick(c)}
                className="w-full text-left px-1.5 py-1.5 rounded hover:bg-accent text-sm flex items-center gap-2">
                <CompanyLogo domain={c.domain} name={c.name} size={18} />
                <span className="truncate flex-1">{c.name}</span>
                <span className="text-[10px] text-muted-foreground shrink-0">{c.people_count}p</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </span>
  );
}
