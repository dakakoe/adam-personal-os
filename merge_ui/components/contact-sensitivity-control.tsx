"use client";

// Sensitivity-routing opt-in (Ollama expansion): a sensitive contact's
// messages are only ever processed by the on-box LLM — profile builder,
// interaction scanner, and draft outreach route to local Ollama for them
// (fail-closed: local failure skips, never falls back to cloud).

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck, Cloud } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { toast } from "sonner";

export function ContactSensitivityControl(
  { id, sensitive: initial }: { id: string; sensitive: boolean },
) {
  const router = useRouter();
  const [sensitive, setSensitive] = useState(initial);
  const [busy, setBusy] = useState(false);

  async function set(v: boolean) {
    if (v === sensitive || busy) return;
    setBusy(true);
    const prev = sensitive;
    setSensitive(v);
    try {
      await api.setPersonSensitivity(id, v);
      toast.success(v
        ? "Sensitive — this contact's messages stay on-box (local model only)"
        : "Standard processing (cloud model)");
      router.refresh();
    } catch (e) {
      setSensitive(prev);
      toast.error(e instanceof Error ? e.message : "Failed");
    }
    setBusy(false);
  }

  return (
    <div className="rounded-lg border border-border bg-card/40 px-3 py-2 flex items-center justify-between gap-3">
      <span className="text-xs text-muted-foreground">
        {sensitive
          ? "Sensitive — processed by the local model only; messages never leave the server"
          : "Standard — AI features may use the cloud model"}
      </span>
      <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
        <button type="button" disabled={busy} onClick={() => set(false)}
          className={cn("px-2.5 py-1 inline-flex items-center gap-1",
            !sensitive ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
          <Cloud className="h-3 w-3" /> Standard
        </button>
        <button type="button" disabled={busy} onClick={() => set(true)}
          className={cn("px-2.5 py-1 inline-flex items-center gap-1",
            sensitive ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
          <ShieldCheck className="h-3 w-3" /> Sensitive
        </button>
      </div>
    </div>
  );
}
