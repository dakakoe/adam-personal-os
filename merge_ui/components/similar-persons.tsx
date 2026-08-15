"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { GitMerge, ExternalLink, Users, X, EyeOff } from "lucide-react";
import { api, type SimilarPerson } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { PersonAvatar } from "./person-avatar";

/**
 * Surfaces other canonical.persons with the same / similar display_name.
 * Catches cases the candidate generators missed (e.g. fuzzy_name dropped
 * them due to the per-run cap, or LinkedIn-only persons that never had a
 * bridge to telegram).
 */
export function SimilarPersons({
  personId,
  thisDisplayName,
}: {
  personId: string;
  thisDisplayName: string;
}) {
  const router = useRouter();
  const [items, setItems] = useState<SimilarPerson[] | null>(null);
  const [target, setTarget] = useState<SimilarPerson | null>(null);
  const [winner, setWinner] = useState<"this" | "other">("this");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.listSimilarPersons(personId)
      .then((r) => { if (!cancelled) setItems(r); })
      .catch(() => { if (!cancelled) setItems([]); });
  }, [personId]);

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
      if (winner === "other") {
        router.replace(`/persons/${target.person_id}`);
      } else {
        router.refresh();
        setItems((xs) => (xs ?? []).filter((x) => x.person_id !== target.person_id));
      }
      setTarget(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Merge failed");
    } finally {
      setBusy(false);
    }
  }

  async function dismiss(other: SimilarPerson) {
    // Optimistic: remove from UI immediately, undo on error
    setItems((xs) => (xs ?? []).filter((x) => x.person_id !== other.person_id));
    try {
      await api.dismissSimilar(personId, other.person_id);
      toast.success(`Dismissed: ${other.display_name}`);
    } catch (e) {
      setItems((xs) => [...(xs ?? []), other]);
      toast.error(e instanceof Error ? e.message : "Dismiss failed");
    }
  }

  async function dismissAll() {
    if (!items || items.length === 0) return;
    const snapshot = items;
    setItems([]);
    let failed = 0;
    await Promise.all(
      snapshot.map(async (s) => {
        try { await api.dismissSimilar(personId, s.person_id); }
        catch { failed += 1; }
      }),
    );
    if (failed === 0) {
      toast.success(`Dismissed all ${snapshot.length}`);
    } else {
      toast.error(`Dismissed ${snapshot.length - failed}, ${failed} failed`);
      // Restore the ones that failed (best-effort — we don't know which)
      // No-op for v1; user can refresh to re-fetch.
    }
  }

  if (items === null || items.length === 0) return null;

  return (
    <>
      <section className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-amber-500" />
            <h3 className="text-sm font-medium text-foreground">
              {items.length} possible match{items.length === 1 ? "" : "es"} by name
            </h3>
          </div>
          {items.length > 1 && (
            <Button
              size="sm"
              variant="ghost"
              onClick={dismissAll}
              className="h-7 text-xs text-muted-foreground hover:text-foreground"
            >
              <EyeOff className="h-3.5 w-3.5 mr-1" />
              Dismiss all
            </Button>
          )}
        </div>
        <p className="text-xs text-muted-foreground -mt-1">
          Exact-name matches or shared first + similar second name. Dismiss to stop suggesting a pair; merge to combine identities + history.
        </p>
        <ul className="space-y-1.5">
          {items.map((s) => {
            const exact = s.display_name === thisDisplayName;
            return (
              <li
                key={s.person_id}
                className={cn(
                  "flex flex-wrap sm:flex-nowrap items-center gap-2 sm:gap-3 px-3 py-2 rounded-md",
                  "bg-card/60 border border-border",
                )}
              >
                <PersonAvatar
                  personId={s.person_id}
                  displayName={s.display_name}
                  size="sm"
                />
                <div className="min-w-0 flex-1 text-sm order-2 sm:order-none basis-full sm:basis-auto">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="font-medium truncate">{s.display_name}</span>
                    {exact && (
                      <Badge variant="default" className="h-5 text-[10px] shrink-0">
                        exact name
                      </Badge>
                    )}
                    <span className="text-xs text-muted-foreground tabular">
                      {s.total_interactions.toLocaleString()} msgs · {s.identity_count} ids
                    </span>
                  </div>
                  {s.identities_preview && s.identities_preview.length > 0 ? (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {s.identities_preview.map((id, i) => (
                        <IdentityPreviewChip key={i} source={id.source} value={id.source_id} />
                      ))}
                      {s.identity_count > s.identities_preview.length && (
                        <span className="text-[10px] text-muted-foreground self-center">
                          +{s.identity_count - s.identities_preview.length} more
                        </span>
                      )}
                    </div>
                  ) : (
                    s.sources && s.sources.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {s.sources.map((src) => (
                          <span
                            key={src}
                            className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-border bg-secondary/40"
                          >
                            {src}
                          </span>
                        ))}
                      </div>
                    )
                  )}
                </div>
                {/* Actions cluster — sits inline with avatar on mobile, on
                    the right on desktop. ml-auto pushes them to the row
                    end on mobile after the avatar; on sm+ flex-nowrap
                    keeps them right-aligned naturally. */}
                <div className="flex items-center gap-1 ml-auto sm:ml-0 order-1 sm:order-none shrink-0">
                <Link
                  href={`/persons/${s.person_id}`}
                  className="hidden sm:grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-accent hover:text-foreground"
                  title="Open this person"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                </Link>
                <button
                  type="button"
                  onClick={() => dismiss(s)}
                  className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-accent hover:text-destructive transition-colors"
                  title="Not the same person — dismiss"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => { setTarget(s); setWinner("this"); }}
                  className="h-7 text-xs"
                >
                  <GitMerge className="h-3.5 w-3.5 mr-1" />
                  Merge
                </Button>
                </div>
              </li>
            );
          })}
        </ul>
      </section>

      <AlertDialog open={!!target} onOpenChange={(o) => { if (!o) setTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Merge these two people?</AlertDialogTitle>
            <AlertDialogDescription className="space-y-3">
              <span className="block">
                One person will be merged into the other, combining all interactions and identities under the winner.
              </span>
              <div className="rounded-md border border-border p-3 space-y-2 text-xs">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio" name="winner"
                    checked={winner === "this"}
                    onChange={() => setWinner("this")}
                    className="accent-primary"
                  />
                  <span>
                    <span className="font-medium text-foreground">{thisDisplayName}</span>{" "}
                    (this person) wins; {target?.display_name} merges into it
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio" name="winner"
                    checked={winner === "other"}
                    onChange={() => setWinner("other")}
                    className="accent-primary"
                  />
                  <span>
                    <span className="font-medium text-foreground">{target?.display_name}</span>{" "}
                    wins; this person merges into it
                  </span>
                </label>
              </div>
              <span className="block text-xs text-muted-foreground">
                Reversible via SQL (clear canonical.person.merged_into).
              </span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmMerge} disabled={busy}>
              {busy ? "Merging…" : "Merge"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}


// Compact, non-interactive identity preview chip used inside the similar
// rows. Mirrors IdentityChip's icon + color conventions but stays small,
// truncates long values, and skips link / edit / delete affordances.
import {
  Linkedin as LinkedinIcon, Instagram as InstagramIcon, Twitter as TwitterIcon,
  Send as SendIcon, Phone as PhoneIcon, Mail as MailIcon, Github as GithubIcon,
  Globe as GlobeIcon,
} from "lucide-react";

const PREVIEW_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  linkedin: LinkedinIcon,
  instagram: InstagramIcon,
  x: TwitterIcon,
  twitter: TwitterIcon,
  telegram: SendIcon,
  telegram_handle: SendIcon,
  phone: PhoneIcon,
  email: MailIcon,
  github: GithubIcon,
  website: GlobeIcon,
};

