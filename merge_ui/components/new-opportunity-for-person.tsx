"use client";

import { useEffect, useState } from "react";
import { Target } from "lucide-react";
import { CreateOpportunityDialog } from "@/components/opportunities-client";
import { api, type ProjectRow, type PersonCompany } from "@/lib/api";

/**
 * "New opportunity" straight from a contact page — the deal opens with this
 * person as counterparty already set, and their company pre-filled when we know
 * one, so the common case is just a title.
 *
 * Company preference: a CURRENT company link beats a past one; ties fall back to
 * the first link. We pass the company_id too, so the deal keeps the real company
 * association rather than a detached free-text name.
 */
export function NewOpportunityForPerson({
  personId, personName, companies,
}: {
  personId: string;
  personName: string;
  companies?: PersonCompany[];
}) {
  const [open, setOpen] = useState(false);
  // Projects are only needed once the dialog is opened — fetch lazily so the
  // contact page doesn't pay for a list most visits never use.
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  useEffect(() => {
    if (!open || projects.length) return;
    api.listProjects({}).then(setProjects).catch(() => { /* dialog still works */ });
  }, [open, projects.length]);

  const co = (companies ?? []).find((c) => c.is_current) ?? (companies ?? [])[0];

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-md border border-primary/40 bg-primary/10 text-xs font-medium text-primary hover:bg-primary/20 transition-colors"
        title={co ? `New opportunity with ${personName} (${co.name})` : `New opportunity with ${personName}`}
      >
        <Target className="h-3.5 w-3.5" /> New opportunity
      </button>
      <CreateOpportunityDialog
        open={open}
        onOpenChange={setOpen}
        projects={projects}
        defaults={{
          counterparty_id: personId,
          counterparty_name: personName,
          ...(co ? { company_id: co.company_id, company: co.name } : {}),
        }}
      />
    </>
  );
}
