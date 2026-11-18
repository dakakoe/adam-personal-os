"use client";

// Members admin (sharing PR1) — owner-only. List household members tied to their
// Authelia login (email) and audit actor; add/edit, link to a CRM person, toggle
// active, set role. Per-account visibility (shared/private) lives on the account
// dialog; this page manages WHO the members are.

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Users, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { api, type FinMember } from "@/lib/api";
import { toast } from "sonner";

export function MembersClient({ initial }: { initial: FinMember[] }) {
  const router = useRouter();
  const [editing, setEditing] = useState<FinMember | null>(null);
  const [adding, setAdding] = useState(false);

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-muted-foreground flex items-center gap-1.5">
          <Users className="h-4 w-4" /> Members
        </h2>
        <Button size="sm" onClick={() => setAdding(true)}>
          <Plus className="h-3.5 w-3.5 mr-1" /> Add member
        </Button>
      </div>

      <p className="text-xs text-muted-foreground mb-3">
        Members map an Authelia login (email) to a person in the budget. Accounts are
        shared with everyone by default; mark an account <em>private</em> on its edit
        dialog to limit it to its owner. Owners see everything.
      </p>

      {initial.length === 0 ? (
        <div className="rounded-md border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
          No members yet.
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-card/40 divide-y divide-border">
          {initial.map((m) => (
            <div key={m.id} className="p-3 flex items-center gap-3 text-sm">
              <span className={cn("inline-block h-1.5 w-1.5 rounded-full shrink-0",
                m.is_active ? "bg-emerald-400" : "bg-muted-foreground/40")} />
              <div className="min-w-0 flex-1">
                <div className="font-medium truncate flex items-center gap-2">
                  {m.display_name}
                  {m.role === "owner" && (
                    <span className="text-[10px] uppercase tracking-wide text-amber-400 border border-amber-500/40 rounded px-1">owner</span>
                  )}
                  {!m.is_active && <span className="text-[10px] text-muted-foreground">(inactive)</span>}
                </div>
                <div className="text-xs text-muted-foreground truncate">
                  {m.email || "no email"} · actor {m.actor}
                  {m.person_name ? ` · ${m.person_name}` : ""}
                </div>
              </div>
              <Button size="sm" variant="ghost" onClick={() => setEditing(m)}>
                <Pencil className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {adding && (
        <MemberDialog onClose={() => setAdding(false)} onDone={() => { setAdding(false); router.refresh(); }} />
      )}
      {editing && (
        <MemberDialog member={editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); router.refresh(); }} />
      )}
    </section>
  );
}

function MemberDialog({ member, onClose, onDone }: { member?: FinMember; onClose: () => void; onDone: () => void }) {
  const [displayName, setDisplayName] = useState(member?.display_name ?? "");
  const [email, setEmail] = useState(member?.email ?? "");
  const [actor, setActor] = useState(member?.actor ?? "");
  const [role, setRole] = useState<"owner" | "member">(member?.role ?? "member");
  const [active, setActive] = useState(member?.is_active ?? true);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!displayName.trim()) { toast.error("Display name required"); return; }
    if (!member && !actor.trim()) { toast.error("Actor required (audit id, e.g. 'member')"); return; }
    setBusy(true);
    try {
      if (member) {
        await api.finance.patchMember(member.id, {
          display_name: displayName.trim(), email: email.trim() || null,
          role, is_active: active,
        });
        toast.success("Member updated");
      } else {
        await api.finance.createMember({
          display_name: displayName.trim(), actor: actor.trim().toLowerCase(),
          email: email.trim() || null, role, is_active: active,
        });
        toast.success("Member added");
      }
      onDone();
    } catch (err) { toast.error(err instanceof Error ? err.message : "Failed"); }
    setBusy(false);
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-background/70 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="w-full max-w-sm rounded-lg border border-border bg-card p-4" onClick={(e) => e.stopPropagation()}>
        <h3 className="font-medium mb-3">{member ? "Edit member" : "Add member"}</h3>
        <form onSubmit={submit} className="space-y-3">
          <Input placeholder="Display name (e.g. Alex)" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          <Input placeholder="Authelia email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          {!member && (
            <Input placeholder="Actor — audit id, lowercase (e.g. member)" value={actor} onChange={(e) => setActor(e.target.value)} />
          )}
          <div className="flex items-center gap-3">
            <select value={role} onChange={(e) => setRole(e.target.value as "owner" | "member")}
              className="flex-1 rounded-md border border-border bg-background p-2 text-sm">
              <option value="member">Member (scoped)</option>
              <option value="owner">Owner (full access)</option>
            </select>
            <label className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Active
            </label>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
            <Button type="submit" size="sm" disabled={busy}>{busy ? "Saving…" : member ? "Save" : "Add"}</Button>
          </div>
        </form>
      </div>
    </div>
  );
}
