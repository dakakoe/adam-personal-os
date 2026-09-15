"use client";

import { useState } from "react";
import { MessageSquarePlus, X } from "lucide-react";
import { api, type FollowupRow } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";

/**
 * The running log on one follow-up — what you actually discussed, what changed.
 *
 * Distinct from the follow-up's `topic`, which is what you MEANT to discuss and
 * is fixed when you plan it. These accumulate afterwards, so a reconnect keeps
 * its own short history instead of the outcome living only in your head.
 *
 * Shared by the pipeline page and the contact panel. `onChanged` receives the
 * refreshed follow-up the API returns, so neither surface has to refetch.
 */
export function FollowupNotes({
  followup, onChanged, compact,
}: {
  followup: FollowupRow;
  onChanged: (updated: FollowupRow) => void;
  compact?: boolean;
}) {
  const [adding, setAdding] = useState(false);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);

  const notes = followup.notes ?? [];

  async function add() {
    const text = body.trim();
    if (!text) return;
    setBusy(true);
    try {
      onChanged(await api.addFollowupNote(followup.id, text));
      setBody(""); setAdding(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't save the note");
    } finally { setBusy(false); }
  }

  async function remove(noteId: string) {
    try { onChanged(await api.deleteFollowupNote(followup.id, noteId)); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }

  return (
    <div className={compact ? "mt-1" : "mt-1.5"}>
      {notes.length > 0 && (
        <ul className="space-y-1 mb-1">
          {notes.map((n) => (
            <li key={n.id} className="group/note flex items-start gap-1.5 text-xs">
              <span className="text-muted-foreground/50 tabular shrink-0 mt-px">
                {new Date(n.created_at).toLocaleDateString([], { day: "numeric", month: "short" })}
              </span>
              <span className="min-w-0 flex-1 text-foreground/80 break-words">{n.body}</span>
              <button
                type="button" onClick={() => remove(n.id)} title="Delete this note"
                className="opacity-0 group-hover/note:opacity-100 shrink-0 text-muted-foreground hover:text-destructive"
              >
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {adding ? (
        <div className="flex items-center gap-1.5">
          <Input
            value={body} onChange={(e) => setBody(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") add();
              if (e.key === "Escape") { setAdding(false); setBody(""); }
            }}
            autoFocus disabled={busy}
            placeholder="what we discussed, what changed…"
            className="h-7 text-xs"
          />
          <button type="button" onClick={() => { setAdding(false); setBody(""); }}
            className="text-[11px] text-muted-foreground hover:text-foreground shrink-0">
            cancel
          </button>
        </div>
      ) : (
        <button
          type="button" onClick={() => setAdding(true)}
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
        >
          <MessageSquarePlus className="h-3 w-3" />
          {notes.length ? "Add update" : "Add a note"}
        </button>
      )}
    </div>
  );
}
