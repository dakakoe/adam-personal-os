"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, type ProjectRow } from "@/lib/api";
import { toast } from "sonner";

/** Move a meeting recap to another project. The ingest guesses the project
 *  from the transcript, and a meeting that spans two ventures lands under
 *  the wrong one; this is the correction. */
export function RecapProjectPicker({ recapId, currentProjectId, projects }: {
  recapId: string; currentProjectId: string | null; projects: ProjectRow[];
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  async function move(value: string) {
    const target = value === "" ? null : value;
    if (target === currentProjectId) return;
    setBusy(true);
    try {
      await api.moveRecap(recapId, target);
      const name = projects.find((p) => p.id === target)?.name;
      toast.success(name ? `Moved to ${name}` : "Recap unassigned");
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Move failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <select
      value={currentProjectId ?? ""}
      onChange={(e) => void move(e.target.value)}
      disabled={busy}
      title="Move this recap to another project"
      className="h-6 max-w-[11rem] rounded border border-border bg-background px-1.5 text-[11px] text-muted-foreground hover:text-foreground focus:outline-none focus:border-primary/60"
    >
      {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      <option value="">— no project</option>
    </select>
  );
}
