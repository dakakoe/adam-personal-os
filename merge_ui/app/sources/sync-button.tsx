"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw } from "lucide-react";
import { api } from "@/lib/api";

/** Triggers a one-shot source worker, then refreshes the page so the new
 *  count + last-sync show. Oneshot sources only. */
export function SyncButton({ sourceKey }: { sourceKey: string }) {
  const router = useRouter();
  const [state, setState] = useState<"idle" | "busy" | "error">("idle");

  async function run() {
    setState("busy");
    try {
      await api.syncSource(sourceKey);
      // The worker runs async; give it a few seconds, then re-fetch.
      setTimeout(() => {
        router.refresh();
        setState("idle");
      }, 3500);
    } catch {
      setState("error");
      setTimeout(() => setState("idle"), 2500);
    }
  }

  return (
    <button
      type="button"
      onClick={run}
      disabled={state === "busy"}
      className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground transition-colors disabled:opacity-60"
    >
      <RefreshCw className={`h-3.5 w-3.5 ${state === "busy" ? "animate-spin" : ""}`} />
      {state === "busy" ? "Syncing…" : state === "error" ? "Failed" : "Sync now"}
    </button>
  );
}
