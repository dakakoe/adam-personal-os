"use client";

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

/**
 * The date / time / topic form, shared by create and edit on both the
 * Follow-ups page and the contact panel — so editing a follow-up offers
 * exactly the fields that made it, and the two surfaces can't drift apart.
 *
 * Time is OPTIONAL by design: most follow-ups are "some point on Tuesday", and
 * forcing a time makes you invent one that reads as a commitment you never
 * made. Leave it blank for an all-day item.
 */
export function FollowupEditor({
  initialDate, initialTime, initialTopic, submitLabel, onSubmit, onCancel, autoFocusTopic,
}: {
  initialDate: string;
  initialTime?: string | null;
  initialTopic?: string | null;
  submitLabel: string;
  onSubmit: (v: { due_date: string; due_time: string | null; topic: string | null }) => Promise<void>;
  onCancel?: () => void;
  autoFocusTopic?: boolean;
}) {
  const [date, setDate] = useState(initialDate);
  // The API returns "15:00:00"; <input type=time> wants "15:00".
  const [time, setTime] = useState((initialTime ?? "").slice(0, 5));
  const [topic, setTopic] = useState(initialTopic ?? "");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!date) return;
    setBusy(true);
    try {
      await onSubmit({
        due_date: date,
        due_time: time ? `${time}:00` : null,
        topic: topic.trim() || null,
      });
    } finally { setBusy(false); }
  }

  return (
    <div className="flex items-end gap-2 flex-wrap rounded-lg border border-border bg-card/40 p-2.5">
      <div>
        <label className="text-[11px] text-muted-foreground block mb-1">Day</label>
        <Input type="date" value={date} onChange={(e) => setDate(e.target.value)}
          className="h-8 text-sm w-40" />
      </div>
      <div>
        <label className="text-[11px] text-muted-foreground block mb-1">
          Time <span className="opacity-60">(optional)</span>
        </label>
        <Input type="time" value={time} onChange={(e) => setTime(e.target.value)}
          title="Leave blank for an all-day follow-up"
          className="h-8 text-sm w-28" />
      </div>
      <div className="flex-1 min-w-[12rem]">
        <label className="text-[11px] text-muted-foreground block mb-1">What should we discuss</label>
        <Input value={topic} onChange={(e) => setTopic(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          autoFocus={autoFocusTopic}
          placeholder="their new role, the intro, the fund…" className="h-8 text-sm" />
      </div>
      <Button size="sm" onClick={submit} disabled={busy || !date} className="h-8">{submitLabel}</Button>
      {onCancel && (
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy} className="h-8">Cancel</Button>
      )}
    </div>
  );
}
