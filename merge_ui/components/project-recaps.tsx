"use client";

import { useState } from "react";
import { ChevronRight, Users } from "lucide-react";
import type { ProjectRow } from "@/lib/api";
import { RecapProjectPicker } from "@/components/recap-project-picker";
import { cn } from "@/lib/utils";

export type RecapItem = {
  id: string;
  title: string | null;
  meeting_date: string | null;
  recap: string | null;
  attendees: { name?: string | null; email?: string | null }[] | null;
};

/** Meeting recaps as a scannable list: one line each (title, date, who,
 *  the first sentence), expanding to the full recap on click. */
export function ProjectRecaps({ recaps, projectId, projects, canEdit }: {
  recaps: RecapItem[]; projectId: string; projects: ProjectRow[]; canEdit: boolean;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const allOpen = recaps.length > 0 && open.size === recaps.length;
  const toggle = (id: string) => setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  if (recaps.length === 0) {
    return <p className="text-sm text-muted-foreground py-6 text-center">No meeting recaps attached to this project yet.</p>;
  }
  return (
    <div>
      <div className="flex justify-end mb-2">
        <button type="button" onClick={() => setOpen(allOpen ? new Set() : new Set(recaps.map((r) => r.id)))}
          className="text-[11px] text-muted-foreground hover:text-foreground">
          {allOpen ? "Collapse all" : "Expand all"}
        </button>
      </div>
      <ul className="rounded-md border border-border bg-card/40 divide-y divide-border">
        {recaps.map((r) => {
          const isOpen = open.has(r.id);
          const who = (r.attendees ?? []).map((a) => a.name || a.email).filter(Boolean) as string[];
          const date = r.meeting_date
            ? new Date(r.meeting_date).toLocaleDateString("en", { year: "numeric", month: "short", day: "numeric" })
            : null;
          return (
            <li key={r.id}>
              <div className="flex items-start gap-2 px-3 py-2">
                <button type="button" onClick={() => toggle(r.id)} aria-expanded={isOpen}
                  className="flex-1 min-w-0 text-left flex items-start gap-2 group">
                  <ChevronRight className={cn("h-3.5 w-3.5 mt-1 shrink-0 text-muted-foreground transition-transform", isOpen && "rotate-90")} />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline gap-2">
                      <span className="text-sm font-medium truncate group-hover:text-foreground">{r.title || "(untitled meeting)"}</span>
                      {date && <span className="text-[11px] text-muted-foreground tabular shrink-0">{date}</span>}
                    </span>
                    {!isOpen && r.recap && (
                      <span className="block text-xs text-muted-foreground truncate">{r.recap}</span>
                    )}
                  </span>
                </button>
                {canEdit && (
                  <span className="shrink-0 pt-0.5">
                    <RecapProjectPicker recapId={r.id} currentProjectId={projectId} projects={projects} />
                  </span>
                )}
              </div>
              {isOpen && (
                <div className="px-3 pb-3 pl-9">
                  {r.recap && <p className="text-sm text-foreground/85 leading-relaxed whitespace-pre-line">{r.recap}</p>}
                  {who.length > 0 && (
                    <p className="mt-2 text-[11px] text-muted-foreground flex items-center gap-1">
                      <Users className="h-3 w-3" />{who.join(", ")}
                    </p>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
