"use client";

import type { FollowupPriority } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * How much a follow-up matters, and the control for saying so.
 *
 * Three levels, because the question the list has to answer is "which of these
 * eighty first" — a five-point scale would be a slider you fiddle with instead
 * of an answer. 'mid' is the unmarked default: most follow-ups are ordinary,
 * and only the ends of the scale carry information.
 *
 * A NATIVE select, not a menu: it's one tap to open on a phone, one click to
 * pick anywhere, needs no portal, and can't be left half-open behind a dialog.
 */

export const PRIORITY_META: Record<FollowupPriority, {
  label: string;      // what the control shows
  chip: string;       // classes for the chip/select itself
  dot: string;        // classes for the leading dot
}> = {
  high: {
    label: "High",
    chip: "text-red-300 border-red-500/40 bg-red-500/10 hover:bg-red-500/15",
    dot: "bg-red-400",
  },
  mid: {
    label: "Mid",
    // The default is deliberately quiet: if every row shouted, none would.
    chip: "text-muted-foreground border-border hover:bg-accent",
    dot: "bg-muted-foreground/50",
  },
  low: {
    label: "Low",
    chip: "text-sky-300/70 border-sky-500/30 bg-sky-500/5 hover:bg-sky-500/10",
    dot: "bg-sky-400/60",
  },
};

export const PRIORITY_ORDER: FollowupPriority[] = ["high", "mid", "low"];

/** Sort key: high first. Matches the server's ordering exactly. */
export function priorityRank(p: FollowupPriority | null | undefined): number {
  return p === "high" ? 0 : p === "low" ? 2 : 1;
}

export function PrioritySelect({
  value, onChange, disabled, className, ariaLabel,
}: {
  value: FollowupPriority;
  onChange: (v: FollowupPriority) => void;
  disabled?: boolean;
  className?: string;
  ariaLabel?: string;
}) {
  const meta = PRIORITY_META[value] ?? PRIORITY_META.mid;
  return (
    <div className={cn("relative shrink-0", className)}>
      <span
        className={cn("pointer-events-none absolute left-1.5 top-1/2 -translate-y-1/2 h-1.5 w-1.5 rounded-full", meta.dot)}
        aria-hidden="true"
      />
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as FollowupPriority)}
        aria-label={ariaLabel ?? "Priority"}
        title="How much this one matters — high ones get the earliest days when you distribute"
        className={cn(
          "appearance-none h-6 pl-4 pr-1.5 rounded border text-[11px] cursor-pointer",
          "disabled:opacity-50 disabled:cursor-default",
          meta.chip,
        )}
      >
        {PRIORITY_ORDER.map((p) => (
          <option key={p} value={p} className="bg-background text-foreground">
            {PRIORITY_META[p].label}
          </option>
        ))}
      </select>
    </div>
  );
}

/** Read-only marker, for places too tight for the control (the contact panel).
 *  'mid' renders nothing at all — the absence of a mark IS "ordinary". */
export function PriorityMark({ value }: { value: FollowupPriority }) {
  if (value === "mid") return null;
  const meta = PRIORITY_META[value];
  return (
    <span
      className={cn("inline-flex items-center gap-1 text-[10px] uppercase tracking-wide rounded border px-1", meta.chip)}
      title={`${meta.label} priority`}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} aria-hidden="true" />
      {meta.label}
    </span>
  );
}
