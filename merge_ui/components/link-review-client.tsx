"use client";

import { useState } from "react";
import Link from "next/link";
import { Linkedin, ArrowRight, Check, X, Building2 } from "lucide-react";
import { CompanyLogo } from "@/components/company-logo";
import { PersonAvatar } from "@/components/person-avatar";
import { api, type LinkSuggestion } from "@/lib/api";
import { toast } from "sonner";

/** Review queue for FUZZY LinkedIn-employer → company-entity matches. Each row
 *  is one-click Link (writes a company_person row) or Dismiss (tombstones the
 *  pair). High-similarity rows are clearly the same company; lower ones are the
 *  false-positives this human-in-the-loop step exists to catch. */
export function LinkReviewClient({ initial }: { initial: LinkSuggestion[] }) {
  const [items, setItems] = useState(initial);
  const [busy, setBusy] = useState<string | null>(null);
  const key = (s: LinkSuggestion) => `${s.person_id}:${s.company_id}`;

  function drop(k: string) {
    setItems((xs) => xs.filter((s) => key(s) !== k));
  }

  async function link(s: LinkSuggestion) {
    const k = key(s);
    setBusy(k);
    try {
      await api.addCompanyPerson(s.company_id, s.person_id, s.role ?? undefined);
      toast.success(`Linked ${s.person_name} → ${s.company_name}`);
      drop(k);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Link failed");
    } finally { setBusy(null); }
  }

  async function dismiss(s: LinkSuggestion) {
    const k = key(s);
    setBusy(k);
    try {
      await api.dismissLinkSuggestion(s.person_id, s.company_id);
      drop(k);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Dismiss failed");
    } finally { setBusy(null); }
  }

  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
        Nothing to review — every LinkedIn employer either matches a linked
        company or has been actioned. New suggestions appear as data grows.
      </div>
    );
  }

  return (
    <ul className="divide-y divide-border rounded-lg border border-border overflow-hidden bg-card/40">
      {items.map((s) => {
        const k = key(s);
        const strong = s.similarity >= 0.85;
        return (
          <li key={k} className="flex items-center gap-3 px-3 sm:px-4 py-2.5">
            <Link href={`/persons/${s.person_id}`} className="shrink-0">
              <PersonAvatar personId={s.person_id} displayName={s.person_name} className="h-8 w-8" />
            </Link>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <Link href={`/persons/${s.person_id}`} className="font-medium truncate hover:underline">
                  {s.person_name}
                </Link>
                {s.linkedin_vanity && (
                  <a
                    href={`https://www.linkedin.com/in/${s.linkedin_vanity}`}
                    target="_blank" rel="noopener noreferrer"
                    title="Open LinkedIn profile to verify"
                    onClick={(e) => e.stopPropagation()}
                    className="inline-flex items-center text-[var(--color-channel-linkedin)] hover:opacity-80 shrink-0">
                    <Linkedin className="h-3.5 w-3.5" />
                  </a>
                )}
                {s.role && <span className="text-xs text-muted-foreground truncate">· {s.role}</span>}
              </div>
              <div className="text-xs text-muted-foreground flex items-center gap-1.5 mt-0.5 flex-wrap">
                <Linkedin className="h-3 w-3 text-[var(--color-channel-linkedin)] shrink-0" />
                <span className="truncate">{s.employer}</span>
                <ArrowRight className="h-3 w-3 shrink-0" />
                <Link href={`/companies/${s.company_id}`} className="inline-flex items-center gap-1 hover:underline">
                  <CompanyLogo domain={s.company_domain} name={s.company_name} size={14} />
                  <span className="text-foreground">{s.company_name}</span>
                </Link>
                <span className={`ml-1 text-[10px] font-mono px-1 rounded border shrink-0 ${strong ? "border-emerald-500/40 text-emerald-400" : "border-amber-500/40 text-amber-400"}`}>
                  {Math.round(s.similarity * 100)}%
                </span>
              </div>
            </div>
            <div className="flex items-center gap-1 shrink-0">
              <button
                type="button" disabled={busy === k} onClick={() => link(s)}
                title={`Link to ${s.company_name}`}
                className="inline-flex items-center gap-1 h-7 px-2 rounded border border-emerald-500/40 text-emerald-400 text-xs hover:bg-emerald-500/10 disabled:opacity-40">
                <Check className="h-3.5 w-3.5" /> Link
              </button>
              <button
                type="button" disabled={busy === k} onClick={() => dismiss(s)}
                title="Dismiss — not the same company"
                className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-accent disabled:opacity-40">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
