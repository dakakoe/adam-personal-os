"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Users as UsersIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, type WhatsAppGroup } from "@/lib/api";
import { formatRelativeDate, cn } from "@/lib/utils";
import { toast } from "sonner";

/**
 * Per-row on/off for WhatsApp groups. The bridge lists every group it sees
 * and drops its messages until it is switched on. Switching one off is
 * remembered: auto-follow never turns it back on.
 */
export function WhatsAppGroupsList({ rows }: { rows: WhatsAppGroup[] }) {
  const router = useRouter();
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [override, setOverride] = useState<Map<string, boolean>>(new Map());
  const [, startTransition] = useTransition();

  async function toggle(g: WhatsAppGroup) {
    const next = !(override.get(g.chat_jid) ?? g.enabled);
    const name = g.title ?? "this group";
    setPending((p) => new Set(p).add(g.chat_jid));
    setOverride((m) => new Map(m).set(g.chat_jid, next));
    try {
      await api.toggleWhatsAppGroup(g.chat_jid, next);
      toast.success(
        next
          ? `On — new messages from "${name}" will be saved`
          : `Off — "${name}" is no longer saved, and auto-follow will leave it off`,
      );
      startTransition(() => router.refresh());
    } catch (e) {
      setOverride((m) => {
        const copy = new Map(m);
        copy.delete(g.chat_jid);
        return copy;
      });
      toast.error(e instanceof Error ? e.message : "Toggle failed");
    } finally {
      setPending((p) => {
        const copy = new Set(p);
        copy.delete(g.chat_jid);
        return copy;
      });
    }
  }

  return (
    <>
      <div className="mb-3 rounded-md border border-border bg-card/40 px-3 py-2">
        <p className="text-xs text-muted-foreground">
          Switch on the groups you want saved. New messages flow from then on;
          WhatsApp sends a linked device no history, so earlier messages only
          come from the iPhone-backup import.
        </p>
      </div>

      <WhatsAppAutoFollowControl />

      <ul className="divide-y divide-border rounded-md border border-border overflow-hidden bg-card/40">
        {rows.length === 0 ? (
          <li className="p-10 text-center text-sm text-muted-foreground">
            No WhatsApp groups yet. They appear here as soon as the bridge sees them.
          </li>
        ) : (
          rows.map((g) => {
            const enabled = override.get(g.chat_jid) ?? g.enabled;
            const busy = pending.has(g.chat_jid);
            return (
              <li
                key={g.chat_jid}
                className={cn(
                  "flex items-center gap-3 px-3 sm:px-4 py-2.5 transition-colors",
                  enabled ? "bg-emerald-500/5" : "hover:bg-accent/20",
                )}
              >
                <span className="grid place-items-center h-7 w-7 rounded-md bg-secondary/60 shrink-0 text-muted-foreground">
                  <UsersIcon className="h-3.5 w-3.5" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{g.title || "(untitled group)"}</div>
                  <div className="text-xs text-muted-foreground tabular truncate">
                    {g.member_count ? `${g.member_count.toLocaleString()} members · ` : ""}
                    {g.last_message_at
                      ? <>last message {formatRelativeDate(g.last_message_at)}</>
                      : <span className="opacity-60">no messages seen yet</span>}
                    {g.msg_count > 0 && (
                      <>
                        {" · "}
                        <span className="text-emerald-500/90 font-medium">
                          {g.msg_count.toLocaleString()} saved
                        </span>
                      </>
                    )}
                    {!enabled && g.switched_off && <span className="opacity-60"> · switched off by you</span>}
                  </div>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={enabled}
                  aria-label={`Save messages from ${g.title ?? "this group"}`}
                  disabled={busy}
                  onClick={() => toggle(g)}
                  className={cn(
                    "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                    enabled ? "bg-primary" : "bg-secondary",
                    busy && "opacity-50",
                  )}
                >
                  <span
                    className={cn(
                      "inline-block h-5 w-5 transform rounded-full bg-background transition-transform",
                      enabled ? "translate-x-5" : "translate-x-0.5",
                    )}
                  />
                </button>
              </li>
            );
          })
        )}
      </ul>
    </>
  );
}

/** "Auto-follow groups up to N members": applies to the groups known now and
 *  to each new one the bridge sees. Groups you switched off stay off. */
function WhatsAppAutoFollowControl() {
  const router = useRouter();
  const [value, setValue] = useState<string>("");
  const [saved, setSaved] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.getWhatsAppAutoFollow()
      .then((r) => { setSaved(r.max_members); setValue(r.max_members ? String(r.max_members) : ""); })
      .catch(() => setSaved(0));
  }, []);
  async function save() {
    const n = value.trim() ? Number(value) : 0;
    if (!Number.isInteger(n) || n < 0) { toast.error("Enter a whole number of members (0 = off)"); return; }
    setBusy(true);
    try {
      const r = await api.setWhatsAppAutoFollow(n);
      setSaved(r.max_members);
      toast.success(n ? `Auto-follow on for groups up to ${n} members, ${r.enabled_now} switched on now` : "Auto-follow off");
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally { setBusy(false); }
  }
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-border bg-card/40 px-3 py-2 text-xs">
      <span className="text-muted-foreground">Auto-follow groups up to</span>
      <input value={value} onChange={(e) => setValue(e.target.value)} inputMode="numeric" placeholder="off"
        aria-label="Maximum members to auto-follow"
        onKeyDown={(e) => { if (e.key === "Enter") void save(); }}
        className="h-7 w-16 rounded border border-border bg-background px-2 text-xs tabular" />
      <span className="text-muted-foreground">members</span>
      <Button size="sm" variant="outline" onClick={save} disabled={busy || saved === null} className="h-7 text-xs">
        {busy ? "Applying…" : "Apply"}
      </Button>
      <span className="text-[11px] text-muted-foreground/70">
        {saved ? `on (≤ ${saved})` : saved === 0 ? "off" : ""} · new small groups are switched on as they appear; ones you switch off stay off.
      </span>
    </div>
  );
}
