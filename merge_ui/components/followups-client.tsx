"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CalendarClock, Check, Pencil, Trash2, Undo2, MessageSquare, Plus, ListChecks, CalendarRange, Building2 } from "lucide-react";
import { api, type FollowupRow, type PersonRow, type CircleDueRow, type FollowupPriority } from "@/lib/api";
import { defaultDueDate, dueLabel, channelLabel, daysLate, groupOf, compareGroups, groupDropTarget, lastSpokeLabel, type Group } from "@/lib/followups";
import { FollowupEditor } from "@/components/followup-editor";
import { FollowupNotes } from "@/components/followup-notes";
import { FollowupBulkBar } from "@/components/followup-bulk-bar";
import { PersonPicker } from "@/components/person-picker";
import { PersonAvatar } from "@/components/person-avatar";
import { PrioritySelect, priorityRank, PRIORITY_ORDER, PRIORITY_META } from "@/components/followup-priority";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

export function FollowupsClient() {
  const [rows, setRows] = useState<FollowupRow[] | null>(null);
  const [scope, setScope] = useState<"open" | "all">("open");
  // Which priorities to show. Empty = all of them, which is the normal view;
  // a filter is something you turn ON to work one level at a time ("just the
  // high ones this morning") and off again.
  const [levels, setLevels] = useState<Set<FollowupPriority>>(new Set());
  const [person, setPerson] = useState<PersonRow | null>(null);
  // Recommended date for a new follow-up: the next day under the per-day cap,
  // fetched when a person is picked so the add form opens on a day with room.
  const [addSlot, setAddSlot] = useState<string>(defaultDueDate());
  const [editing, setEditing] = useState<string | null>(null);
  // Selection is behind a mode rather than always-on checkboxes: with three
  // follow-ups the checkboxes are noise, and with eighty you want them. The
  // mode makes it a choice instead of a permanent tax on the common view.
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [distributing, setDistributing] = useState(false);
  // Drag state: which card is in flight, and which day group it's hovering.
  const [dragId, setDragId] = useState<string | null>(null);
  const [dragOverKey, setDragOverKey] = useState<string | null>(null);
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
    | { kind: "followup"; key: string; urgency: number; rank: number; group: Group; f: FollowupRow }
    | { kind: "cadence"; key: string; urgency: number; rank: number; group: Group; c: CircleDueRow };

  const filtering = levels.size > 0;
  const shown = (rows ?? []).filter((f) => !filtering || levels.has(f.priority));

  const feed: Feed[] = [
    ...shown.map((f): Feed => ({
      kind: "followup", key: `f:${f.id}`, f,
      // Settled ones are a record, not a debt — their own group at the end.
      group: groupOf(f.due_date, f.due_time,
                     f.connected || f.status === "cancelled"),
      urgency: daysLate(f.due_date, f.due_time),
      rank: priorityRank(f.priority),
    })),
    // A cadence nudge isn't a follow-up yet, so it carries no priority — it
    // would be dishonest to show it under any level, and hiding it is what
    // filtering by priority means.
    ...(scope === "open" && !filtering ? cadenceDue : []).map((c): Feed => ({
      kind: "cadence", key: `c:${c.person_id}`, c,
      // A cadence nudge only exists because it's already late.
      group: { key: "overdue", label: "Overdue" },
      // "never spoken" has no number but is the oldest debt there is.
      urgency: c.days_overdue ?? 10_000,
      // A nudge nobody has weighted yet sits with the ordinary ones.
      rank: priorityRank("mid"),
    })),
    // Priority BEFORE lateness: the pile is worked top-down, and a high-priority
    // conversation that is four days late matters more than a low-priority one
    // that has been rotting for a year. Lateness still orders within a level.
  ].sort((a, b) => a.rank - b.rank || b.urgency - a.urgency);

  // Grouped rather than one long list: overdue and next Thursday want
  // different attention, and a flat ordering makes you re-derive that from
  // every date. Groups are discovered from the items rather than declared up front, since
  // every future day is its own group. Empty ones can't exist by construction.
  const groups = Object.values(
    feed.reduce<Record<string, { key: string; label: string; items: Feed[] }>>((acc, i) => {
      (acc[i.group.key] ??= { key: i.group.key, label: i.group.label, items: [] })
        .items.push(i);
      return acc;
    }, {}),
  ).sort((a, b) => compareGroups(a.key, b.key));

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

  async function create(v: { due_date: string | null; due_time: string | null; topic: string | null; priority: FollowupPriority }) {
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

  // How many of each level are loaded, so the filter says what it would show
  // before you click it. Counted over everything fetched, not the filtered
  // view, or the numbers would change as you filter and mean nothing.
  const levelCounts = (rows ?? []).reduce<Record<string, number>>((acc, f) => {
    acc[f.priority] = (acc[f.priority] ?? 0) + 1;
    return acc;
  }, {});

  function toggleLevel(p: FollowupPriority) {
    setLevels((prev) => {
      const next = new Set(prev);
      next.has(p) ? next.delete(p) : next.add(p);
      // A selection must never outlive what you can see: leaving a hidden row
      // selected means the bulk bar deletes or reschedules something that
      // isn't on screen. Anything the new filter hides drops out of it.
      setSelected((picked) => new Set(
        [...picked].filter((id) => {
          const row = (rows ?? []).find((r) => r.id === id);
          return row ? next.size === 0 || next.has(row.priority) : false;
        }),
      ));
      return next;
    });
  }

  // Overdue OWED follow-ups (not cadence nudges, which aren't real follow-ups
  // and can't be moved) — gates the Distribute button and sizes its prompt.
  const overdueCount = (rows ?? []).filter(
    (f) => !f.connected && f.status === "open"
      && groupOf(f.due_date, f.due_time, false).key === "overdue",
  ).length;

  /** Spread the backlog across the coming days, capped at 10/day, highest
   *  priority first. The server does the assignment in one transaction; we
   *  just reload. */
  async function distribute() {
    if (overdueCount === 0) return;
    if (!confirm(
      `Spread ${overdueCount} overdue follow-up${overdueCount === 1 ? "" : "s"} ` +
      `across the coming days, up to 10 a day.\n\n` +
      `High-priority ones take the earliest days and push lower-priority ones ` +
      `back. Today, and anything with a time on it, is left alone.`
    )) return;
    setDistributing(true);
    try {
      const { moved, days } = await api.distributeFollowups();
      toast.success(moved
        ? `Moved ${moved} across ${days} day${days === 1 ? "" : "s"}`
        : "Nothing to distribute");
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't distribute");
    } finally {
      setDistributing(false);
    }
  }

  /** Drop a card onto a day group → re-date it to that day (time preserved).
   *  Dropping onto No date clears the date. Same-day drops are a no-op. */
  async function moveToGroup(id: string, key: string) {
    const target = groupDropTarget(key);
    if (!target.droppable) return;
    const f = (rows ?? []).find((r) => r.id === id);
    if (!f) return;
    if ((target.date ?? null) === (f.due_date ?? null)) return;   // no move
    try {
      // Only due_date is sent, so due_time is left as it was.
      await api.patchFollowup(id, { due_date: target.date });
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't move that");
    }
  }

  // --- bulk -------------------------------------------------------------

  function toggleOne(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  /** Select-all for one group, so "everything overdue" is one click rather
   *  than eighty. Toggles off when the whole group is already selected. */
  function toggleGroup(ids: string[]) {
    setSelected((prev) => {
      const next = new Set(prev);
      const all = ids.every((id) => next.has(id));
      ids.forEach((id) => (all ? next.delete(id) : next.add(id)));
      return next;
    });
  }

  /** Every bulk action ends the same way: report what actually changed, drop
   *  the selection, reload. `changed` can be lower than what you picked when a
   *  row was settled or deleted from another tab — saying so beats pretending. */
  async function runBulk(
    verb: string, action: (ids: string[]) => Promise<{ changed: number }>,
  ) {
    const ids = [...selected];
    if (ids.length === 0) return;
    setBulkBusy(true);
    try {
      const { changed } = await action(ids);
      toast.success(
        changed === ids.length
          ? `${changed} ${changed === 1 ? "follow-up" : "follow-ups"} ${verb}`
          : `${changed} of ${ids.length} ${verb} — the rest were already gone`,
      );
      setSelected(new Set());
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : `Couldn't ${verb} those`);
    } finally {
      setBulkBusy(false);
    }
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
                  <PersonAvatar personId={c.person_id} displayName={c.display_name}
                    size="sm" className="shrink-0 hidden sm:flex" />
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
                    initialPriority={f.priority}
                    submitLabel="Save"
                    onSubmit={(v) => save(f.id, v)}
                    onCancel={() => setEditing(null)}
                  />
                </li>
              );
            }
            const picked = selected.has(f.id);
            // Draggable to another day, except while selecting or editing —
            // both want the pointer for their own gestures.
            const canDrag = !selectMode && editing !== f.id;
            return (
              <li key={f.id}
                draggable={canDrag}
                onDragStart={(e) => {
                  setDragId(f.id);
                  e.dataTransfer.setData("text/plain", f.id);
                  e.dataTransfer.effectAllowed = "move";
                }}
                onDragEnd={() => { setDragId(null); setDragOverKey(null); }}
                className={cn("group/row flex items-start gap-3 px-3 sm:px-4 py-2.5",
                  canDrag && "cursor-grab active:cursor-grabbing",
                  picked && "bg-primary/10",
                  dragId === f.id && "opacity-40")}>
                {/* In select mode the leading control becomes the checkbox.
                    Two round controls side by side — tick and checkbox — read
                    as one thing and get mis-clicked; ticking is what the bar
                    does while you're selecting anyway. */}
                {selectMode ? (
                  <button
                    type="button"
                    onClick={() => toggleOne(f.id)}
                    role="checkbox"
                    aria-checked={picked}
                    aria-label={`Select the follow-up with ${f.display_name}`}
                    className={cn("mt-0.5 grid place-items-center h-5 w-5 shrink-0 rounded border",
                      picked
                        ? "bg-primary border-primary text-primary-foreground"
                        : "border-border text-transparent hover:border-foreground/40")}
                  >
                    <Check className="h-3 w-3" />
                  </button>
                ) : (
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
                )}

                {/* The face is what makes a list of fifty scannable — you
                    recognise people before you read them. */}
                <PersonAvatar personId={f.person_id} displayName={f.display_name}
                  size="sm" className="mt-0.5 shrink-0 hidden sm:flex" />

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

                  {/* Where they work and how cold it's gone: the two facts that
                      decide whether this is worth doing today, without opening
                      the contact to find them. */}
                  {(f.company || f.last_interaction_at) && (
                    <div className="flex items-center gap-2 flex-wrap text-xs text-muted-foreground mt-0.5">
                      {f.company && (
                        <span className="inline-flex items-center gap-1 min-w-0">
                          <Building2 className="h-3 w-3 shrink-0" />
                          {f.company_id ? (
                            <Link href={`/companies/${f.company_id}`} className="truncate hover:underline">
                              {f.company}
                            </Link>
                          ) : (
                            <span className="truncate">{f.company}</span>
                          )}
                        </span>
                      )}
                      {f.last_interaction_at && (
                        <span className="opacity-70 shrink-0">
                          spoke {lastSpokeLabel(f.last_interaction_at)}
                        </span>
                      )}
                    </div>
                  )}

                  {/* Who they are, in one line, from their role card. */}
                  {f.context && (
                    <p className="text-xs text-muted-foreground/75 mt-0.5 line-clamp-2">
                      {f.context}
                    </p>
                  )}

                  {f.topic && (
                    <p className="text-xs text-foreground/80 mt-1 inline-flex items-start gap-1">
                      <MessageSquare className="h-3 w-3 mt-0.5 shrink-0" />
                      <span className="min-w-0">{f.topic}</span>
                    </p>
                  )}
                  <FollowupNotes followup={f} onChanged={replaceRow} />
                </div>

                {/* Per-row actions are hidden while selecting: the bar owns
                    what happens to the selection, and a stray pencil-click
                    mid-selection loses it. */}
                {!selectMode && (
                  <>
                    {/* Always visible, unlike the edit/delete buttons: it's
                        the one thing you set while scanning, and hunting for
                        a hover-only control fifty times is how triage stops
                        happening. */}
                    {!f.connected && (
                      <PrioritySelect
                        value={f.priority}
                        onChange={(v) => save(f.id, { priority: v })}
                        ariaLabel={`Priority of the follow-up with ${f.display_name}`}
                      />
                    )}
                    <button type="button" onClick={() => setEditing(f.id)} title="Edit day, time, priority or topic"
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
                  </>
                )}
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
          Conversations you owe people — before any of it is a deal. Highest priority
          first, then most overdue, mixed with anyone your circle cadences say it&apos;s
          been too long with (<span className="text-foreground">Plan</span> turns one of
          those into a real follow-up). Talk to them on Telegram or email and it settles
          itself; for a call or a coffee, tick it yourself.
        </p>
      </div>

      {/* Add */}
      <div className="flex items-end gap-2 flex-wrap">
        <div>
          <label className="text-[11px] text-muted-foreground block mb-1">Who</label>
          <PersonPicker
            onPick={async (p) => {
              setPerson(p);
              try { const { date } = await api.nextFollowupSlot(); setAddSlot(date); }
              catch { setAddSlot(defaultDueDate()); }
            }}
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
              initialDate={addSlot}
              submitLabel="Add"
              onSubmit={create}
              onCancel={() => setPerson(null)}
              autoFocusTopic
            />
          </div>
        )}
      </div>

      {/* Scope */}
      <div className="flex items-center gap-1 gap-y-1.5 flex-wrap text-xs">
        {(["open", "all"] as const).map((s) => (
          <button key={s} type="button" onClick={() => setScope(s)}
            className={cn("h-7 px-2.5 rounded-md border border-border",
              scope === s ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50")}>
            {s === "open" ? "Still owed" : "Everything"}
          </button>
        ))}

        <span className="h-4 w-px bg-border mx-1" aria-hidden="true" />

        {/* Priority filter. Several can be on at once — "high and mid" is a
            real way to work a backlog — and none on means all of them, so the
            unfiltered list needs no "All" button to get back to. */}
        {PRIORITY_ORDER.map((p) => {
          const on = levels.has(p);
          const n = levelCounts[p] ?? 0;
          return (
            <button
              key={p} type="button" onClick={() => toggleLevel(p)}
              aria-pressed={on}
              title={on
                ? `Stop showing only ${PRIORITY_META[p].label.toLowerCase()} priority`
                : `Show only ${PRIORITY_META[p].label.toLowerCase()} priority`}
              // An ON filter has to be unmistakable even for 'mid', whose own
              // chip is deliberately quiet — so being on also means the
              // pressed look the scope buttons use.
              className={cn("inline-flex items-center gap-1 h-7 px-2 rounded-md border",
                "border-border text-muted-foreground hover:bg-accent/50",
                on && PRIORITY_META[p].chip,
                on && "bg-accent text-foreground font-medium")}
            >
              <span className={cn("h-1.5 w-1.5 rounded-full", PRIORITY_META[p].dot)} aria-hidden="true" />
              {PRIORITY_META[p].label}
              {n > 0 && <span className="tabular opacity-60">{n}</span>}
            </button>
          );
        })}
        {scope === "open" && overdueCount > 0 && (
          <button
            type="button"
            onClick={distribute}
            disabled={distributing}
            title={`Spread ${overdueCount} overdue across the coming days, up to 10 a day — high priority takes the earliest`}
            className="ml-auto inline-flex items-center gap-1 h-7 px-2.5 rounded-md border border-border text-muted-foreground hover:bg-accent/50 disabled:opacity-50"
          >
            <CalendarRange className="h-3.5 w-3.5" />
            {distributing ? "Distributing…" : "Distribute overdue"}
          </button>
        )}
        <button
          type="button"
          onClick={() => { setSelectMode((v) => !v); setSelected(new Set()); setEditing(null); }}
          title="Pick several and reschedule, settle or drop them together"
          className={cn("inline-flex items-center gap-1 h-7 px-2.5 rounded-md border border-border",
            scope !== "open" || overdueCount === 0 ? "ml-auto" : "",
            selectMode ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50")}
        >
          <ListChecks className="h-3.5 w-3.5" />
          {selectMode ? "Done" : "Select"}
        </button>
      </div>

      {/* One list, both kinds, most overdue first. A planned follow-up and a
          cadence nudge are different objects but the same question — "who am I
          overdue with" — so they share a list and are told apart by their row,
          not by living in separate sections. */}
      {rows === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : feed.length === 0 ? (
        <div className="rounded-lg border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
          {filtering
            ? "Nothing at that priority. Clear the filter to see the rest."
            : scope === "open"
              ? "Nothing owed. Add someone above, or you're genuinely on top of it."
              : "No follow-ups yet."}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {groups.map((g) => {
            // Cadence nudges aren't follow-ups yet — nothing to select.
            const ids = g.items.filter((i) => i.kind === "followup").map((i) => i.f.id);
            const allPicked = ids.length > 0 && ids.every((id) => selected.has(id));
            const drop = groupDropTarget(g.key);
            const dragActive = dragId !== null && drop.droppable;
            return (
              <div
                key={g.key}
                onDragOver={(e) => {
                  if (!dragActive) return;
                  e.preventDefault();               // allow the drop
                  if (dragOverKey !== g.key) setDragOverKey(g.key);
                }}
                onDragLeave={() => setDragOverKey((k) => (k === g.key ? null : k))}
                onDrop={(e) => {
                  e.preventDefault();
                  const id = dragId ?? e.dataTransfer.getData("text/plain");
                  setDragOverKey(null);
                  if (id) void moveToGroup(id, g.key);
                }}
                className={cn("rounded-lg transition-colors",
                  dragActive && dragOverKey === g.key && "ring-2 ring-primary/60 bg-accent/20")}
              >
                <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5 px-0.5 flex items-center gap-2">
                  {g.label}
                  <span className="font-normal normal-case">· {g.items.length}</span>
                  {selectMode && ids.length > 0 && (
                    <button
                      type="button" onClick={() => toggleGroup(ids)}
                      className="font-normal normal-case tracking-normal text-[11px] text-primary hover:underline"
                    >
                      {allPicked ? "none" : `all ${ids.length}`}
                    </button>
                  )}
                </h2>
                <ul className="rounded-lg border border-border overflow-hidden bg-card/40 divide-y divide-border">
                  {g.items.map((item) => renderItem(item))}
                </ul>
              </div>
            );
          })}
        </div>
      )}

      {selectMode && selected.size > 0 && (
        <FollowupBulkBar
          count={selected.size}
          busy={bulkBusy}
          onSetDate={(due_date, due_time) =>
            runBulk("rescheduled", (ids) => api.bulkPatchFollowups(ids, { due_date, due_time }))}
          onSetPriority={(priority) =>
            runBulk("reprioritised", (ids) => api.bulkPatchFollowups(ids, { priority }))}
          onConnect={() =>
            runBulk("marked connected", (ids) => api.bulkPatchFollowups(ids, { connected: true }))}
          onDelete={() => {
            if (!confirm(`Drop ${selected.size} follow-up${selected.size === 1 ? "" : "s"}?`)) return;
            runBulk("dropped", (ids) => api.bulkDeleteFollowups(ids));
          }}
          onClear={() => setSelected(new Set())}
        />
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
