"use client";

// Owner control (unified-sharing — contacts): mark a contact Shared (visible to
// members) or Private (owner only). Contacts default to private, so this is how
// a contact gets surfaced to the budget member(s). Owner-only surface.

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Users, Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { toast } from "sonner";

// Owner Shared/Private control, reused for contacts and companies (unified
// sharing). Both default to private; this is how an entity is surfaced to members.
export function ContactSharingControl(
  { id, visibility: initial, kind }:
  { id: string; visibility: "shared" | "private"; kind: "person" | "company" },
) {
  const router = useRouter();
  const [visibility, setVisibility] = useState<"shared" | "private">(initial);
  const [busy, setBusy] = useState(false);
  const noun = kind === "company" ? "company" : "contact";

  async function set(v: "shared" | "private") {
    if (v === visibility || busy) return;
    setBusy(true);
    const prev = visibility;
    setVisibility(v);
    try {
      if (kind === "company") await api.setCompanySharing(id, { visibility: v });
      else await api.setPersonSharing(id, { visibility: v });
      toast.success(v === "shared" ? "Shared with members" : "Set private");
      router.refresh();
    } catch (e) {
      setVisibility(prev);
      toast.error(e instanceof Error ? e.message : "Failed");
    }
    setBusy(false);
  }

  return (
    <div className="rounded-lg border border-border bg-card/40 px-3 py-2 flex items-center justify-between gap-3">
      <span className="text-xs text-muted-foreground">
        {visibility === "shared"
          ? "Shared — visible to members"
          : `Private — only you can see this ${noun}`}
      </span>
      <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
        <button type="button" disabled={busy} onClick={() => set("private")}
          className={cn("px-2.5 py-1 inline-flex items-center gap-1",
            visibility === "private" ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
          <Lock className="h-3 w-3" /> Private
        </button>
        <button type="button" disabled={busy} onClick={() => set("shared")}
          className={cn("px-2.5 py-1 inline-flex items-center gap-1",
            visibility === "shared" ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
          <Users className="h-3 w-3" /> Shared
        </button>
      </div>
    </div>
  );
}
