"use client";

import { useCallback, useEffect, useState } from "react";
import { CalendarClock, Check, Pencil, Plus, Trash2, X } from "lucide-react";
import { api, type FollowupRow } from "@/lib/api";
import { defaultDueDate, dueLabel, channelLabel } from "@/lib/followups";
import { FollowupEditor } from "@/components/followup-editor";
import { FollowupNotes } from "@/components/followup-notes";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

/**
 * Follow-ups for one contact, on their own page — where you actually decide to
 * owe someone a conversation. The pipeline page answers "who do I owe"; this
 * answers "what do I owe *them*", and lets you add one without leaving.
 *
 * Settled ones stay listed (dimmed) rather than disappearing, so the panel
 * doubles as a record of when you last deliberately reconnected and how.
 */
export function PersonFollowups({
  personId, personName,
}: {
  personId: string;
  personName: string;
}) {
  const [rows, setRows] = useState<FollowupRow[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.listFollowups({ scope: "all", person_id: personId, limit: 50 }));
    } catch {
      setRows([]);   // a failed panel shouldn't take the contact page with it
    }
  }, [personId]);
  useEffect(() => { load(); }, [load]);

  async function create(v: { due_date: string | null; due_time: string | null; topic: string | null }) {
    try {
      await api.createFollowup({ person_id: personId, ...v });
      setAdding(false);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't add the follow-up");
    }
  }

  async function save(id: string, v: Record<string, unknown>) {
    try { await api.patchFollowup(id, v); setEditing(null); await load(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
  }

  async function tick(f: FollowupRow, connected: boolean) {
    try { await api.patchFollowup(f.id, { connected }); await load(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
  }

  async function remove(f: FollowupRow) {
    try { await api.deleteFollowup(f.id); await load(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }

  const open = (rows ?? []).filter((f) => !f.connected && f.status !== "cancelled");
  const settled = (rows ?? []).filter((f) => f.connected || f.status === "cancelled");

  return (
    <section>
      <div className="flex items-center justify-between gap-2 mb-2">
        <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider inline-flex items-center gap-1.5">
          <CalendarClock className="h-3.5 w-3.5" /> Follow-ups
          {open.length > 0 && <span className="font-normal normal-case">· {open.length} owed</span>}
        </h2>
        <button
          type="button"
          onClick={() => { setAdding((v) => !v); setEditing(null); }}
          className="inline-flex items-center gap-1 h-7 px-2 rounded-md border border-border text-xs text-muted-foreground hover:bg-accent"
          title={`Plan a conversation with ${personName}`}
        >
          {adding ? <X className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
          {adding ? "Cancel" : "Follow up"}
        </button>
      </div>

      {adding && (
        <div className="mb-2">
          <FollowupEditor
            initialDate={defaultDueDate()}
            submitLabel="Add"
            onSubmit={create}
            autoFocusTopic
          />
        </div>
      )}

      {rows === null ? null : rows.length === 0 ? (
        !adding && (
          <p className="text-xs text-muted-foreground">Nothing planned with {personName}.</p>
        )
      ) : (
        <ul className="rounded-lg border border-border overflow-hidden bg-card/40 divide-y divide-border">
          {[...open, ...settled].map((f) => {
            const d = dueLabel(f.due_date, f.due_time);
            if (editing === f.id) {
              return (
                <li key={f.id} className="p-2">
                  <FollowupEditor
                    initialDate={f.due_date}
                    initialTime={f.due_time}
                    initialTopic={f.topic}
                    submitLabel="Save"
                    onSubmit={(v) => save(f.id, v)}
                    onCancel={() => setEditing(null)}
                  />
                </li>
              );
            }
            return (
              <li key={f.id} className="flex items-start gap-2.5 px-3 py-2 group">
                <button
                  type="button"
                  onClick={() => tick(f, !f.connected)}
                  title={f.connected ? "Reopen" : "Mark as connected"}
                  className={cn("mt-0.5 grid place-items-center h-4 w-4 shrink-0 rounded border",
                    f.connected
                      ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-400"
                      : "border-border text-transparent hover:border-foreground/40")}
                >
                  <Check className="h-2.5 w-2.5" />
                </button>
                <div className="min-w-0 flex-1">
                  {f.connected ? (
                    <span className="text-[10px] uppercase tracking-wide text-emerald-400 border border-emerald-500/40 rounded px-1">
                      {channelLabel(f.connected_via, f.connected_source)}
                      {f.connected_at && ` · ${new Date(f.connected_at).toLocaleDateString([], { day: "numeric", month: "short" })}`}
                    </span>
                  ) : (
                    <span className={cn("text-xs tabular", d.overdue ? "text-amber-400" : "text-muted-foreground")}>
                      {d.text}
                    </span>
                  )}
                  {f.topic && (
                    <p className={cn("text-xs mt-0.5 break-words",
                      f.connected ? "text-muted-foreground/70" : "text-foreground/90")}>
                      {f.topic}
                    </p>
                  )}
                  <FollowupNotes followup={f} compact onChanged={(u) =>
                    setRows((prev) => (prev ?? []).map((r) => (r.id === u.id ? u : r)))} />
                </div>
                <button type="button" onClick={() => { setEditing(f.id); setAdding(false); }}
                  title="Edit day, time or topic"
                  className="grid place-items-center h-6 w-6 rounded text-muted-foreground hover:bg-accent">
                  <Pencil className="h-3 w-3" />
                </button>
                <button type="button" onClick={() => remove(f)} title="Drop this follow-up"
                  className="grid place-items-center h-6 w-6 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground">
                  <Trash2 className="h-3 w-3" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
