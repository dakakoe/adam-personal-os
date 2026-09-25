"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Trash2, ExternalLink, CheckSquare, Square, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { PersonAvatar } from "@/components/person-avatar";
import { api, type CleanupCandidate } from "@/lib/api";
import { formatRelativeDate } from "@/lib/utils";
import { toast } from "sonner";

/**
 * Cleanup queue UI. The server fetches the candidate slice; this client
 * component owns the per-row checkbox state + the per-row and bulk
 * delete actions. Single confirm AlertDialog handles both 1 and N
 * deletions to keep destructive actions explicit.
 */
export function CleanupList({ rows }: { rows: CleanupCandidate[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmTarget, setConfirmTarget] = useState<
    null | { kind: "single"; row: CleanupCandidate }
        | { kind: "bulk"; ids: string[] }
  >(null);
  const [busy, setBusy] = useState(false);
  const [hiddenIds, setHiddenIds] = useState<Set<string>>(new Set());
  // Track recently-deleted ids so we can show an inline Undo for ~10s
  // without re-querying the server; restore POSTs the same id back.
  const [recentlyDeleted, setRecentlyDeleted] = useState<
    { id: string; display_name: string; at: number }[]
  >([]);

  const visible = useMemo(
    () => rows.filter((r) => !hiddenIds.has(r.person_id)),
    [rows, hiddenIds],
  );
  const allSelected = visible.length > 0 && visible.every((r) => selected.has(r.person_id));
  const someSelected = !allSelected && visible.some((r) => selected.has(r.person_id));

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function toggleAll() {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(visible.map((r) => r.person_id)));
    }
  }

  async function performDelete(ids: string[], nameForToast?: string) {
    setBusy(true);
    try {
      const { deleted } = await api.cleanupDeleteBatch(ids);
      // Optimistically hide them from this view and bank for undo
      setHiddenIds((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.add(id));
        return next;
      });
      setSelected(new Set());
      const banked = ids
        .map((id) => rows.find((r) => r.person_id === id))
        .filter((r): r is CleanupCandidate => !!r)
        .map((r) => ({ id: r.person_id, display_name: r.display_name, at: Date.now() }));
      setRecentlyDeleted((prev) => [...banked, ...prev].slice(0, 10));
      toast.success(
        ids.length === 1
          ? `Deleted ${nameForToast ?? "person"}`
          : `Deleted ${deleted} ${deleted === 1 ? "person" : "people"}`,
      );
      setConfirmTarget(null);
      // Refresh in background so the count + next page are up to date
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  async function undoOne(id: string) {
    try {
      await api.restorePerson(id);
      setRecentlyDeleted((prev) => prev.filter((r) => r.id !== id));
      setHiddenIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      toast.success("Restored");
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Restore failed");
    }
  }

  return (
    <>
      {/* Bulk-action toolbar */}
      <div className="mb-3 flex items-center justify-between gap-2 rounded-md border border-border bg-card/40 px-3 py-2">
        <button
          type="button"
          onClick={toggleAll}
          className="inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground min-w-0"
        >
          {allSelected ? (
            <CheckSquare className="h-4 w-4 text-primary shrink-0" />
          ) : someSelected ? (
            <CheckSquare className="h-4 w-4 text-primary opacity-60 shrink-0" />
          ) : (
            <Square className="h-4 w-4 shrink-0" />
          )}
          <span className="truncate">
            {selected.size > 0
              ? `${selected.size} selected`
              : <>Select all <span className="hidden sm:inline">({visible.length} on this page)</span></>}
          </span>
        </button>
        <Button
          size="sm"
          variant="destructive"
          disabled={selected.size === 0 || busy}
          onClick={() =>
            setConfirmTarget({ kind: "bulk", ids: Array.from(selected) })
          }
          className="h-7 px-2 sm:px-3 text-xs shrink-0"
        >
          <Trash2 className="h-3.5 w-3.5 sm:mr-1" />
          <span className="hidden sm:inline">Delete selected</span>
          <span className="sm:hidden">Delete</span>
        </Button>
      </div>

      {/* Recently-deleted undo strip */}
      {recentlyDeleted.length > 0 && (
        <div className="mb-3 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 space-y-1 text-xs">
          <div className="text-emerald-500/80 font-medium">
            Recently deleted this session — click Undo to restore:
          </div>
          <ul className="space-y-0.5">
            {recentlyDeleted.map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-3">
                <span className="truncate">{r.display_name}</span>
                <button
                  type="button"
                  onClick={() => undoOne(r.id)}
                  className="inline-flex items-center gap-1 text-emerald-500 hover:text-foreground"
                >
                  <Undo2 className="h-3 w-3" />
                  Undo
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Candidates list */}
      <ul className="divide-y divide-border rounded-md border border-border overflow-hidden bg-card/40">
        {visible.length === 0 ? (
          <li className="p-10 text-center text-sm text-muted-foreground">
            No cleanup candidates on this page.
          </li>
        ) : (
          visible.map((r) => {
            const checked = selected.has(r.person_id);
            return (
              <li
                key={r.person_id}
                className={`flex items-center gap-2 sm:gap-3 px-3 sm:px-4 py-2.5 transition-colors ${checked ? "bg-accent/40" : "hover:bg-accent/20"}`}
              >
                <button
                  type="button"
                  onClick={() => toggle(r.person_id)}
                  aria-label={checked ? "Deselect" : "Select"}
                  className="text-muted-foreground hover:text-foreground shrink-0"
                >
                  {checked ? <CheckSquare className="h-4 w-4 text-primary" /> : <Square className="h-4 w-4" />}
                </button>
                <PersonAvatar
                  personId={r.person_id}
                  displayName={r.display_name}
                  className="h-7 w-7 sm:h-8 sm:w-8 shrink-0"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="font-medium truncate">{r.display_name}</span>
                    {r.by_email && (
                      <span className="text-[10px] font-mono px-1 rounded border border-border bg-secondary/40 text-muted-foreground">
                        email
                      </span>
                    )}
                    {r.by_name && (
                      <span className="text-[10px] font-mono px-1 rounded border border-border bg-secondary/40 text-muted-foreground">
                        name
                      </span>
                    )}
                  </div>
                  {r.sample_email && (
                    <div className="text-xs text-muted-foreground truncate font-mono">
                      {r.sample_email}
                    </div>
                  )}
                  {/* Mobile-only: msgs + last_at inline under email so they
                      don't get crushed by the right-aligned column. */}
                  <div className="sm:hidden text-[11px] text-muted-foreground tabular mt-0.5">
                    <span className="text-foreground font-medium">{r.msgs.toLocaleString()}</span> msgs · {formatRelativeDate(r.last_at)}
                  </div>
                </div>
                {/* Desktop-only: right-aligned msgs + last_at column. */}
                <div className="hidden sm:block text-right text-xs text-muted-foreground tabular shrink-0">
                  <div className="text-foreground font-medium">
                    {r.msgs.toLocaleString()}
                  </div>
                  <div>{formatRelativeDate(r.last_at)}</div>
                </div>
                <Link
                  href={`/persons/${r.person_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Open profile in new tab"
                  className="hidden sm:grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                </Link>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => setConfirmTarget({ kind: "single", row: r })}
                  className="h-7 px-2 sm:px-3 text-xs text-destructive hover:text-destructive-foreground hover:bg-destructive border-destructive/40 shrink-0"
                  aria-label={`Delete ${r.display_name}`}
                >
                  <Trash2 className="h-3.5 w-3.5 sm:mr-1" />
                  <span className="hidden sm:inline">Delete</span>
                </Button>
              </li>
            );
          })
        )}
      </ul>

      {/* Confirm dialog (handles both single and bulk) */}
      <AlertDialog open={!!confirmTarget} onOpenChange={(o) => { if (!o) setConfirmTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmTarget?.kind === "single"
                ? `Delete ${confirmTarget.row.display_name}?`
                : `Delete ${confirmTarget?.kind === "bulk" ? confirmTarget.ids.length : 0} people?`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              Soft-delete — the row + identities + summary + photo stay
              in the database, but disappear from every list. You can
              undo from the strip above for the next few clicks, or
              restore via SQL later
              (<code className="font-mono text-[10px]">UPDATE canonical.person SET deleted_at = NULL WHERE id = …</code>).
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={busy}
              onClick={() => {
                if (!confirmTarget) return;
                if (confirmTarget.kind === "single") {
                  performDelete([confirmTarget.row.person_id], confirmTarget.row.display_name);
                } else {
                  performDelete(confirmTarget.ids);
                }
              }}
              className="bg-destructive hover:bg-destructive/90"
            >
              {busy ? "Deleting…" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
