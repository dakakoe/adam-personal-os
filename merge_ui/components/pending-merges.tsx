"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { GitMerge, ChevronRight, AlertTriangle, X, EyeOff } from "lucide-react";
import { api, type PendingMergeForPerson } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

/**
 * Shows pending merge candidates that involve this person. Lets the user
 * approve via the merge queue (jump link), reject inline (× per row), or
 * reject all at once (header button + AlertDialog confirm).
 */
export function PendingMerges({ personId }: { personId: string }) {
  const [items, setItems] = useState<PendingMergeForPerson[] | null>(null);
  const [confirmRejectAll, setConfirmRejectAll] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.listPendingForPerson(personId)
      .then((r) => { if (!cancelled) setItems(r); })
      .catch(() => { if (!cancelled) setItems([]); });
    return () => { cancelled = true; };
  }, [personId]);

  async function rejectOne(m: PendingMergeForPerson) {
    // Optimistic: drop from UI immediately, restore on error
    setItems((xs) => (xs ?? []).filter((x) => x.id !== m.id));
    try {
      await api.decideCandidate(m.id, { decision: "reject" });
      toast.success(`Rejected: ${m.other_display_name}`);
    } catch (e) {
      setItems((xs) => [...(xs ?? []), m]);
      toast.error(e instanceof Error ? e.message : "Reject failed");
    }
  }

  async function rejectAll() {
    if (!items || items.length === 0) return;
    setBusy(true);
    const snapshot = items;
    setItems([]);
    let failed = 0;
    await Promise.all(
      snapshot.map(async (m) => {
        try { await api.decideCandidate(m.id, { decision: "reject" }); }
        catch { failed += 1; }
      }),
    );
    if (failed === 0) {
      toast.success(`Rejected all ${snapshot.length}`);
    } else {
      toast.error(`Rejected ${snapshot.length - failed}, ${failed} failed`);
    }
    setBusy(false);
    setConfirmRejectAll(false);
  }

  if (items === null || items.length === 0) return null;

  return (
    <>
      <section className="rounded-lg border border-primary/30 bg-primary/5 p-4 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-primary" />
            <h3 className="text-sm font-medium text-foreground">
              {items.length} possible match{items.length === 1 ? "" : "es"} waiting for review
            </h3>
          </div>
          {items.length > 1 && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setConfirmRejectAll(true)}
              className="h-7 text-xs text-muted-foreground hover:text-foreground"
            >
              <EyeOff className="h-3.5 w-3.5 mr-1" />
              Reject all
            </Button>
          )}
        </div>
        <p className="text-xs text-muted-foreground -mt-1">
          These people might be the same person — open in the queue to merge, or × to reject if they&apos;re unrelated.
        </p>
        <ul className="space-y-1.5">
          {items.map((m) => (
            <li
              key={m.id}
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-md",
                "bg-card/60 border border-border",
              )}
            >
              <Badge
                variant={m.confidence === "high" ? "default" : "secondary"}
                className="h-5 text-[10px] shrink-0"
              >
                {m.confidence}
              </Badge>
              <span className="text-[10px] font-mono text-muted-foreground shrink-0 w-20 truncate">
                {m.source}
              </span>
              <div className="min-w-0 flex-1 text-sm flex items-center gap-2">
                <span className="text-muted-foreground">↔</span>
                <span className="font-medium truncate">{m.other_display_name}</span>
                <span className="text-xs text-muted-foreground tabular shrink-0">
                  · {m.other_interactions.toLocaleString()} msgs · {m.other_identity_count} ids
                </span>
              </div>
              <button
                type="button"
                onClick={() => rejectOne(m)}
                className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-accent hover:text-destructive transition-colors"
                title="Not the same person — reject"
              >
                <X className="h-3.5 w-3.5" />
              </button>
              <Link
                href={`/merge?focus=${m.id}`}
                className={cn(
                  "inline-flex items-center gap-1.5 h-7 px-2 rounded text-xs",
                  "text-muted-foreground hover:bg-accent hover:text-foreground transition-colors",
                )}
                title="Open in merge queue"
              >
                <GitMerge className="h-3.5 w-3.5" />
                Review
                <ChevronRight className="h-3 w-3" />
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <AlertDialog open={confirmRejectAll} onOpenChange={setConfirmRejectAll}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reject all {items.length} pending matches?</AlertDialogTitle>
            <AlertDialogDescription className="space-y-2">
              <span className="block">
                Each will be marked as &quot;not the same person&quot; and stop being suggested for this contact.
              </span>
              <span className="block text-xs text-muted-foreground">
                Reversible via SQL — <span className="font-mono">UPDATE memory.merge_candidate SET status=&apos;pending&apos;</span> on the row(s).
              </span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={rejectAll} disabled={busy}>
              {busy ? "Rejecting…" : `Reject all ${items.length}`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
