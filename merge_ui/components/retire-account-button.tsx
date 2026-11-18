"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Archive, RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { toast } from "sonner";

/**
 * Retire a sunsetted mailbox (kept as a read-only archive) or bring one back.
 *
 * Retiring never deletes anything: every message/event already synced stays in
 * Postgres and remains searchable in the app. It only stops the syncs (which
 * fail forever once the mailbox is gone) and drops the dead refresh token.
 */
export function RetireAccountButton({ email, retired = false }: { email: string; retired?: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function run() {
    if (!retired && !confirm(
      `Retire ${email}?\n\nIts synced mail and events STAY in your archive and remain searchable — ` +
      `this only stops syncing it and clears the failing health checks. You can undo this later ` +
      `(it would need a fresh Google consent).`
    )) return;
    setBusy(true);
    try {
      if (retired) {
        await api.unretireAccount(email);
        toast.success(`${email} restored — reconnect it to resume syncing`);
      } else {
        await api.retireAccount(email);
        toast.success(`${email} retired — archive kept, syncing stopped`);
      }
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button" onClick={run} disabled={busy}
      title={retired
        ? "Bring this account back (needs a fresh Google consent)"
        : "Mailbox gone for good? Keep its archive, stop syncing it."}
      className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 hover:bg-muted transition-colors"
    >
      {retired ? <RotateCcw className="h-3 w-3 text-muted-foreground" /> : <Archive className="h-3 w-3 text-muted-foreground" />}
      {busy ? "…" : retired ? "Restore" : "Retire"}
    </button>
  );
}
