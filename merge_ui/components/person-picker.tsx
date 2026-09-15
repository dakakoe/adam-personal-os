"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Search } from "lucide-react";
import { api, type PersonRow } from "@/lib/api";

/**
 * Debounced people-search dropdown. Renders `trigger` (a button); clicking
 * opens a small popover with a search box. Picking a result calls onPick
 * and closes. Reused for task assignee + people-involved + participants.
 *
 * The popover is rendered in a PORTAL so it floats above overflow-clipped
 * containers instead of being clipped by an ancestor.
 *
 * Inside a modal dialog the portal goes INTO the dialog, not to <body>: a
 * Radix modal blocks pointer events and traps focus everywhere outside its
 * content, so a panel on <body> can be seen but never clicked or typed into.
 * The dialog is centred with a transform, which makes it the containing block
 * for its positioned children — so there the panel is placed relative to the
 * dialog's own box rather than the viewport.
 */
export function PersonPicker({
  trigger, onPick, align = "left",
}: {
  trigger: React.ReactNode;
  onPick: (p: PersonRow) => void | Promise<void>;
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<PersonRow[]>([]);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pos, setPos] = useState<{
    position: "fixed" | "absolute"; left: number; width: number; top?: number; bottom?: number;
  } | null>(null);
  // The modal dialog the trigger sits in, if any — the portal target.
  const [host, setHost] = useState<HTMLElement | null>(null);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const PANEL_W = 256;   // w-64
  const PANEL_MAXH = 280;

  function reposition() {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const spaceBelow = window.innerHeight - r.bottom;
    const drop: "down" | "up" = spaceBelow < PANEL_MAXH && r.top > spaceBelow ? "up" : "down";
    const left = align === "right"
      ? Math.max(8, r.right - PANEL_W)
      : Math.min(r.left, window.innerWidth - PANEL_W - 8);
    // Only Radix dialog content: side panels are not focus traps, and some of
    // them clip overflow, which is exactly what the <body> portal avoids.
    const dialog = el.closest<HTMLElement>('[data-slot="dialog-content"]');
    setHost(dialog);
    if (dialog) {
      const d = dialog.getBoundingClientRect();
      setPos({
        position: "absolute", width: PANEL_W,
        left: left - d.left + dialog.scrollLeft,
        ...(drop === "down"
          ? { top: r.bottom + 4 - d.top + dialog.scrollTop }
          : { bottom: d.bottom - r.top + 4 - dialog.scrollTop }),
      });
      return;
    }
    setPos({
      position: "fixed", width: PANEL_W, left,
      ...(drop === "down" ? { top: r.bottom + 4 } : { bottom: window.innerHeight - r.top + 4 }),
    });
  }

  useLayoutEffect(() => {
    if (open) reposition();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open || q.trim().length < 2) { setResults([]); return; }
    let cancel = false;
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        const rows = await api.listPersons({ q: q.trim(), limit: 8 });
        if (!cancel) setResults(rows);
      } catch { /* ignore */ } finally { if (!cancel) setSearching(false); }
    }, 250);
    return () => { cancel = true; clearTimeout(t); };
  }, [q, open]);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") setOpen(false); }
    function onReflow() { reposition(); }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onReflow);
    window.addEventListener("scroll", onReflow, true);   // capture: catch scrolls in any container
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onReflow);
      window.removeEventListener("scroll", onReflow, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function pick(p: PersonRow) {
    setBusy(true);
    try {
      await onPick(p);
      setOpen(false); setQ("");
    } finally {
      setBusy(false);
    }
  }

  const panel = open && pos ? createPortal(
    <div
      ref={panelRef}
      style={{
        position: pos.position, left: pos.left, width: pos.width,
        top: pos.top, bottom: pos.bottom,
      }}
      className="z-[60] rounded-md border border-border bg-popover shadow-lg p-2"
    >
      <div className="flex items-center gap-1.5 px-1.5 py-1 rounded border border-border bg-background">
        <Search className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        <input
          autoFocus value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Search people…"
          className="bg-transparent outline-none text-sm w-full"
        />
      </div>
      <div className="mt-1 max-h-56 overflow-auto">
        {q.trim().length < 2 ? (
          <p className="text-[11px] text-muted-foreground px-1.5 py-1.5">Type at least 2 characters.</p>
        ) : searching ? (
          <p className="text-[11px] text-muted-foreground px-1.5 py-1.5">Searching…</p>
        ) : results.length === 0 ? (
          <p className="text-[11px] text-muted-foreground px-1.5 py-1.5">No matches.</p>
        ) : (
          results.map((p) => (
            <button
              key={p.person_id} type="button" disabled={busy}
              onClick={() => pick(p)}
              className="w-full text-left px-1.5 py-1.5 rounded hover:bg-accent text-sm flex items-baseline justify-between gap-2"
            >
              <span className="min-w-0 flex-1">
                <span className="truncate block">{p.display_name}</span>
                <span className="text-[10px] text-muted-foreground truncate block">
                  {p.email ?? "no email"}
                </span>
              </span>
              <span className="text-[10px] text-muted-foreground tabular shrink-0">{p.total_interactions} msgs</span>
            </button>
          ))
        )}
      </div>
    </div>,
    host ?? document.body,
  ) : null;

  return (
    <span className="relative inline-block" ref={triggerRef}>
      <button type="button" onClick={() => setOpen((o) => !o)} className="inline-flex">
        {trigger}
      </button>
      {panel}
    </span>
  );
}
