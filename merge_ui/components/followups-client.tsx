"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CalendarClock, Check, Pencil, Trash2, Undo2, MessageSquare, AlertCircle, Plus } from "lucide-react";
import { api, type FollowupRow, type PersonRow, type CircleDueRow } from "@/lib/api";
import { defaultDueDate, dueLabel, channelLabel, daysLate, bucketOf, BUCKET_LABEL, BUCKET_ORDER, type Bucket } from "@/lib/followups";
import { FollowupEditor } from "@/components/followup-editor";
import { FollowupNotes } from "@/components/followup-notes";
import { PersonPicker } from "@/components/person-picker";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

export function FollowupsClient() {
  const [rows, setRows] = useState<FollowupRow[] | null>(null);
  const [scope, setScope] = useState<"open" | "all">("open");
  const [person, setPerson] = useState<PersonRow | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  // Contacts your circle cadences say you're overdue with. Same question this
  // page already answers — "who am I overdue with" — so it belongs here rather
  // than on a second page you have to remember to visit.
  const [cadenceDue, setCadenceDue] = useState<CircleDueRow[]>([]);

  const load = useCallback(async () => {
    try {
      const [fu, due] = await Promise.all([
        api.listFollowups({ scope, limit: 200 }),
        // Always the OPEN set regardless of scope: a cadence suggestion is
        // only meaningful against what's still owed.
        api.listFollowups({ scope: "open", limit: 500 }).then(
          (open) => api.circlesDue({ limit: 100 }).then((d) => ({ open, d })),
        ),
      ]);
      setRows(fu);
      // A contact with an open follow-up is already handled — listing them
      // again as "overdue by cadence" is the same debt counted twice.
      const planned = new Set(due.open.map((f) => f.person_id));
      setCadenceDue(due.d.filter((c) => !planned.has(c.person_id)));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to load follow-ups");
      setRows([]);
    }
  }, [scope]);
  useEffect(() => { load(); }, [load]);

  // Cadence rows only exist as "still owed" — there's no settled version of
  // one — so they're absent from the Everything view, which is the history of
  // follow-ups proper.
  type Feed =
    | { kind: "followup"; key: string; urgency: number; bucket: Bucket; f: FollowupRow }
    | { kind: "cadence"; key: string; urgency: number; bucket: Bucket; c: CircleDueRow };

  const feed: Feed[] = [
    ...(rows ?? []).map((f): Feed => ({
      kind: "followup", key: `f:${f.id}`, f,
      // Settled ones are a record, not a debt — their own group at the end.
      bucket: f.connected || f.status === "cancelled"
        ? "settled" as Bucket
        : bucketOf(f.due_date, f.due_time),
      urgency: daysLate(f.due_date, f.due_time),
    })),
    ...(scope === "open" ? cadenceDue : []).map((c): Feed => ({
      kind: "cadence", key: `c:${c.person_id}`, c,
      // A cadence nudge only exists because it's already late.
      bucket: "overdue" as Bucket,
      // "never spoken" has no number but is the oldest debt there is.
      urgency: c.days_overdue ?? 10_000,
    })),
  ].sort((a, b) => b.urgency - a.urgency);

  // Grouped rather than one long list: "overdue" and "next week" want
  // different attention, and a flat ordering makes you re-derive that from
  // every date. Empty groups are omitted entirely.
  const groups = BUCKET_ORDER
    .map((b) => ({ bucket: b, items: feed.filter((i) => i.bucket === b) }))
    .filter((g) => g.items.length > 0);

  // Turn a cadence nudge into a real follow-up: dated today, no topic yet.
  // It vanishes from the cadence list on reload because it now has one.
  async function planFromCadence(c: CircleDueRow) {
    try {
      await api.createFollowup({ person_id: c.person_id, due_date: defaultDueDate() });
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't plan that follow-up");
    }
  }

  async function create(v: { due_date: string | null; due_time: string | null; topic: string | null }) {
    if (!person) return;
    try {
      await api.createFollowup({ person_id: person.person_id, ...v });
      setPerson(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't add the follow-up");
    }
  }

  // The note endpoints hand back the whole follow-up, so patch it in place
  // instead of refetching the list and losing scroll position.
  function replaceRow(updated: FollowupRow) {
    setRows((prev) => (prev ?? []).map((r) => (r.id === updated.id ? updated : r)));
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
    if (!confirm(`Drop the follow-up with ${f.display_name}?`)) return;
    try { await api.deleteFollowup(f.id); await load(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }

  // One row, whichever kind it is. Hoisted out of the JSX so the grouped
  // render below stays readable — the row markup didn't change.
  function renderItem(item: Feed) {
            if (item.kind === "cadence") {
              const c = item.c;
              return (
                <li key={item.key} className="group flex items-center gap-3 px-3 sm:px-4 py-2.5">
                  {/* No tick: there's nothing to complete until it's a plan. */}
                  <span className="h-5 w-5 shrink-0" aria-hidden="true" />
                  <Link href={`/persons/${c.person_id}`} className="min-w-0 flex-1 text-sm font-medium hover:underline">
                    {c.display_name}
                    {c.circle_label && (
                      <span className="ml-2 text-[10px] uppercase tracking-wide text-muted-foreground border border-border rounded px-1">
                        {c.circle_label}
                      </span>
                    )}
                  </Link>
                  <span className="text-xs tabular text-amber-400/80 shrink-0">
                    {cadenceLabel(c.days_overdue)}
                  </span>
                  <button
                    type="button" onClick={() => planFromCadence(c)}
                    title={`Plan a follow-up with ${c.display_name}`}
                    className="inline-flex items-center gap-1 h-6 px-1.5 rounded border border-border text-[11px] text-muted-foreground hover:bg-accent opacity-0 group-hover:opacity-100 focus:opacity-100 shrink-0"
                  >
                    <Plus className="h-3 w-3" /> Plan
                  </button>
                </li>
              );
            }
            const f = item.f;
            const d = dueLabel(f.due_date, f.due_time);
            if (editing === f.id) {
              return (
                <li key={f.id} className="p-2">
                  <div className="text-xs text-muted-foreground mb-1.5 px-1">{f.display_name}</div>
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
              <li key={f.id} className="flex items-start gap-3 px-3 sm:px-4 py-2.5">
                <button
                  type="button"
                  onClick={() => tick(f, !f.connected)}
                  title={f.connected ? "Reopen — we haven't really spoken" : "Mark as connected"}
                  className={cn("mt-0.5 grid place-items-center h-5 w-5 shrink-0 rounded border",
                    f.connected
                      ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-400"
                      : "border-border text-transparent hover:border-foreground/40")}
                >
                  <Check className="h-3 w-3" />
                </button>

                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <Link href={`/persons/${f.person_id}`}
                      className={cn("text-sm font-medium hover:underline",
                        f.connected && "text-muted-foreground line-through")}>
                      {f.display_name}
                    </Link>
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
                  </div>
                  {f.topic && (
                    <p className="text-xs text-muted-foreground mt-0.5 inline-flex items-start gap-1">
                      <MessageSquare className="h-3 w-3 mt-0.5 shrink-0" />
                      <span className="min-w-0">{f.topic}</span>
                    </p>
                  )}
                  <FollowupNotes followup={f} onChanged={replaceRow} />
                </div>

                <button type="button" onClick={() => setEditing(f.id)} title="Edit day, time or topic"
                  className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-accent">
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                {f.connected && (
                  <button type="button" onClick={() => tick(f, false)} title="Reopen"
                    className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-accent">
                    <Undo2 className="h-3.5 w-3.5" />
                  </button>
                )}
                <button type="button" onClick={() => remove(f)} title="Drop this follow-up"
                  className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            );
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold inline-flex items-center gap-2">
          <CalendarClock className="h-5 w-5 text-primary" /> Follow-ups
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Conversations you owe people — before any of it is a deal. Most overdue first,
          mixed with anyone your circle cadences say it&apos;s been too long with
          (<span className="text-foreground">Plan</span> turns one of those into a real
          follow-up). Talk to them on Telegram or email and it settles itself; for a call
          or a coffee, tick it yourself.
        </p>
      </div>

      {/* Add */}
      <div className="flex items-end gap-2 flex-wrap">
        <div>
          <label className="text-[11px] text-muted-foreground block mb-1">Who</label>
          <PersonPicker
            onPick={(p) => setPerson(p)}
            trigger={
              <span className="inline-flex items-center h-8 px-2.5 rounded-md border border-border text-sm hover:bg-accent cursor-pointer min-w-[10rem]">
                {person ? person.display_name : <span className="text-muted-foreground">Pick someone…</span>}
              </span>
            }
          />
        </div>
        {person && (
          <div className="flex-1 min-w-[20rem]">
            <FollowupEditor
              initialDate={defaultDueDate()}
              submitLabel="Add"
              onSubmit={create}
              onCancel={() => setPerson(null)}
              autoFocusTopic
            />
          </div>
        )}
      </div>

      {/* Scope */}
      <div className="flex items-center gap-1 text-xs">
        {(["open", "all"] as const).map((s) => (
          <button key={s} type="button" onClick={() => setScope(s)}
            className={cn("h-7 px-2.5 rounded-md border border-border",
              scope === s ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50")}>
            {s === "open" ? "Still owed" : "Everything"}
          </button>
        ))}
      </div>

      {/* One list, both kinds, most overdue first. A planned follow-up and a
          cadence nudge are different objects but the same question — "who am I
          overdue with" — so they share a list and are told apart by their row,
          not by living in separate sections. */}
      {rows === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : feed.length === 0 ? (
        <div className="rounded-lg border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
          {scope === "open"
            ? "Nothing owed. Add someone above, or you're genuinely on top of it."
            : "No follow-ups yet."}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {groups.map((g) => (
            <div key={g.bucket}>
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5 px-0.5">
                {BUCKET_LABEL[g.bucket]}
                <span className="font-normal normal-case"> · {g.items.length}</span>
              </h2>
              <ul className="rounded-lg border border-border overflow-hidden bg-card/40 divide-y divide-border">
                {g.items.map((item) => renderItem(item))}
              </ul>
            </div>
          ))}
        </div>
      )}

    </div>
  );
}

/** How late a cadence says you are. Mirrors the wording the Circles page used
 *  before this list moved here, so the phrasing didn't change under you. */
function cadenceLabel(d: number | null): string {
  if (d == null) return "never spoken";
  if (d <= 0) return "due now";
  if (d < 30) return `${d}d overdue`;
  if (d < 365) return `${Math.round(d / 30)}mo overdue`;
  return `${(d / 365).toFixed(1)}y overdue`;
}
