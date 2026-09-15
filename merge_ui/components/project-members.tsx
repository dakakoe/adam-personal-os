"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { X, UserPlus } from "lucide-react";
import { PersonAvatar } from "@/components/person-avatar";
import { PersonPicker } from "@/components/person-picker";
import { api, type PersonRow, type ProjectMember } from "@/lib/api";
import { toast } from "sonner";

/**
 * Manage a project's members: shows each member (click → their profile, × to
 * remove) and an "Add member" people-picker. Reuses PersonPicker + the existing
 * /api/projects/{id}/members endpoints.
 */
export function ProjectMembers({ projectId, initial, canEdit = true }: { projectId: string; initial: ProjectMember[]; canEdit?: boolean }) {
  const router = useRouter();
  const [members, setMembers] = useState<ProjectMember[]>(initial);

  async function add(p: PersonRow) {
    if (members.some((m) => m.person_id === p.person_id)) {
      toast.info(`${p.display_name} is already a member`);
      return;
    }
    try {
      await api.addProjectMember(projectId, p.person_id);
      setMembers((prev) => [...prev, {
        person_id: p.person_id, display_name: p.display_name, role: null,
        added_at: new Date().toISOString(),
      }]);
      toast.success(`Added ${p.display_name}`);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to add member");
    }
  }

  async function remove(m: ProjectMember) {
    try {
      await api.removeProjectMember(projectId, m.person_id);
      setMembers((prev) => prev.filter((x) => x.person_id !== m.person_id));
      toast.success(`Removed ${m.display_name}`);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to remove member");
    }
  }

  return (
    <section className="mb-6">
      <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
        Members ({members.length})
      </h2>
      <ul className="flex flex-wrap gap-2 items-center">
        {members.map((m) => (
          <li
            key={m.person_id}
            className="group inline-flex items-center gap-2 px-2 py-1 rounded-md border border-border bg-card/40 hover:bg-accent/40 transition-colors"
          >
            <Link href={`/persons/${m.person_id}`} className="inline-flex items-center gap-2">
              <PersonAvatar personId={m.person_id} displayName={m.display_name} className="h-6 w-6" />
              <span className="text-sm">{m.display_name}</span>
              {m.role && <span className="text-[10px] text-muted-foreground">· {m.role}</span>}
            </Link>
            {canEdit && (
              <button
                onClick={() => remove(m)}
                title={`Remove ${m.display_name}`}
                className="opacity-0 group-hover:opacity-60 hover:!opacity-100 grid place-items-center h-5 w-5 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </li>
        ))}
        {canEdit && (
          <li>
            <PersonPicker
              onPick={add}
              trigger={
                <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-dashed border-border text-xs text-muted-foreground hover:text-foreground hover:border-foreground/40 transition">
                  <UserPlus className="h-3.5 w-3.5" /> Add member
                </span>
              }
            />
          </li>
        )}
      </ul>
    </section>
  );
}
