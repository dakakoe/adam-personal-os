"use client";

import { useEffect, useMemo, useState } from "react";
import { humanizeHandle } from "@/lib/name-hint";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sparkles, Check, Pencil, X } from "lucide-react";
import { api, type IdentityRow, type NameSuggestionsResponse } from "@/lib/api";
import { toast } from "sonner";

/**
 * Best guess at a real name based on handle-style identities. Priority
 * mirrors what a human would intuit: LinkedIn vanity (often a real name),
 * then social handles, then email local-part as the weakest hint.
 */
function deriveNameHint(
  identities: IdentityRow[],
  telegramUsername: string | null,
): string | null {
  const order = ["linkedin", "instagram", "x", "twitter", "github"];
  for (const src of order) {
    const m = identities.find((i) => i.source === src);
    if (m?.source_id) return humanizeHandle(m.source_id);
  }
  if (telegramUsername) return humanizeHandle(telegramUsername);
  const email = identities.find((i) => i.source === "email");
  if (email?.source_id) {
    const local = email.source_id.split("@")[0];
    if (local) return humanizeHandle(local);
  }
  return null;
}

/**
 * Inline banner that nudges the user to upgrade a synthetic display_name
 * (e.g. "Telegram user 553769") to a real name pulled from LinkedIn,
 * Google Contacts, or Telegram first+last. Shown only when the server
 * flags `current_is_synthetic: true`. Always shows a manual-rename
 * input as well, since plenty of synthetic-named persons (telegram-only
 * accounts) have no bridged identity to pull a suggestion from.
 */
export function NameSuggestionBanner({
  personId,
  initialDisplayName,
  identities = [],
  telegramUsername = null,
}: {
  personId: string;
  initialDisplayName: string;
  /** Identities for hint-derivation (e.g. instagram handle → "Raskalov"). */
  identities?: IdentityRow[];
  /** raw.telegram_user.username — useful as a handle-style hint. */
  telegramUsername?: string | null;
}) {
  const router = useRouter();
  const [data, setData] = useState<NameSuggestionsResponse | null>(null);
  // Hint pre-filled into the manual input when the name is synthetic and
  // there are no auto-suggestions. Computed once per identities change.
  const nameHint = useMemo(
    () => deriveNameHint(identities, telegramUsername),
    [identities, telegramUsername],
  );
  const [manualMode, setManualMode] = useState(false);
  const [manualValue, setManualValue] = useState(initialDisplayName);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.nameSuggestions(personId)
      .then((r) => {
        if (cancelled) return;
        setData(r);
        // Auto-open the manual input when the name is synthetic AND there
        // are no auto-suggestions to apply. Pre-fill with the best hint
        // we have (handle title-cased) so the user can just tweak + save
        // instead of typing from scratch. This is the Vitaly case: name
        // is "Telegram user N", no LinkedIn/email to mine, but they just
        // added @raskalov on Instagram — "Raskalov" is the obvious draft.
        if (r.current_is_synthetic && r.suggestions.length === 0) {
          setManualMode(true);
          setManualValue(nameHint ?? initialDisplayName);
        }
      })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [personId, nameHint, initialDisplayName]);

  async function apply(newName: string) {
    const clean = newName.trim();
    if (!clean || clean === initialDisplayName) return;
    setBusy(true);
    try {
      await api.renamePerson(personId, clean);
      toast.success(`Renamed to ${clean}`);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Rename failed");
    } finally {
      setBusy(false);
    }
  }

  // Hide entirely if the server says current name is fine AND nothing to suggest.
  if (data === null) return null;
  const synthetic = data.current_is_synthetic;
  const hasSuggestions = data.suggestions.length > 0;
  if (!synthetic && !hasSuggestions) return null;

  const sourceLabel: Record<string, string> = {
    linkedin: "LinkedIn",
    google_contacts: "Google Contacts",
    telegram: "Telegram",
  };

  return (
    <section className="rounded-lg border border-sky-500/30 bg-sky-500/5 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-sky-500" />
        <h3 className="text-sm font-medium text-foreground">
          {synthetic
            ? "This name looks auto-generated"
            : "Other names are on file"}
        </h3>
      </div>

      {hasSuggestions && (
        <>
          <p className="text-xs text-muted-foreground -mt-1">
            Pulled from {Array.from(new Set(data.suggestions.map((s) => sourceLabel[s.source] ?? s.source))).join(" · ")}.
            Click to apply.
          </p>
          <ul className="space-y-1.5">
            {data.suggestions.map((s, idx) => (
              <li
                key={`${s.source}-${idx}`}
                className="flex items-center gap-3 px-3 py-2 rounded-md bg-card/60 border border-border"
              >
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-border bg-secondary/40 shrink-0">
                  {sourceLabel[s.source] ?? s.source}
                </span>
                <div className="min-w-0 flex-1 text-sm">
                  <span className="font-medium truncate">{s.suggested}</span>
                  <div className="text-[10px] text-muted-foreground truncate font-mono">
                    {s.evidence}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => apply(s.suggested)}
                  disabled={busy}
                  className="h-7 text-xs shrink-0"
                >
                  <Check className="h-3.5 w-3.5 mr-1" />
                  Use this
                </Button>
              </li>
            ))}
          </ul>
        </>
      )}

      {/* Manual override — useful for telegram-only persons where there's no
          source to pull from, and for cases where none of the suggestions
          match (e.g. nickname vs. legal name). */}
      <div className="pt-1">
        {!manualMode ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => { setManualValue(nameHint ?? initialDisplayName); setManualMode(true); }}
            className="h-7 text-xs text-muted-foreground hover:text-foreground"
          >
            <Pencil className="h-3.5 w-3.5 mr-1" />
            {hasSuggestions ? "Type a different name" : "Type the right name"}
          </Button>
        ) : (
          <form
            onSubmit={(e) => { e.preventDefault(); apply(manualValue); }}
            className="space-y-1"
          >
            {nameHint && manualValue === nameHint && (
              <p className="text-[10px] text-muted-foreground">
                Pre-filled from one of this person’s handles — usually just a
                last name; prepend their first name before saving.
              </p>
            )}
            <div className="flex items-center gap-2">
              <Input
                autoFocus
                value={manualValue}
                onChange={(e) => setManualValue(e.target.value)}
                placeholder="Display name…"
                className="h-8 text-sm flex-1"
                disabled={busy}
              />
              <Button
                type="submit"
                size="sm"
                disabled={busy || !manualValue.trim() || manualValue.trim() === initialDisplayName}
                className="h-8"
              >
                {busy ? "Saving…" : "Save"}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setManualMode(false)}
                disabled={busy}
                className="h-8 w-8 p-0"
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          </form>
        )}
      </div>
    </section>
  );
}
