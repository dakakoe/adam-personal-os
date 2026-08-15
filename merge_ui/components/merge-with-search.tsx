"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { GitMerge, Search, ChevronLeft } from "lucide-react";
import { api, type PersonRow } from "@/lib/api";
import { toast } from "sonner";
import { PersonAvatar } from "./person-avatar";

/**
 * Manual escape hatch: when you eyeball-identify a nameless row (e.g. a
 * Telegram-only "Telegram user 119485045" that you happen to know is
 * the user Repko) the name-based SimilarPersons section can't surface the
 * match. This widget lets you search the whole person directory and
 * trigger a direct merge with the existing canonical row.
 *
 * Two-phase Dialog: search → pick a target → confirm winner direction.
 * Default winner is "other", which fits the most common case (the row
 * you're looking AT is the nameless/mystery side; the row you're
 * searching FOR is the named, canonical one).
 */
export function MergeWithSearch({
  personId,
  thisDisplayName,
}: {
  personId: string;
  thisDisplayName: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<PersonRow[]>([]);
  const [target, setTarget] = useState<PersonRow | null>(null);
  // "other" = the row we're searching FOR keeps its identity, this page
  // merges INTO it. That's almost always what you want for nameless rows.
  const [winner, setWinner] = useState<"this" | "other">("other");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);

  // Debounced search: only while open and no target picked yet. Same 80ms
  // cadence as the global command palette for muscle-memory consistency.
  useEffect(() => {
    if (!open || target) return;
    setLoading(true);
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const data = await api.listPersons({ q: q || undefined, limit: 20 });
        if (!ctrl.signal.aborted) {
          // Hide self — merging with yourself is a 400.
          setRows(data.filter((r) => r.person_id !== personId));
        }
      } catch {
        if (!ctrl.signal.aborted) setRows([]);
      } finally {
        if (!ctrl.signal.aborted) setLoading(false);
      }
    }, 80);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [open, q, target, personId]);

  // Reset everything when the dialog closes so the next open is clean.
  useEffect(() => {
    if (!open) {
      setQ("");
      setRows([]);
      setTarget(null);
      setWinner("other");
      setBusy(false);
      setLoading(false);
    }
  }, [open]);

  async function confirmMerge() {
    if (!target) return;
    setBusy(true);
    try {
      await api.directMerge(personId, target.person_id, winner);
      toast.success(
        winner === "this"
          ? `Merged ${target.display_name} into this person`
          : `This person merged into ${target.display_name}`,
      );
      setOpen(false);
      if (winner === "other") {
        router.replace(`/persons/${target.person_id}`);
      } else {
        router.refresh();
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Merge failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-card/40 px-4 py-2.5">
        <div className="text-xs text-muted-foreground">
          Know this is someone already in your directory?
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setOpen(true)}
          className="h-7 text-xs shrink-0"
        >
          <Search className="h-3.5 w-3.5 mr-1.5" />
          Merge with another person…
        </Button>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-lg">
          {!target ? (
            <>
              <DialogHeader>
                <DialogTitle>Find a person to merge with</DialogTitle>
                <DialogDescription>
                  Search by name. Pick the canonical person this row should
                  collapse into.
                </DialogDescription>
              </DialogHeader>
              <Input
                autoFocus
                placeholder="Search by name…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              <div className="max-h-80 overflow-y-auto -mx-1">
                {rows.length === 0 ? (
                  <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                    {loading
                      ? "Searching…"
                      : q.trim().length === 0
                        ? "Type a name to search"
                        : "No matches"}
                  </div>
                ) : (
                  <ul className="space-y-1">
                    {rows.map((r) => (
                      <li key={r.person_id}>
                        <button
                          type="button"
                          onClick={() => setTarget(r)}
                          className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-left text-sm hover:bg-accent transition-colors"
                        >
                          <PersonAvatar
                            personId={r.person_id}
                            displayName={r.display_name}
                            size="sm"
                          />
                          <span className="font-medium truncate flex-1">
                            {r.display_name}
                          </span>
                          <span className="text-xs text-muted-foreground tabular shrink-0">
                            {r.total_interactions.toLocaleString()} msgs
                          </span>
                          {r.telegram_username && (
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-border bg-secondary/40">
                              @{r.telegram_username}
                            </span>
                          )}
                          {r.email && (
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-border bg-secondary/40 truncate max-w-[140px]">
                              {r.email}
                            </span>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setOpen(false)}>
                  Cancel
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>Merge these two people?</DialogTitle>
                <DialogDescription>
                  One person will be merged into the other, combining all
                  interactions and identities under the winner.
                </DialogDescription>
              </DialogHeader>
              <div className="rounded-md border border-border p-3 space-y-2 text-xs">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="winner"
                    checked={winner === "other"}
                    onChange={() => setWinner("other")}
                    className="accent-primary"
                  />
                  <span>
                    <span className="font-medium text-foreground">
                      {target.display_name}
                    </span>{" "}
                    wins; this person merges into it
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="winner"
                    checked={winner === "this"}
                    onChange={() => setWinner("this")}
                    className="accent-primary"
                  />
                  <span>
                    <span className="font-medium text-foreground">
                      {thisDisplayName}
                    </span>{" "}
                    (this person) wins; {target.display_name} merges into it
                  </span>
                </label>
              </div>
              <p className="text-xs text-muted-foreground">
                Reversible via SQL (clear canonical.person.merged_into).
              </p>
              <DialogFooter className="gap-2">
                <Button
                  variant="ghost"
                  onClick={() => setTarget(null)}
                  disabled={busy}
                  className="mr-auto"
                >
                  <ChevronLeft className="h-3.5 w-3.5 mr-1" />
                  Back
                </Button>
                <Button
                  variant="outline"
                  onClick={() => setOpen(false)}
                  disabled={busy}
                >
                  Cancel
                </Button>
                <Button onClick={confirmMerge} disabled={busy}>
                  <GitMerge className="h-3.5 w-3.5 mr-1.5" />
                  {busy ? "Merging…" : "Merge"}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
