"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { CalendarPlus, Trash2, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { defaultDueDate } from "@/lib/followups";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

/**
 * Per-contact actions in the People list: plan a follow-up, or drop the
 * contact. Both are things you decide while scanning the list, and making
 * either one a trip to the contact page is what stops them happening.
 *
 * Hidden until the row is hovered (or focused, for keyboard use) so 12k rows
 * don't read as a wall of buttons — same treatment as the circle tag.
 */
export function PersonRowActions({
  personId, displayName,
}: {
  personId: string;
  displayName: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<"followup" | "delete" | null>(null);

  async function followUp() {
    setBusy("followup");
    try {
      // Dated tomorrow, matching the "Plan" button on the Follow-ups page, so
      // the same gesture means the same thing in both places. No topic yet —
      // this is the decision to have a conversation, not the agenda.
      await api.createFollowup({ person_id: personId, due_date: defaultDueDate() });
      toast.success(`Follow-up with ${displayName} planned for tomorrow`, {
        action: { label: "Open", onClick: () => router.push("/followups") },
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't plan that follow-up");
    } finally { setBusy(null); }
  }

  async function remove() {
    // Soft delete — recoverable from Cleanup — but it still takes someone out
    // of every list, so it asks first.
    if (!confirm(`Delete ${displayName}?\n\nThey're hidden everywhere, and their history is kept — you can restore them from Cleanup.`)) return;
    setBusy("delete");
    try {
      await api.softDeletePerson(personId);
      toast.success(`${displayName} deleted`);
      router.refresh();   // server-rendered list: re-fetch so the row goes
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Delete failed");
      setBusy(null);
    }
  }

  const btn =
    "grid place-items-center h-6 w-6 rounded shrink-0 text-muted-foreground " +
    "opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity";

  return (
    <>
      <button
        type="button"
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); followUp(); }}
        disabled={busy !== null}
        title={`Plan a follow-up with ${displayName}`}
        className={cn(btn, "hover:bg-accent hover:text-foreground")}
      >
        {busy === "followup"
          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
          : <CalendarPlus className="h-3.5 w-3.5" />}
      </button>
      <button
        type="button"
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); remove(); }}
        disabled={busy !== null}
        title={`Delete ${displayName}`}
        className={cn(btn, "hover:bg-destructive hover:text-destructive-foreground")}
      >
        {busy === "delete"
          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
          : <Trash2 className="h-3.5 w-3.5" />}
      </button>
    </>
  );
}
