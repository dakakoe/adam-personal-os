"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Pencil } from "lucide-react";
import { api } from "@/lib/api";
import { toast } from "sonner";

/**
 * Fill in role + company for a LinkedIn identity you added by hand.
 *
 * A vanity typed into the app is just a link — position/company only arrive
 * with the connections/contacts CSV import, and LinkedIn can't be fetched. This
 * writes them into the identity's `evidence` in the same shape the importer
 * uses, so the profile builder, this card and the "current title is
 * authoritative" prompt rule all treat it identically — and because evidence
 * feeds the profile input_sig, the summary is queued for a rebuild.
 */
export function LinkedinRoleEditor({
  personId, identityId, position, company,
}: {
  personId: string;
  identityId: number;
  position: string | null;
  company: string | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(position ?? "");
  const [co, setCo] = useState(company ?? "");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await api.patchIdentityRole(personId, identityId, { position: pos, company: co });
      toast.success("Saved — the profile summary will refresh on the next build");
      setOpen(false);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally { setBusy(false); }
  }

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <Pencil className="h-3 w-3" />
        {position || company ? "Edit role" : "Add role / company"}
      </button>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <input value={pos} onChange={(e) => setPos(e.target.value)} placeholder="Role (e.g. Chief Commercial Officer)"
        className="h-7 px-2 text-xs rounded-md border border-border bg-background outline-none w-56" />
      <input value={co} onChange={(e) => setCo(e.target.value)} placeholder="Company"
        className="h-7 px-2 text-xs rounded-md border border-border bg-background outline-none w-40" />
      <button type="button" onClick={save} disabled={busy}
        className="h-7 px-2.5 text-xs rounded-md bg-primary text-primary-foreground disabled:opacity-60">
        {busy ? "…" : "Save"}
      </button>
      <button type="button" onClick={() => setOpen(false)} disabled={busy}
        className="h-7 px-2 text-xs rounded-md border border-border text-muted-foreground hover:text-foreground">
        Cancel
      </button>
    </div>
  );
}
