"use client";

import { useRouter } from "next/navigation";
import { Building2, X } from "lucide-react";
import { CompanyPicker } from "@/components/company-picker";

/** Company filter chip for the People list. Picking a company navigates to
 *  ?company=<id> (server re-fetches the filtered slice); the × clears it.
 *  `currentName` is resolved server-side so the chip reads as a name, not a UUID. */
export function PersonsCompanyFilter({
  currentId, currentName,
}: {
  currentId: string | null;
  currentName: string | null;
}) {
  const router = useRouter();

  if (currentId) {
    return (
      <span className="inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border bg-accent/40 text-xs">
        <Building2 className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="font-medium max-w-[12rem] truncate">{currentName ?? "Company"}</span>
        <button
          type="button"
          onClick={() => router.push("/persons")}
          className="ml-0.5 text-muted-foreground hover:text-foreground"
          title="Clear company filter"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </span>
    );
  }

  return (
    <CompanyPicker
      align="right"
      onPick={(c) => router.push(`/persons?company=${c.id}`)}
      trigger={
        <span className="inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-xs text-muted-foreground hover:bg-accent">
          <Building2 className="h-3.5 w-3.5" />
          Filter by company
        </span>
      }
    />
  );
}
