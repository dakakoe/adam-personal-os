"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronRight, FileText, Pin, Plus, Search, ArrowLeft, X } from "lucide-react";
import { api, type NoteRow, type NoteSearchHit, type NoteSummary } from "@/lib/api";
import { NoteEditor } from "@/components/note-editor";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

type Tree = { roots: NoteSummary[]; children: Map<string, NoteSummary[]> };

function buildTree(notes: NoteSummary[]): Tree {
  const byId = new Set(notes.map((n) => n.id));
  const children = new Map<string, NoteSummary[]>();
  const roots: NoteSummary[] = [];
  for (const n of notes) {
    // A parent outside the list (deleted, filtered) makes the page a root.
    if (n.parent_id && byId.has(n.parent_id)) {
      const arr = children.get(n.parent_id) ?? [];
      arr.push(n); children.set(n.parent_id, arr);
    } else roots.push(n);
  }
  return { roots, children };
}

function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "now";
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

export function NotesClient() {
  const router = useRouter();
  const params = useSearchParams();
  const selectedId = params.get("n");

  const [notes, setNotes] = useState<NoteSummary[] | null>(null);
  const [current, setCurrent] = useState<NoteRow | null>(null);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<NoteSearchHit[] | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadList = useCallback(async () => {
    try { setNotes(await api.listNotes()); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed to load notes"); setNotes([]); }
  }, []);
  useEffect(() => { void loadList(); }, [loadList]);

  const open = useCallback((id: string | null) => {
    router.replace(id ? `/notes?n=${id}` : "/notes");
  }, [router]);

  const loadNote = useCallback(async (id: string) => {
    try { setCurrent(await api.getNote(id)); }
    catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to open note");
      open(null);
    }
  }, [open]);

  useEffect(() => {
    if (!selectedId) { setCurrent(null); return; }
    if (current?.id !== selectedId) void loadNote(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  // Search: debounced, server-side; the tree comes back when the box clears.
  useEffect(() => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    const term = q.trim();
    if (!term) { setHits(null); return; }
    searchTimer.current = setTimeout(async () => {
      try { setHits(await api.searchNotes(term, 30)); }
      catch { setHits([]); }
    }, 250);
  }, [q]);

  async function create(parent_id: string | null = null) {
    try {
      const n = await api.createNote({ title: "", parent_id });
      setNotes((prev) => [n, ...(prev ?? [])]);
      if (parent_id) setCollapsed((c) => { const s = new Set(c); s.delete(parent_id); return s; });
      setCurrent(n);
      open(n.id);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Could not create page"); }
  }

  const onSaved = useCallback((saved: NoteRow) => {
    setNotes((prev) => {
      if (!prev) return prev;
      const summary: NoteSummary = { ...saved, snippet: saved.body_md.slice(0, 160) };
      const i = prev.findIndex((n) => n.id === saved.id);
      const next = i >= 0 ? [...prev.slice(0, i), summary, ...prev.slice(i + 1)] : [summary, ...prev];
      return next;
    });
    setCurrent((cur) => (cur && cur.id === saved.id ? { ...cur, ...saved } : cur));
  }, []);

  const onDeleted = useCallback((n: NoteRow) => {
    setNotes((prev) => prev ? prev.filter((x) => x.id !== n.id).map((x) => x.parent_id === n.id ? { ...x, parent_id: n.parent_id } : x) : prev);
    setCurrent(null);
    open(n.parent_id);
    toast.success("Page deleted");
  }, [open]);

  const tree = useMemo(() => buildTree(notes ?? []), [notes]);

  function toggle(id: string) {
    setCollapsed((c) => { const s = new Set(c); if (s.has(id)) s.delete(id); else s.add(id); return s; });
  }

  function Node({ n, depth }: { n: NoteSummary; depth: number }) {
    const kids = tree.children.get(n.id) ?? [];
    const isOpen = !collapsed.has(n.id);
    const active = n.id === selectedId;
    return (
      <li>
        <div className={cn("group flex items-center gap-1 rounded-md pr-1 text-sm",
          active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")}
          style={{ paddingLeft: 4 + depth * 14 }}>
          <button type="button" onClick={() => toggle(n.id)} tabIndex={-1}
            className={cn("grid place-items-center h-6 w-5 shrink-0 rounded hover:bg-accent", kids.length === 0 && "invisible")}>
            <ChevronRight className={cn("h-3 w-3 transition-transform", isOpen && "rotate-90")} />
          </button>
          <button type="button" onClick={() => open(n.id)} className="flex-1 min-w-0 text-left py-1 flex items-center gap-1.5">
            {n.pinned ? <Pin className="h-3 w-3 shrink-0 text-primary" /> : <FileText className="h-3 w-3 shrink-0 opacity-60" />}
            <span className="truncate">{n.title || <span className="italic opacity-60">Untitled</span>}</span>
          </button>
          <button type="button" onClick={() => create(n.id)} title="New subpage"
            className="grid place-items-center h-6 w-6 rounded opacity-0 group-hover:opacity-100 hover:bg-accent shrink-0">
            <Plus className="h-3 w-3" />
          </button>
        </div>
        {isOpen && kids.length > 0 && (
          <ul>{kids.map((k) => <Node key={k.id} n={k} depth={depth + 1} />)}</ul>
        )}
      </li>
    );
  }

  const list = (
    <aside className={cn("w-full md:w-72 lg:w-80 shrink-0 border-r border-border flex flex-col min-h-0",
      selectedId && "hidden md:flex")}>
      <div className="p-3 flex items-center gap-2 border-b border-border">
        <div className="relative flex-1">
          <Search className="h-3.5 w-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search pages"
            className="h-8 w-full pl-7 pr-7 text-sm rounded-md border border-border bg-background outline-none focus:border-primary/60" />
          {q && (
            <button type="button" onClick={() => setQ("")} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <button type="button" onClick={() => create(null)} title="New page"
          className="h-8 px-2.5 inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:opacity-90">
          <Plus className="h-3.5 w-3.5" /> New
        </button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-2">
        {hits !== null ? (
          hits.length === 0 ? (
            <p className="px-2 py-6 text-xs text-muted-foreground text-center">Nothing matches “{q.trim()}”.</p>
          ) : (
            <ul className="space-y-0.5">
              {hits.map((h) => (
                <li key={h.id}>
                  <button type="button" onClick={() => open(h.id)}
                    className={cn("w-full text-left rounded-md px-2 py-1.5 hover:bg-accent/60",
                      h.id === selectedId && "bg-accent")}>
                    <div className="text-sm truncate">{h.title || <span className="italic opacity-60">Untitled</span>}</div>
                    {h.snippet && (
                      <div className="text-[11px] text-muted-foreground line-clamp-2 [&_b]:text-foreground"
                        dangerouslySetInnerHTML={{ __html: h.snippet.replace(/</g, "&lt;").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>") }} />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )
        ) : notes === null ? (
          <p className="px-2 py-6 text-xs text-muted-foreground">Loading…</p>
        ) : notes.length === 0 ? (
          <div className="px-3 py-10 text-center text-xs text-muted-foreground">
            No pages yet. <button type="button" onClick={() => create(null)} className="text-foreground underline underline-offset-2">Start one</button> —
            ideas, a running log, a plan. The assistant can read and add to them.
          </div>
        ) : (
          <ul className="space-y-0.5">{tree.roots.map((n) => <Node key={n.id} n={n} depth={0} />)}</ul>
        )}
      </div>
      {notes && notes.length > 0 && (
        <div className="px-3 py-1.5 border-t border-border text-[10px] text-muted-foreground/70">
          {notes.length} page{notes.length === 1 ? "" : "s"} · last edit {ago(notes.reduce((m, n) => (n.updated_at > m ? n.updated_at : m), notes[0].updated_at))} ago
        </div>
      )}
    </aside>
  );

  return (
    <div className="flex h-[calc(100dvh-3rem)] md:h-dvh min-h-0">
      {list}
      <section className={cn("flex-1 min-w-0 min-h-0 flex flex-col", !selectedId && "hidden md:flex")}>
        {selectedId && (
          <button type="button" onClick={() => open(null)}
            className="md:hidden inline-flex items-center gap-1 text-xs text-muted-foreground px-3 py-2 border-b border-border">
            <ArrowLeft className="h-3.5 w-3.5" /> All pages
          </button>
        )}
        {current && current.id === selectedId ? (
          <NoteEditor note={current} onSaved={onSaved} onDeleted={onDeleted} onReload={loadNote} />
        ) : selectedId ? (
          <p className="p-6 text-sm text-muted-foreground">Opening…</p>
        ) : (
          <div className="flex-1 grid place-items-center text-center p-6">
            <div className="max-w-sm text-sm text-muted-foreground">
              <FileText className="h-8 w-8 mx-auto mb-3 opacity-40" />
              Pick a page on the left or start a new one. Everything autosaves; markdown shortcuts work while you type.
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
