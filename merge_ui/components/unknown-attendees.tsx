"use client";

// "You had a call with someone who isn't in your contacts — add them?"
//
// Meetings now resolve their attendees to people by email. The ones that don't
// resolve are the interesting leftovers: you met them, so they matter, but
// nothing else in the system knows who they are. Left alone they'd stay
// invisible forever — this is the one place they surface.
//
// Grouped by address, not by meeting: the same unknown person across six calls
// is one decision. Linking writes the email identity too, so the next meeting
// resolves itself and their mail lands on the same contact.

import { useState } from "react";
import { useRouter } from "next/navigation";
import { CalendarDays, UserPlus, X, Link2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, type UnknownAttendee, type PersonRow } from "@/lib/api";
import { toast } from "sonner";

function ago(iso: string | null): string {
  if (!iso) return "";
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (d <= 0) return "today";
  if (d === 1) return "yesterday";
  if (d < 30) return `${d}d ago`;
  return `${Math.floor(d / 30)}mo ago`;
}

export function UnknownAttendees({ initial }: { initial: UnknownAttendee[] }) {
  const router = useRouter();
  const [items, setItems] = useState(initial);
  const [busy, setBusy] = useState<string | null>(null);
  const [linking, setLinking] = useState<UnknownAttendee | null>(null);

  if (items.length === 0) return null;

  function drop(email: string) {
    setItems((prev) => prev.filter((i) => i.email !== email));
  }

  async function add(a: UnknownAttendee) {
    setBusy(a.email);
    try {
      // No person_id → the API creates the contact from the attendee's name.
      const { linked } = await api.linkUnknownAttendee({ email: a.email, name: a.name });
      drop(a.email);
      toast.success(`Added ${a.name || a.email}${linked > 1 ? ` · ${linked} meetings attached` : ""}`);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't add them");
    } finally { setBusy(null); }
  }

  async function ignore(a: UnknownAttendee) {
    setBusy(a.email);
    try {
      await api.dismissUnknownAttendee(a.email);
      drop(a.email);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't dismiss");
    } finally { setBusy(null); }
  }

  async function linkTo(personId: string) {
    if (!linking) return;
    const a = linking;
    setBusy(a.email);
    try {
      const { linked } = await api.linkUnknownAttendee({ email: a.email, person_id: personId });
      drop(a.email);
      setLinking(null);
      toast.success(`Linked${linked > 1 ? ` · ${linked} meetings attached` : ""}`);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't link");
    } finally { setBusy(null); }
  }

  return (
    <section className="rounded-lg border border-border bg-card/40 p-3">
      <h2 className="flex items-center gap-2 text-sm font-medium mb-1">
        <CalendarDays className="h-4 w-4 text-[var(--color-channel-meeting)]" />
        You met someone we don&apos;t know
      </h2>
      <p className="text-xs text-muted-foreground mb-3">
        These addresses were on your calls but aren&apos;t contacts yet. Adding one
        attaches every meeting you&apos;ve had with them.
      </p>

      <ul className="divide-y divide-border">
        {items.map((a) => (
          <li key={a.email} className="flex items-center gap-3 py-2">
            <div className="min-w-0 flex-1">
              <div className="text-sm truncate">
                {a.name || a.email.split("@")[0]}
                <span className="ml-2 text-xs text-muted-foreground tabular">
                  {a.meetings} {a.meetings === 1 ? "call" : "calls"} · {ago(a.last_seen)}
                </span>
              </div>
              <div className="text-xs text-muted-foreground truncate font-mono">{a.email}</div>
              {a.last_title && (
                <div className="text-xs text-muted-foreground truncate italic">{a.last_title}</div>
              )}
            </div>
            <div className="flex items-center gap-1 shrink-0">
              <Button size="sm" variant="outline" disabled={busy === a.email}
                onClick={() => add(a)}>
                <UserPlus className="h-3.5 w-3.5 mr-1" /> Add
              </Button>
              {/* For someone already in the CRM under another channel — link
                  rather than create, or you get a duplicate person. */}
              <Button size="sm" variant="ghost" disabled={busy === a.email}
                onClick={() => setLinking(a)} title="Link to an existing contact">
                <Link2 className="h-3.5 w-3.5" />
              </Button>
              <Button size="sm" variant="ghost" disabled={busy === a.email}
                onClick={() => ignore(a)} title="Never ask about this address">
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          </li>
        ))}
      </ul>

      {linking && (
        <PersonPicker
          label={`Link ${linking.name || linking.email} to…`}
          busy={busy === linking.email}
          onCancel={() => setLinking(null)}
          onPick={linkTo}
        />
      )}
    </section>
  );
}


/** Type-to-search over existing contacts.
 *
 * Exists because "Add" on someone who is ALREADY a contact under another
 * channel — Telegram, say, with no email on file — would create a second
 * person for the same human. Linking writes the email onto the contact you
 * already have instead. */
function PersonPicker({
  label, busy, onPick, onCancel,
}: {
  label: string;
  busy: boolean;
  onPick: (personId: string) => void;
  onCancel: () => void;
}) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<PersonRow[]>([]);
  const [searching, setSearching] = useState(false);

  async function search(term: string) {
    setQ(term);
    if (term.trim().length < 2) { setRows([]); return; }
    setSearching(true);
    try {
      setRows(await api.listPersons({ q: term.trim(), limit: 8 }));
    } catch {
      setRows([]);
    } finally { setSearching(false); }
  }

  return (
    <div className="mt-3 rounded-md border border-border bg-background p-2">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs text-muted-foreground flex-1 truncate">{label}</span>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      <input
        autoFocus
        value={q}
        onChange={(e) => void search(e.target.value)}
        placeholder="Search contacts…"
        disabled={busy}
        className="w-full h-8 px-2 rounded border border-border bg-background text-sm outline-none"
      />
      <div className="mt-1 max-h-48 overflow-y-auto">
        {rows.map((p) => (
          <button
            key={p.person_id}
            type="button"
            disabled={busy}
            onClick={() => onPick(p.person_id)}
            className="w-full text-left px-2 py-1.5 rounded text-sm hover:bg-accent truncate"
          >
            {p.display_name}
            {p.email && <span className="ml-2 text-xs text-muted-foreground">{p.email}</span>}
          </button>
        ))}
        {q.trim().length >= 2 && !searching && rows.length === 0 && (
          <p className="px-2 py-1.5 text-xs text-muted-foreground">No match.</p>
        )}
      </div>
    </div>
  );
}
