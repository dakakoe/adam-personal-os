"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Check, X, Clock, ArrowLeftRight, Plus, ChevronRight, Link2, Cake, ExternalLink } from "lucide-react";
import { api, type MergeCandidate, type PersonDetail } from "@/lib/api";
import { formatRelativeDate, formatBirthday, cn } from "@/lib/utils";
import { toast } from "sonner";
import { AddIdentityDialog } from "./add-identity-dialog";
import { IdentityChip } from "./identity-chip";
import { PersonAvatar } from "./person-avatar";

export function MergeQueue({ initial }: { initial: MergeCandidate[] }) {
  const [items, setItems] = useState(initial);
  const [winner, setWinner] = useState<"left" | "right">("left");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [related, setRelated] = useState<MergeCandidate[]>([]);
  const [addOpenFor, setAddOpenFor] = useState<string | null>(null);

  const current = items[0];

  // Refetch the current candidate's full detail (after add-identity invalidates it)
  const refreshCurrent = useCallback(async () => {
    if (!current) return;
    try {
      const [freshLeft, freshRight] = await Promise.all([
        api.getPerson(current.left.person_id),
        api.getPerson(current.right.person_id),
      ]);
      setItems((xs) => {
        if (xs.length === 0) return xs;
        const [first, ...rest] = xs;
        return [{ ...first, left: freshLeft, right: freshRight }, ...rest];
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Refresh failed");
    }
  }, [current]);

  // Default winner = whichever side has more interactions
  useEffect(() => {
    if (current) {
      setWinner(
        current.left.total_interactions >= current.right.total_interactions ? "left" : "right",
      );
    }
  }, [current]);

  // Load related candidates whenever the current pair changes
  useEffect(() => {
    if (!current) {
      setRelated([]);
      return;
    }
    let cancelled = false;
    api.listRelatedCandidates(current.id, 8)
      .then((r) => { if (!cancelled) setRelated(r); })
      .catch(() => { if (!cancelled) setRelated([]); });
    return () => { cancelled = true; };
  }, [current?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  function jumpToCandidate(c: MergeCandidate) {
    setItems((xs) => {
      // Move that candidate to the top, drop it from anywhere else in the queue
      const without = xs.filter((x) => x.id !== c.id);
      return [c, ...without];
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function decide(decision: "approve" | "reject" | "defer") {
    if (!current) return;
    if (decision === "approve") {
      setConfirmOpen(true);
      return;
    }
    try {
      await api.decideCandidate(current.id, { decision });
      toast.success(decision === "reject" ? "Rejected" : "Deferred");
      setItems((xs) => xs.slice(1));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed");
    }
  }

  async function confirmApprove() {
    if (!current) return;
    try {
      await api.decideCandidate(current.id, { decision: "approve", winner });
      toast.success(
        `Merged into ${winner === "left" ? current.left.display_name : current.right.display_name}`,
      );
      setItems((xs) => xs.slice(1));
      setConfirmOpen(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Merge failed");
    }
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (confirmOpen || addOpenFor) return;
      const k = e.key.toLowerCase();
      if (k === "y") { e.preventDefault(); decide("approve"); }
      if (k === "n") { e.preventDefault(); decide("reject"); }
      if (k === "s") { e.preventDefault(); decide("defer"); }
      if (k === "t") { e.preventDefault(); setWinner((w) => (w === "left" ? "right" : "left")); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, confirmOpen, addOpenFor]);

  if (items.length === 0) {
    return <RegenerateEmptyState />;
  }

  const winnerPerson = winner === "left" ? current.left : current.right;
  const loserPerson = winner === "left" ? current.right : current.left;

  return (
    <div className="space-y-6">
      {/* Evidence header */}
      <div className="flex items-center justify-between rounded-lg border border-border bg-card/40 px-4 py-2.5">
        <div className="flex items-center gap-3 text-sm">
          <Badge variant={current.confidence === "high" ? "default" : "secondary"}>
            {current.confidence}
          </Badge>
          <span className="text-muted-foreground">via</span>
          <span className="font-mono text-xs">{current.source}</span>
          {current.score !== null && (
            <span className="text-xs text-muted-foreground tabular">
              · score {current.score.toFixed(2)}
            </span>
          )}
        </div>
        <div className="text-xs text-muted-foreground tabular">
          {items.length} in queue
        </div>
      </div>

      {/* Pair — side-by-side on md+, stacked on mobile so cards don't crush */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6 items-stretch">
        <PersonMini
          person={current.left}
          chosen={winner === "left"}
          onChoose={() => setWinner("left")}
          onAdd={() => setAddOpenFor(current.left.person_id)}
        />
        <PersonMini
          person={current.right}
          chosen={winner === "right"}
          onChoose={() => setWinner("right")}
          onAdd={() => setAddOpenFor(current.right.person_id)}
        />
      </div>

      {/* Evidence panel */}
      {Object.keys(current.evidence).length > 0 && (
        <Card className="border-border bg-card/40">
          <CardHeader className="py-3 px-4">
            <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Evidence
            </h3>
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-0">
            <pre className="text-xs font-mono text-muted-foreground whitespace-pre-wrap break-words">
              {JSON.stringify(current.evidence, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}

      {/* Actions — stack on mobile so buttons don't truncate; on desktop
          keep the swap-winner control opposite the decide group. Keyboard
          hints (kbd) hidden on mobile (no keyboard). */}
      <div className="flex flex-col-reverse sm:flex-row items-stretch sm:items-center sm:justify-between gap-3 pt-2">
        <Button
          variant="outline" size="sm"
          onClick={() => setWinner((w) => (w === "left" ? "right" : "left"))}
          className="text-xs sm:self-start"
        >
          <ArrowLeftRight className="h-3.5 w-3.5 mr-1" />
          Swap winner
          <kbd className="hidden sm:inline-flex ml-2 font-mono text-[10px] px-1 py-0.5 rounded border border-border bg-muted">t</kbd>
        </Button>
        <div className="grid grid-cols-3 sm:flex sm:items-center gap-2">
          <Button variant="outline" onClick={() => decide("defer")} className="min-w-0">
            <Clock className="h-4 w-4 sm:mr-1.5" /><span className="hidden sm:inline">Defer</span>
            <kbd className="hidden sm:inline-flex ml-2 font-mono text-[10px] px-1 py-0.5 rounded border border-border bg-muted">s</kbd>
          </Button>
          <Button variant="outline" onClick={() => decide("reject")} className="min-w-0">
            <X className="h-4 w-4 sm:mr-1.5" /><span className="hidden sm:inline">Reject</span>
            <kbd className="hidden sm:inline-flex ml-2 font-mono text-[10px] px-1 py-0.5 rounded border border-border bg-muted">n</kbd>
          </Button>
          <Button onClick={() => decide("approve")} className="min-w-0">
            <Check className="h-4 w-4 sm:mr-1.5" /><span className="hidden sm:inline">Approve merge</span><span className="sm:hidden">Approve</span>
            <kbd className="hidden sm:inline-flex ml-2 font-mono text-[10px] px-1 py-0.5 rounded border border-primary-foreground/30 bg-primary-foreground/10">y</kbd>
          </Button>
        </div>
      </div>

      {/* Related candidates — clusters around either person */}
      {related.length > 0 && (
        <section className="pt-4">
          <div className="flex items-center gap-2 mb-3 text-xs text-muted-foreground uppercase tracking-wide">
            <Link2 className="h-3.5 w-3.5" />
            <span className="font-medium">Also pending for these people</span>
            <span className="tabular">({related.length})</span>
          </div>
          <ul className="space-y-2">
            {related.map((r) => (
              <li key={r.id}>
                <RelatedRow
                  candidate={r}
                  highlightPersonIds={[current.left.person_id, current.right.person_id]}
                  onJump={() => jumpToCandidate(r)}
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Approve-merge confirm */}
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Merge these two people?</AlertDialogTitle>
            <AlertDialogDescription className="space-y-2">
              <span className="block">
                <span className="font-medium text-foreground">{loserPerson.display_name}</span>
                {" "}({loserPerson.total_interactions.toLocaleString()} msgs) will be merged into{" "}
                <span className="font-medium text-foreground">{winnerPerson.display_name}</span>
                {" "}({winnerPerson.total_interactions.toLocaleString()} msgs).
              </span>
              <span className="block text-xs text-muted-foreground">
                All interactions, identities, and signals from the merged-away person will
                reattach to the winner. The merge is reversible by clearing
                canonical.person.merged_into in SQL.
              </span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmApprove}>
              Merge into {winnerPerson.display_name}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Add-identity dialog (shared between left and right) */}
      {addOpenFor && (
        <AddIdentityDialog
          open={!!addOpenFor}
          onOpenChange={(o) => { if (!o) setAddOpenFor(null); }}
          personId={addOpenFor}
          onAdded={() => { refreshCurrent(); }}
        />
      )}
    </div>
  );
}

function PersonMini({
  person, chosen, onChoose, onAdd,
}: {
  person: PersonDetail;
  chosen: boolean;
  onChoose: () => void;
  onAdd: () => void;
}) {
  return (
    <div
      className={cn(
        "relative rounded-lg border bg-card/60 p-5 transition-colors group",
        chosen ? "border-primary ring-1 ring-primary" : "border-border hover:border-border/80 hover:bg-card",
      )}
    >
      {/* Whole card except the +Add button is the chooser hit area */}
      <button
        type="button"
        onClick={onChoose}
        aria-label={`Choose ${person.display_name} as winner`}
        className="absolute inset-0 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />

      <div className="relative pointer-events-none">
        <div className="flex items-start gap-3">
          <PersonAvatar
            personId={person.person_id}
            displayName={person.display_name}
            className="h-10 w-10 shrink-0"
          />
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2">
              <h3 className="font-semibold truncate">{person.display_name}</h3>
              {chosen && <Badge className="text-[10px] h-5">winner</Badge>}
            </div>
            {person.structured?.current_role && (
              <p className="text-xs text-muted-foreground truncate mt-0.5">
                {person.structured.current_role}
                {person.structured.current_company && ` · ${person.structured.current_company}`}
              </p>
            )}
          </div>
        </div>

        <div className="mt-3 flex items-center gap-3 text-xs text-muted-foreground tabular flex-wrap">
          <span>
            <span className="text-foreground font-medium">
              {person.total_interactions.toLocaleString()}
            </span>{" "}
            msgs
          </span>
          <span>·</span>
          <span>last {formatRelativeDate(person.last_interaction_at)}</span>
          {formatBirthday(person.birthday) && (
            <>
              <span>·</span>
              <span className="inline-flex items-center gap-1">
                <Cake className="h-3 w-3" />
                {formatBirthday(person.birthday)}
              </span>
            </>
          )}
        </div>

        {/* Identity chips. We render "synthetic" chips for the human-friendly
            telegram handle and phone first (they live on raw.telegram_user,
            not in canonical.identity), then the actual identities — minus
            the numeric telegram one which would duplicate the @handle. */}
        <div className="mt-3 flex flex-wrap gap-1.5 pointer-events-auto">
          {person.telegram_username && (
            <IdentityChip
              source="telegram_handle"
              value={person.telegram_username}
              readOnly
              compact
            />
          )}
          {person.phone && (
            <IdentityChip source="phone" value={person.phone} readOnly compact />
          )}
          {person.identities
            .filter((i) => !(i.source === "telegram" && person.telegram_username))
            .slice(0, 10)
            .map((i) => (
              <IdentityChip
                key={i.identity_id}
                source={i.source}
                value={i.source_id}
                readOnly
                compact
              />
            ))}
          {person.identities.length > 10 && (
            <span className="text-[10px] text-muted-foreground self-center">
              +{person.identities.length - 10}
            </span>
          )}
        </div>

        {person.summary && (
          <p className="mt-3 text-xs text-foreground/75 line-clamp-3 leading-relaxed">
            {person.summary}
          </p>
        )}
      </div>

      {/* Top-right action cluster — opens profile in a new tab + adds
          identity. Both escape the chooser overlay via pointer-events-auto
          (their z-10 + position keep them above the absolute button). */}
      <div className="absolute top-3 right-3 z-10 flex items-center gap-1.5">
        <a
          href={`/persons/${person.person_id}`}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          className={cn(
            "inline-flex items-center gap-1 h-6 px-2 rounded-md",
            "border border-border bg-card/80 backdrop-blur",
            "text-[11px] text-muted-foreground hover:text-foreground hover:bg-card hover:border-primary/40",
            "transition-colors opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
          )}
          title="Open full profile in new tab"
        >
          <ExternalLink className="h-3 w-3" />
          Profile
        </a>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onAdd(); }}
          className={cn(
            "inline-flex items-center gap-1 h-6 px-2 rounded-md",
            "border border-border bg-card/80 backdrop-blur",
            "text-[11px] text-muted-foreground hover:text-foreground hover:bg-card hover:border-primary/40",
            "transition-colors opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
          )}
          title="Add identity to this person"
        >
          <Plus className="h-3 w-3" />
          Add
        </button>
      </div>
    </div>
  );
}

/** Mini-row for a related pending candidate — clickable to jump to it. */
function RelatedRow({
  candidate, highlightPersonIds, onJump,
}: {
  candidate: MergeCandidate;
  highlightPersonIds: string[];
  onJump: () => void;
}) {
  const isShared = (id: string) => highlightPersonIds.includes(id);
  return (
    <button
      type="button"
      onClick={onJump}
      className={cn(
        "w-full flex items-center gap-3 px-3 py-2 rounded-md border border-border",
        "bg-card/40 hover:bg-card hover:border-primary/40 transition-colors text-left",
      )}
    >
      <Badge
        variant={candidate.confidence === "high" ? "default" : "secondary"}
        className="h-5 text-[10px] shrink-0"
      >
        {candidate.confidence}
      </Badge>
      <span className="text-[10px] font-mono text-muted-foreground shrink-0 w-20 truncate">
        {candidate.source}
      </span>
      <div className="min-w-0 flex-1 flex items-center gap-2 text-sm">
        <span
          className={cn(
            "truncate",
            isShared(candidate.left.person_id) && "text-primary font-medium",
          )}
          title={candidate.left.display_name}
        >
          {candidate.left.display_name}
        </span>
        <span className="text-muted-foreground shrink-0">↔</span>
        <span
          className={cn(
            "truncate",
            isShared(candidate.right.person_id) && "text-primary font-medium",
          )}
          title={candidate.right.display_name}
        >
          {candidate.right.display_name}
        </span>
      </div>
      <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
    </button>
  );
}


/**
 * Shown when the queue is empty. Lets the user re-run the candidate
 * generator from the UI instead of SSH-ing to the droplet. Server-side
 * call subprocesses out to `enrichment generate-candidates` and re-runs
 * the auto-reject sweeps; on success the queue is refreshed.
 */
function RegenerateEmptyState() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<
    null | { live_pending: number; auto_rejected: { zombie: number; incompatible: number; weak_fuzzy: number } }
  >(null);

  async function regenerate() {
    setBusy(true);
    try {
      const r = await api.regenerateCandidates();
      setLastResult({ live_pending: r.live_pending, auto_rejected: r.auto_rejected });
      const ar = r.auto_rejected;
      const autoTotal = ar.zombie + ar.incompatible + ar.weak_fuzzy;
      toast.success(
        `${r.live_pending} live candidates · auto-rejected ${autoTotal} noise`,
      );
      if (r.live_pending > 0) router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Regenerate failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-card p-6 sm:p-12 text-center space-y-4">
      <p className="text-sm text-muted-foreground">
        No candidates pending.
      </p>
      <Button onClick={regenerate} disabled={busy} size="lg">
        {busy ? (
          <>
            <Clock className="h-4 w-4 mr-2 animate-pulse" />
            Regenerating… (up to 5 min)
          </>
        ) : (
          <>
            <Plus className="h-4 w-4 mr-2" />
            Regenerate candidates
          </>
        )}
      </Button>
      {lastResult && (
        <p className="text-xs text-muted-foreground">
          Last run: {lastResult.live_pending} live · auto-rejected{" "}
          {lastResult.auto_rejected.zombie} zombie ·{" "}
          {lastResult.auto_rejected.incompatible} incompatible ·{" "}
          {lastResult.auto_rejected.weak_fuzzy} weak-fuzzy
        </p>
      )}
      <p className="text-[11px] text-muted-foreground/70">
        Runs the same job as <span className="font-mono">enrichment generate-candidates</span> on the droplet.
        New pairs surface based on changed/added identities, signals, and LLM-verified emails.
      </p>
    </div>
  );
}
