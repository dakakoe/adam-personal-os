"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, X, CheckSquare, Target, UserPlus, Sparkles, Pencil, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProjectBadge } from "@/components/project-badge";
import { StageBadge } from "@/components/stage-badge";
import { api, type SuggestionRow, type PersonRow } from "@/lib/api";
import { formatRelativeDate, formatDiscussed, cn } from "@/lib/utils";
import { toast } from "sonner";

/** Inline person search to re-point a mis-resolved suggestion. Opens a
 *  small dropdown; debounced search over /api/persons; picking one calls
 *  the reassign endpoint and refreshes. */
function ReassignControl({ suggestion, onDone }: { suggestion: SuggestionRow; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<PersonRow[]>([]);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

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

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  async function pick(p: PersonRow) {
    setSaving(true);
    try {
      await api.reassignSuggestionPerson(suggestion.id, p.person_id);
      toast.success(`Re-pointed to ${p.display_name}`);
      setOpen(false); setQ("");
      onDone();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Reassign failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <span className="relative inline-block" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground/70 hover:text-foreground"
        title="Wrong person? Re-point this suggestion"
      >
        <Pencil className="h-3 w-3" /> {suggestion.person_id ? "change" : "set person"}
      </button>
      {open && (
        <div className="absolute z-20 mt-1 left-0 w-64 rounded-md border border-border bg-popover shadow-lg p-2">
          <div className="flex items-center gap-1.5 px-1.5 py-1 rounded border border-border bg-background">
            <Search className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
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
                  key={p.person_id}
                  type="button"
                  disabled={saving}
                  onClick={() => pick(p)}
                  className="w-full text-left px-1.5 py-1.5 rounded hover:bg-accent text-sm flex items-baseline justify-between gap-2"
                >
                  <span className="truncate">{p.display_name}</span>
                  <span className="text-[10px] text-muted-foreground tabular shrink-0">{p.total_interactions} msgs</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </span>
  );
}

const KIND_ICON = {
  task: CheckSquare,
  opportunity: Target,
  person_mention: UserPlus,
} as const;

const KIND_LABEL = {
  task: "Task",
  opportunity: "Opportunity",
  person_mention: "Follow-up",
} as const;

const CONF_STYLE: Record<string, string> = {
  high: "text-emerald-400 border-emerald-500/40",
  medium: "text-amber-400 border-amber-500/40",
  low: "text-muted-foreground border-border",
};

export function SuggestionsInbox({ initial }: { initial: SuggestionRow[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [, startTransition] = useTransition();

  const visible = initial.filter((s) => !hidden.has(s.id));

  function mark(id: string, on: boolean) {
    setBusy((p) => {
      const n = new Set(p);
      on ? n.add(id) : n.delete(id);
      return n;
    });
  }

  async function accept(s: SuggestionRow) {
    mark(s.id, true);
    try {
      const r = await api.acceptSuggestion(s.id);
      setHidden((h) => new Set(h).add(s.id));
      toast.success(
        r.entity_kind === "opportunity"
          ? "Opportunity created"
          : "Task created",
      );
      startTransition(() => router.refresh());
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Accept failed");
    } finally {
      mark(s.id, false);
    }
  }

  async function dismiss(s: SuggestionRow) {
    mark(s.id, true);
    try {
      await api.dismissSuggestion(s.id);
      setHidden((h) => new Set(h).add(s.id));
      toast.success("Dismissed");
      startTransition(() => router.refresh());
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Dismiss failed");
    } finally {
      mark(s.id, false);
    }
  }

  if (visible.length === 0) {
    return (
      <div className="rounded-md border border-border bg-card/40 p-10 text-center">
        <Sparkles className="h-6 w-6 mx-auto mb-2 text-muted-foreground/50" />
        <p className="text-sm text-muted-foreground">
          Inbox zero. New meeting recaps will surface task + opportunity
          suggestions here for your review.
        </p>
      </div>
    );
  }

  return (
    <ul className="space-y-2">
      {visible.map((s) => {
        const Icon = KIND_ICON[s.kind];
        const isBusy = busy.has(s.id);
        const pending = s.status === "pending";
        return (
          <li
            key={s.id}
            className="rounded-lg border border-border bg-card/40 p-3 sm:p-4"
          >
            <div className="flex items-start gap-3">
              <span className="grid place-items-center h-7 w-7 rounded-md bg-secondary/60 shrink-0 text-muted-foreground mt-0.5">
                <Icon className="h-3.5 w-3.5" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="text-[10px] font-mono uppercase text-muted-foreground">{KIND_LABEL[s.kind]}</span>
                  <span className={cn("text-[10px] font-mono px-1 rounded border", CONF_STYLE[s.confidence] ?? CONF_STYLE.low)}>
                    {s.confidence}
                  </span>
                  {s.suggested_stage && <StageBadge stage={s.suggested_stage} />}
                  <ProjectBadge slug={s.project_slug} name={s.project_name} color={s.project_color} />
                </div>
                <p className="font-medium mt-1 break-words">{s.title}</p>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                  {s.person_id && s.person_name && (
                    <Link href={`/persons/${s.person_id}`} className="text-sky-400 hover:text-sky-300 font-medium">
                      {s.kind === "opportunity" ? "with" : "→"} {s.person_name}
                    </Link>
                  )}
                  {!s.person_id && s.person_name_raw && (
                    <span className="italic">mentioned: {s.person_name_raw} (not in DB)</span>
                  )}
                  {pending && (s.kind !== "task" || s.person_id || s.person_name_raw) && (
                    <ReassignControl suggestion={s} onDone={() => startTransition(() => router.refresh())} />
                  )}
                  {s.estimated_value && <span>{s.estimated_value}</span>}
                  {s.due_date && <span>due {s.due_date}</span>}
                  {s.detail && <span className="text-muted-foreground/80">{s.detail}</span>}
                </div>
                <div className="mt-1 text-[11px] text-muted-foreground/60">
                  {s.source_kind === "granola" && s.recap_title
                    ? <>from meeting “{s.recap_title}”</>
                    : s.source_kind === "interaction"
                      ? <>from chat/email{s.person_name ? ` with ${s.person_name}` : ""}</>
                      : <>{s.source_kind}</>}
                  {(() => {
                    const discussed = s.context_at ?? s.recap_date;
                    return discussed
                      ? <> · <span title={`discussed ${formatRelativeDate(discussed)}`}>discussed {formatDiscussed(discussed)}</span></>
                      : <> · added {formatRelativeDate(s.created_at)}</>;
                  })()}
                </div>
              </div>
              {pending ? (
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    size="sm" variant="outline" disabled={isBusy}
                    onClick={() => accept(s)}
                    className="h-7 px-2 text-xs text-emerald-400 hover:text-emerald-foreground hover:bg-emerald-600 border-emerald-500/40"
                  >
                    <Check className="h-3.5 w-3.5 sm:mr-1" />
                    <span className="hidden sm:inline">Accept</span>
                  </Button>
                  <button
                    type="button" disabled={isBusy}
                    onClick={() => dismiss(s)}
                    title="Dismiss"
                    className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-accent hover:text-destructive"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ) : (
                <span className={cn(
                  "text-[10px] font-mono uppercase shrink-0 px-1.5 py-0.5 rounded border",
                  s.status === "accepted" ? "text-emerald-400 border-emerald-500/40" : "text-muted-foreground border-border",
                )}>
                  {s.status}
                </span>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