const PREVIEW_COLOR: Record<string, string> = {
  telegram: "text-[var(--color-channel-telegram)]",
  telegram_handle: "text-[var(--color-channel-telegram)]",
  email: "text-[var(--color-channel-email)]",
  linkedin: "text-[var(--color-channel-linkedin)]",
  phone: "text-[var(--color-channel-phone)]",
  x: "text-[var(--color-channel-x)]",
  twitter: "text-[var(--color-channel-x)]",
  instagram: "text-[var(--color-channel-instagram)]",
  github: "text-[var(--color-channel-github)]",
  website: "text-[var(--color-channel-website)]",
};

function previewLabel(source: string, value: string): string {
  const v = value.trim();
  if (source === "x" || source === "twitter" || source === "instagram") return `@${v.replace(/^@/, "")}`;
  if (source === "telegram") return v;  // numeric ID
  if (source === "linkedin") return v;  // vanity slug
  return v;                              // email / phone / github / website / etc — show raw
}

function IdentityPreviewChip({ source, value }: { source: string; value: string }) {
  const Icon = PREVIEW_ICONS[source];
  const color = PREVIEW_COLOR[source] ?? "text-muted-foreground";
  return (
    <span
      title={`${source}: ${value}`}
      className="inline-flex items-center gap-1 max-w-[220px] text-[10px] font-mono px-1.5 py-0.5 rounded border border-border bg-secondary/40"
    >
      {Icon && <Icon className={cn("h-3 w-3 shrink-0", color)} />}
      <span className="truncate">{previewLabel(source, value)}</span>
    </span>
  );
}
