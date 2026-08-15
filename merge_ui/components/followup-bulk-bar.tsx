"use client";

import { useState } from "react";
import { Check, Trash2, X, CalendarDays } from "lucide-react";
import { defaultDueDate } from "@/lib/followups";

/** The bar that appears once you've selected follow-ups.
 *
 *  Pinned to the bottom of the viewport rather than sitting above the list:
 *  the selection that makes it appear is usually made by scrolling down
 *  through eighty overdue rows, and a control at the top would be off-screen
 *  exactly when you need it. */
export function FollowupBulkBar({
  count, onSetDate, onConnect, onDelete, onClear, busy,
}: {
  count: number;
  onSetDate: (due_date: string | null, due_time: string | null) => void;
  onConnect: () => void;
  onDelete: () => void;
  onClear: () => void;
  busy: boolean;
}) {
  const [date, setDate] = useState(defaultDueDate());
  const [time, setTime] = useState("");

  return (
    <div className="sticky bottom-3 z-20 mx-auto w-full max-w-3xl">
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 shadow-lg shadow-black/40">
        <span className="text-sm font-medium tabular shrink-0">
          {count} selected
        </span>

        <span className="h-4 w-px bg-border mx-1" aria-hidden="true" />

        <CalendarDays className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        <input
          type="date" value={date} onChange={(e) => setDate(e.target.value)}
          className="h-8 px-2 rounded-md border border-border bg-background text-sm"
          aria-label="New due date"
        />
        <input
          type="time" value={time} onChange={(e) => setTime(e.target.value)}
          className="h-8 px-2 rounded-md border border-border bg-background text-sm"
          aria-label="New due time (optional)"
        />
        <button
          type="button" disabled={busy}
          onClick={() => onSetDate(date || null, time || null)}
          // An empty date is a real instruction, not a mistake: it moves the
          // selection to the No-date pile, which is how you park things.
          title={date ? "Move the selected follow-ups to this day" : "Clear the date — move them to No date"}
          className="h-8 px-3 rounded-md border border-border text-sm hover:bg-accent disabled:opacity-50"
        >
          {date ? "Set date" : "Clear date"}
        </button>

        <span className="h-4 w-px bg-border mx-1" aria-hidden="true" />

        <button
          type="button" onClick={onConnect} disabled={busy}
          title="Mark every selected follow-up as connected"
          className="inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-sm text-emerald-400 hover:bg-accent disabled:opacity-50"
        >
          <Check className="h-3.5 w-3.5" /> Connected
        </button>
        <button
          type="button" onClick={onDelete} disabled={busy}
          title="Drop every selected follow-up"
          className="inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-sm text-muted-foreground hover:bg-destructive hover:text-destructive-foreground disabled:opacity-50"
        >
          <Trash2 className="h-3.5 w-3.5" /> Delete
        </button>

        <button
          type="button" onClick={onClear} disabled={busy}
          className="ml-auto inline-flex items-center gap-1 h-8 px-2 rounded-md text-sm text-muted-foreground hover:bg-accent disabled:opacity-50"
        >
          <X className="h-3.5 w-3.5" /> Clear
        </button>
      </div>
    </div>
  );
}
