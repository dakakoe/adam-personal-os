"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { formatRelativeDate, cn } from "@/lib/utils";
import { Briefcase, MapPin, Plus, Linkedin, ExternalLink, Trash2, CheckSquare, Target, Building2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { toast } from "sonner";
import { IdentityChip } from "./identity-chip";
import { AddIdentityDialog } from "./add-identity-dialog";
import { LinkedinRoleEditor } from "./linkedin-role-editor";
import { NewOpportunityForPerson } from "./new-opportunity-for-person";
import { PersonAvatar } from "./person-avatar";
import { BiosSection } from "./bios-section";
import { EditableName } from "./editable-name";
import { EditableBirthday } from "./editable-birthday";
import { ProjectBadge } from "./project-badge";
import { StageBadge } from "./stage-badge";
import { api, type PersonDetail } from "@/lib/api";

const SOURCE_ORDER: Record<string, number> = {
  email: 0, telegram: 1, telegram_handle: 1, phone: 2,
  linkedin: 3, x: 4, instagram: 5, github: 6, website: 7,
};

export function PersonCard({ person }: { person: PersonDetail }) {
  const router = useRouter();
  const [addOpen, setAddOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  async function confirmDelete() {
    setDeleting(true);
    try {
      await api.softDeletePerson(person.person_id);
      toast.success(`Deleted ${person.display_name}`);
      // Send the user back to the list; the row will be gone from every
      // query (filters add `deleted_at IS NULL` everywhere).
      router.push("/persons");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeleting(false);
      setDeleteOpen(false);
    }
  }
  const struct = person.structured ?? {};

  const sortedIdentities = [...person.identities].sort((a, b) => {
    const oa = SOURCE_ORDER[a.source] ?? 99;
    const ob = SOURCE_ORDER[b.source] ?? 99;
    return oa === ob ? a.source_id.localeCompare(b.source_id) : oa - ob;
  });

  function refresh() {
    router.refresh();
  }

  return (
    <Card className="border-border bg-card overflow-hidden">
      <CardHeader className="p-4 sm:p-6 pb-4">
        <div className="flex items-start gap-3 sm:gap-4">
          <PersonAvatar
            personId={person.person_id}
            displayName={person.display_name}
            className="h-12 w-12 sm:h-16 sm:w-16"
          />
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <EditableName
                personId={person.person_id}
                displayName={person.display_name}
                className="text-xl sm:text-2xl font-semibold tracking-tight"
              />
              {/* Primary action for a contact — kept in the header so it's
                  reachable without scrolling past bios/tasks. */}
              <span className="shrink-0">
                <NewOpportunityForPerson
                  personId={person.person_id}
                  personName={person.display_name}
                  companies={person.companies}
                />
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-1.5 text-sm text-muted-foreground">
              {struct.current_role && struct.current_company && (
                <span className="inline-flex items-center gap-1.5">
                  <Briefcase className="h-3.5 w-3.5" />
                  <span className="text-foreground">{struct.current_role}</span>
                  <span>at</span>
                  <span className="text-foreground">{struct.current_company}</span>
                </span>
              )}
              {struct.current_role && !struct.current_company && (
                <span className="inline-flex items-center gap-1.5">
                  <Briefcase className="h-3.5 w-3.5" />
                  <span className="text-foreground">{struct.current_role}</span>
                </span>
              )}
              {!struct.current_role && struct.current_company && (
                <span className="inline-flex items-center gap-1.5">
                  <Briefcase className="h-3.5 w-3.5" />
                  <span className="text-foreground">{struct.current_company}</span>
                </span>
              )}
              {struct.languages && struct.languages.length > 0 && (
                <span className="inline-flex items-center gap-1.5">
                  <MapPin className="h-3.5 w-3.5" />
                  <span>{struct.languages.join(", ")}</span>
                </span>
              )}
            </div>
            <div className="flex items-center gap-3 mt-2 text-xs text-muted-foreground tabular flex-wrap">
              <span>
                <span className="text-foreground font-medium">
                  {person.total_interactions.toLocaleString()}
                </span>{" "}
                interactions
              </span>
              <span>·</span>
              <span>{person.inbound_count.toLocaleString()} in</span>
              <span>·</span>
              <span>{person.outbound_count.toLocaleString()} out</span>
              {person.last_interaction_at && (
                <>
                  <span>·</span>
                  <span>last {formatRelativeDate(person.last_interaction_at)}</span>
                </>
              )}
              <span>·</span>
              <EditableBirthday personId={person.person_id} birthday={person.birthday} />
            </div>
          </div>
        </div>
      </CardHeader>

      <Separator />

      <CardContent className="p-4 sm:p-6 space-y-6">
        {/* LinkedIn snapshot — pulled from canonical.identity (source='linkedin')
            evidence + a join to linkedin_imported_contact for location. */}
        {person.linkedin && (
          <section className="rounded-lg border border-[var(--color-channel-linkedin)]/30 bg-[var(--color-channel-linkedin)]/5 p-4">
            <div className="flex items-start gap-3">
              <Linkedin className="h-4 w-4 mt-0.5 shrink-0 text-[var(--color-channel-linkedin)]" />
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex items-baseline justify-between gap-2">
                  <h3 className="text-sm font-medium text-foreground">LinkedIn</h3>
                  <a
                    href={person.linkedin.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary"
                  >
                    {person.linkedin.vanity}
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </div>
                {(person.linkedin.position || person.linkedin.company) && (
                  <p className="text-sm text-foreground/90">
                    {person.linkedin.position}
                    {person.linkedin.position && person.linkedin.company && (
                      <span className="text-muted-foreground"> at </span>
                    )}
                    <span className="font-medium">{person.linkedin.company}</span>
                  </p>
                )}
                {/* A hand-added vanity carries no role/company (only the CSV
                    import does) — let the user supply them; that feeds the
                    profile summary via the identity's evidence. */}
                {(() => {
                  const li = person.identities.find((i) => i.source === "linkedin");
                  return li ? (
                    <LinkedinRoleEditor
                      personId={person.person_id}
                      identityId={li.identity_id}
                      position={person.linkedin!.position ?? null}
                      company={person.linkedin!.company ?? null}
                    />
                  ) : null;
                })()}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  {person.linkedin.location && (
                    <span className="inline-flex items-center gap-1">
                      <MapPin className="h-3 w-3" />
                      {person.linkedin.location}
                    </span>
                  )}
                  {person.linkedin.connected_on && (
                    <span className="tabular">
                      Connected {new Date(person.linkedin.connected_on).toLocaleDateString("en", { year: "numeric", month: "short" })}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </section>
        )}

        {/* Identities */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide">
              Identities
            </h2>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setAddOpen(true)}
              className="h-7 text-xs"
            >
              <Plus className="h-3.5 w-3.5 mr-1" />
              Add
            </Button>
          </div>
          {sortedIdentities.length === 0 && !person.telegram_username && !person.phone ? (
            <p className="text-sm text-muted-foreground italic">No identities yet.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {/* Synthetic chips for the human-friendly @handle + phone that
                  live on raw.telegram_user (not in canonical.identity). */}
              {person.telegram_username && (
                <IdentityChip
                  source="telegram_handle"
                  value={person.telegram_username}
                  readOnly
                />
              )}
              {person.phone && (
                <IdentityChip source="phone" value={person.phone} readOnly />
              )}
              {sortedIdentities
                .filter((i) => !(i.source === "telegram" && person.telegram_username))
                .map((i) => (
                  <IdentityChip
                    key={i.identity_id}
                    personId={person.person_id}
                    identityId={i.identity_id}
                    source={i.source}
                    value={i.source_id}
                    onRemoved={refresh}
                  />
                ))}
            </div>
          )}
        </section>

        {/* Unified summary (LLM-consolidated across all sources). The
            per-source bios that fed this summary are rendered below in
            BiosSection so the user can see provenance. */}
        {person.summary && (
          <>
            <Separator />
            <section>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1.5">
                Summary
              </h3>
              <p className="text-sm leading-relaxed text-foreground/90">
                {person.summary}
              </p>
            </section>
          </>
        )}

        <BiosSection bios={person.bios ?? []} />

        {/* Companies this person is linked to (Phase 6). */}
        {person.companies && person.companies.length > 0 && (
          <>
            <Separator />
            <section>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2 flex items-center gap-1.5">
                <Building2 className="h-3.5 w-3.5" />
                Companies
              </h3>
              <div className="flex flex-wrap gap-1.5">
                {person.companies.map((co) => (
                  <Link key={co.company_id} href={`/companies/${co.company_id}`}
                    className="inline-flex items-center gap-1.5 text-xs rounded-md border border-border bg-secondary/40 px-2 py-1 hover:bg-accent transition-colors">
                    <span className="font-medium">{co.name}</span>
                    {co.role && <span className="text-muted-foreground">· {co.role}</span>}
                    {!co.is_current && <span className="text-muted-foreground/60">· past</span>}
                  </Link>
                ))}
              </div>
            </section>
          </>
        )}

        {/* Phase 1 personal-OS sections: tasks + opportunities involving
            this person. Always rendered so the user can spot the gap
            even when empty (cheap nudge to create one). */}
        <Separator />
        <section>
          <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <CheckSquare className="h-3.5 w-3.5" />
            Tasks with {person.display_name} ({person.tasks?.length ?? 0})
          </h3>
          {!person.tasks || person.tasks.length === 0 ? (
            <p className="text-xs text-muted-foreground italic">No tasks logged with this person.</p>
          ) : (
            <ul className="space-y-1.5">
              {person.tasks.map((t) => (
                <li key={t.id} className="flex items-center gap-2 text-sm px-2 py-1.5 rounded-md hover:bg-accent/30">
                  <span className={cn(
                    "text-[10px] font-mono uppercase px-1 rounded border shrink-0",
                    t.status === "doing" && "text-amber-400 border-amber-500/40",
                    t.status === "open" && "text-foreground border-border",
                    (t.status === "done" || t.status === "cancelled") && "text-muted-foreground border-border line-through",
                  )}>{t.status}</span>
                  <span className={cn(
                    "min-w-0 flex-1 truncate",
                    (t.status === "done" || t.status === "cancelled") && "line-through text-muted-foreground",
                  )}>{t.title}</span>
                  <ProjectBadge slug={t.project_slug} name={t.project_name} color={t.project_color} />
                  {t.due_date && <span className="text-[10px] text-muted-foreground tabular shrink-0">due {t.due_date}</span>}
                </li>
              ))}
            </ul>
          )}
        </section>

        <Separator />
        <section>
          <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <Target className="h-3.5 w-3.5" />
            Opportunities with {person.display_name} ({person.opportunities?.length ?? 0})
          </h3>
          {!person.opportunities || person.opportunities.length === 0 ? (
            <p className="text-xs text-muted-foreground italic">No opportunities tracked with this person.</p>
          ) : (
            <ul className="space-y-1.5">
              {person.opportunities.map((o) => (
                <li key={o.id} className="flex items-center gap-2 text-sm px-2 py-1.5 rounded-md hover:bg-accent/30">
                  <StageBadge stage={o.stage} />
                  <span className="min-w-0 flex-1 truncate">{o.title}</span>
                  <ProjectBadge slug={o.project_slug} name={o.project_name} color={o.project_color} />
                  {o.estimated_value && (
                    <span className="text-[10px] text-muted-foreground shrink-0">{o.estimated_value}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Recent messages */}
        {person.recent_messages.length > 0 && (
          <>
            <Separator />
            <section>
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
                Recent messages
              </h3>
              <ul className="space-y-2.5">
                {person.recent_messages.map((m, idx) => (
                  <li key={idx} className="text-sm flex gap-3">
                    <Badge
                      variant="outline"
                      className={cn(
                        "h-5 px-1.5 text-[10px] font-normal shrink-0 self-start mt-0.5",
                        m.direction === "outbound"
                          ? "text-primary border-primary/40"
                          : "text-muted-foreground",
                      )}
                    >
                      {m.direction === "outbound" ? "you" : "them"}
                    </Badge>
                    <span className="text-xs text-muted-foreground tabular shrink-0 w-16 mt-0.5">
                      {formatRelativeDate(m.occurred_at)}
                    </span>
                    <div className="min-w-0 flex-1">
                      {/* Group context — only present for telegram messages
                          that flowed through an enabled group. Links into
                          the new /groups/{chat_id} stream view. */}
                      {m.group_title && m.group_chat_id && (
                        <a
                          href={`/groups/${m.group_chat_id}`}
                          className="inline-flex items-center gap-1 text-[10px] font-medium text-sky-400 hover:text-sky-300 mb-0.5"
                          title={`Open ${m.group_title} stream`}
                        >
                          via {m.group_title}
                        </a>
                      )}
                      <p className="text-foreground/85 line-clamp-2">
                        {m.body_excerpt}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
        {/* Destructive zone — muted unless hovered. Soft-delete via the
            same /api/persons/{id}/delete endpoint the cleanup queue uses.
            Restoring is one SQL UPDATE if it was a mistake. */}
        <Separator />
        <section className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            Delete this person — hides them from every list and the merge
            queue. The row stays in the DB so you can restore later.
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setDeleteOpen(true)}
            className="h-7 text-xs text-muted-foreground hover:text-destructive-foreground hover:bg-destructive hover:border-destructive shrink-0"
          >
            <Trash2 className="h-3.5 w-3.5 mr-1" />
            Delete person
          </Button>
        </section>
      </CardContent>

      <AddIdentityDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        personId={person.person_id}
        onAdded={refresh}
      />

      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {person.display_name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Soft-delete — the row + identities + summary + photo stay in
              the database but disappear from every list and the merge
              queue. Restore via SQL later if you change your mind
              (<code className="font-mono text-[10px]">UPDATE canonical.person SET deleted_at = NULL WHERE id = '{person.person_id}'</code>).
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDelete}
              disabled={deleting}
              className="bg-destructive hover:bg-destructive/90"
            >
              {deleting ? "Deleting…" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
