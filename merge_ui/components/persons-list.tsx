"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Mail, Linkedin, Send, MapPin, ListChecks, Trash2, X, Check } from "lucide-react";
import { toast } from "sonner";
import { PersonAvatar } from "@/components/person-avatar";
import { PersonCircleTag } from "@/components/person-circle-tag";
import { PersonRowActions } from "@/components/person-row-actions";
import { api, type PersonRow, type ContactCircle } from "@/lib/api";
import { formatRelativeDate, cn } from "@/lib/utils";

/** The admin People list, with a Select mode for mass-deleting contacts.
 *
 *  Selection spans the current page (100 rows) — the same slice the server
 *  rendered. Deleting is a soft delete (recoverable from Cleanup), reusing the
 *  batch endpoint the Cleanup page already uses. Off by default: with a screen
 *  of contacts you're reading, not culling, and permanent checkboxes are noise.
 */
export function PersonsList({
  rows, circleCatalogue,
}: {
  rows: PersonRow[];
  circleCatalogue: ContactCircle[];
}) {
  const router = useRouter();
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Deleted rows vanish immediately, before the server re-render catches up.
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const visible = rows.filter((r) => !hidden.has(r.person_id));
  const allPicked = visible.length > 0 && visible.every((r) => selected.has(r.person_id));

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) => {
      if (allPicked) return new Set();
      const next = new Set(prev);
      visible.forEach((r) => next.add(r.person_id));
      return next;
    });
  }

  async function remove() {
    const ids = [...selected];
    if (ids.length === 0) return;
    if (!confirm(
      `Delete ${ids.length} contact${ids.length === 1 ? "" : "s"}?\n\n` +
      `They're hidden everywhere and their history is kept — you can restore them from Cleanup.`
    )) return;
    setBusy(true);
    try {
      const { deleted } = await api.cleanupDeleteBatch(ids);
      setHidden((prev) => new Set([...prev, ...ids]));
      setSelected(new Set());
      toast.success(`${deleted} deleted${deleted !== ids.length ? ` (${ids.length - deleted} were already gone)` : ""}`);
      router.refresh();   // server list re-fetches; the rows are already hidden
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't delete those");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {/* Select toggle sits above the list — it flips every row into a
          checkbox and reveals the delete bar. */}
      <div className="mb-2 flex items-center gap-2 text-xs">
        <button
          type="button"
          onClick={() => { setSelectMode((v) => !v); setSelected(new Set()); }}
          className={cn("inline-flex items-center gap-1 h-7 px-2.5 rounded-md border border-border",
            selectMode ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50")}
        >
          <ListChecks className="h-3.5 w-3.5" />
          {selectMode ? "Done" : "Select"}
        </button>
        {selectMode && visible.length > 0 && (
          <button type="button" onClick={toggleAll}
            className="text-primary hover:underline">
            {allPicked ? "none" : `all ${visible.length} on this page`}
          </button>
        )}
      </div>

      <div className="rounded-lg border border-border overflow-hidden bg-card/40">
        {visible.length === 0 ? (
          <div className="p-10 text-center text-muted-foreground text-sm">No people found.</div>
        ) : (
          <ul className="divide-y divide-border">
            {visible.map((p) => {
              const picked = selected.has(p.person_id);
              if (selectMode) {
                // The whole row toggles selection; no navigation, no per-row
                // tools — the bar owns what happens to the selection.
                return (
                  <li key={p.person_id}>
                    <button
                      type="button"
                      onClick={() => toggle(p.person_id)}
                      role="checkbox"
                      aria-checked={picked}
                      className={cn("w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-accent/40",
                        picked && "bg-primary/10")}
                    >
                      <span className={cn("grid place-items-center h-5 w-5 shrink-0 rounded border",
                        picked ? "bg-primary border-primary text-primary-foreground"
                               : "border-border text-transparent")}>
                        <Check className="h-3 w-3" />
                      </span>
                      <PersonRowInner row={p} showShared />
                    </button>
                  </li>
                );
              }
              return (
                <li key={p.person_id}>
                  <div className="group flex items-center gap-2 pr-3 hover:bg-accent/40 transition-colors">
                    <Link href={`/persons/${p.person_id}`}
                      className="flex items-center gap-3 px-4 py-2.5 min-w-0 flex-1">
                      <PersonRowInner row={p} showShared />
                    </Link>
                    <PersonCircleTag personId={p.person_id} circles={p.circles ?? []} catalogue={circleCatalogue} />
                    <PersonRowActions personId={p.person_id} displayName={p.display_name} />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {selectMode && selected.size > 0 && (
        <div className="sticky bottom-3 z-20 mt-3 mx-auto w-full max-w-md">
          <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 shadow-lg shadow-black/40">
            <span className="text-sm font-medium tabular shrink-0">{selected.size} selected</span>
            <span className="h-4 w-px bg-border mx-1" aria-hidden="true" />
            <button type="button" onClick={remove} disabled={busy}
              className="inline-flex items-center gap-1 h-8 px-3 rounded-md border border-border text-sm text-muted-foreground hover:bg-destructive hover:text-destructive-foreground disabled:opacity-50">
              <Trash2 className="h-3.5 w-3.5" /> {busy ? "Deleting…" : "Delete"}
            </button>
            <button type="button" onClick={() => setSelected(new Set())} disabled={busy}
              className="ml-auto inline-flex items-center gap-1 h-8 px-2 rounded-md text-sm text-muted-foreground hover:bg-accent disabled:opacity-50">
              <X className="h-3.5 w-3.5" /> Clear
            </button>
          </div>
        </div>
      )}
    </>
  );
}

/** One row's inner content — avatar, name, channel glyphs, handles, location,
 *  interaction count. Shared by the admin list and the read-only budget list. */
export function PersonRowInner({ row: p, showShared }: { row: PersonRow; showShared?: boolean }) {
  return (
    <>
      <PersonAvatar personId={p.person_id} displayName={p.display_name} className="h-8 w-8 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="font-medium truncate">{p.display_name}</span>
          <ChannelGlyphs row={p} />
          {showShared && p.visibility === "shared" && (
            <span className="text-[10px] text-emerald-400 border border-emerald-500/40 rounded px-1">shared</span>
          )}
        </div>
        <div className="text-xs text-muted-foreground truncate font-mono">
          {[p.telegram_username && `@${p.telegram_username}`, p.email, p.linkedin]
            .filter(Boolean)
            .join(" · ") || "—"}
        </div>
        {p.location && (
          <div className="text-xs text-muted-foreground truncate inline-flex items-center gap-1 mt-0.5">
            <MapPin className="h-3 w-3 shrink-0" /> {p.location}
          </div>
        )}
      </div>
      <div className="text-right text-xs text-muted-foreground tabular shrink-0">
        <div className="text-foreground font-medium">{p.total_interactions.toLocaleString()}</div>
        <div>{formatRelativeDate(p.last_interaction_at)}</div>
      </div>
    </>
  );
}

function ChannelGlyphs({ row }: { row: PersonRow }) {
  return (
    <span className="inline-flex items-center gap-1 text-muted-foreground">
      {row.telegram_username && <Send className="h-3 w-3 text-[var(--color-channel-telegram)]" />}
      {row.email && <Mail className="h-3 w-3 text-[var(--color-channel-email)]" />}
      {row.linkedin && <Linkedin className="h-3 w-3 text-[var(--color-channel-linkedin)]" />}
    </span>
  );
}
