"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { MessageSquare, Megaphone, Users as UsersIcon, RefreshCw, History } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, type TelegramGroup } from "@/lib/api";
import { formatRelativeDate, cn } from "@/lib/utils";
import { toast } from "sonner";

/**
 * Per-row enable/disable for Telegram groups + channels. Server-rendered
 * page passes the current slice in; this client component owns the
 * optimistic toggle state and the "Discover groups" button that
 * subprocesses out to fetcher discover-groups on the droplet.
 */
export function GroupsList({ rows }: { rows: TelegramGroup[] }) {
  const router = useRouter();
  // Track in-flight toggles so the row UI can show a spinner state
  // without re-fetching the whole page.
  const [pending, setPending] = useState<Set<number>>(new Set());
  // Locally-overridden enabled state per row so the UI flips immediately
  // (router.refresh below pulls authoritative state). Map<chat_id, bool>.
  const [override, setOverride] = useState<Map<number, boolean>>(new Map());
  const [discoverBusy, setDiscoverBusy] = useState(false);
  const [backfillBusy, setBackfillBusy] = useState<Set<number>>(new Set());
  const [, startTransition] = useTransition();

  async function toggle(g: TelegramGroup) {
    const currentlyEnabled = override.get(g.chat_id) ?? g.enabled;
    const next = !currentlyEnabled;
    setPending((p) => new Set(p).add(g.chat_id));
    setOverride((m) => new Map(m).set(g.chat_id, next));
    try {
      await api.toggleTelegramGroup(g.chat_id, next);
      toast.success(
        next
          ? `Enabled — new messages from "${g.title ?? g.chat_id}" will be ingested`
          : `Disabled — "${g.title ?? g.chat_id}" no longer ingested`,
      );
      // Reflect authoritative state across the page (count chip, etc.)
      startTransition(() => router.refresh());
    } catch (e) {
      // Roll back the override on failure
      setOverride((m) => {
        const next = new Map(m);
        next.delete(g.chat_id);
        return next;
      });
      toast.error(e instanceof Error ? e.message : "Toggle failed");
    } finally {
      setPending((p) => {
        const next = new Set(p);
        next.delete(g.chat_id);
        return next;
      });
    }
  }

  async function backfill(g: TelegramGroup) {
    setBackfillBusy((p) => new Set(p).add(g.chat_id));
    try {
      const r = await api.backfillTelegramGroup(g.chat_id);
      toast.success(
        r.seen === 0
          ? `"${g.title ?? g.chat_id}" had no messages to backfill`
          : `Backfilled "${g.title ?? g.chat_id}": ${r.new} new of ${r.seen} seen`,
      );
      startTransition(() => router.refresh());
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Backfill failed");
    } finally {
      setBackfillBusy((p) => {
        const next = new Set(p);
        next.delete(g.chat_id);
        return next;
      });
    }
  }

  async function discover() {
    setDiscoverBusy(true);
    try {
      const r = await api.discoverTelegramGroups();
      toast.success(`Discovered ${r.total} groups/channels total`);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Discover failed");
    } finally {
      setDiscoverBusy(false);
    }
  }

  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-2 rounded-md border border-border bg-card/40 px-3 py-2">
        <p className="text-xs text-muted-foreground">
          Toggle on the groups you actually want in your corpus. New messages
          start flowing immediately; history backfill is a separate action
          (not yet wired — manual SSH for now).
        </p>
        <Button
          size="sm"
          variant="outline"
          onClick={discover}
          disabled={discoverBusy}
          className="h-7 text-xs shrink-0"
        >
          <RefreshCw className={cn("h-3.5 w-3.5 sm:mr-1", discoverBusy && "animate-spin")} />
          <span className="hidden sm:inline">{discoverBusy ? "Discovering…" : "Refresh list"}</span>
        </Button>
      </div>

      <ul className="divide-y divide-border rounded-md border border-border overflow-hidden bg-card/40">
        {rows.length === 0 ? (
          <li className="p-10 text-center text-sm text-muted-foreground">
            No groups match. Tap “Refresh list” if this is your first visit.
          </li>
        ) : (
          rows.map((g) => {
            const enabled = override.get(g.chat_id) ?? g.enabled;
            const busy = pending.has(g.chat_id);
            return (
              <li
                key={g.chat_id}
                className={cn(
                  "flex items-center gap-3 px-3 sm:px-4 py-2.5 transition-colors",
                  enabled ? "bg-emerald-500/5" : "hover:bg-accent/20",
                )}
              >
                <span
                  title={g.kind}
                  className="grid place-items-center h-7 w-7 rounded-md bg-secondary/60 shrink-0 text-muted-foreground"
                >
                  <KindIcon kind={g.kind} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <Link
                      href={`/groups/${g.chat_id}`}
                      className="font-medium truncate hover:text-primary transition-colors"
                      title="Open group stream"
                    >
                      {g.title || `(untitled ${g.kind} ${g.chat_id})`}
                    </Link>
                    <span className="text-[10px] font-mono px-1 rounded border border-border bg-secondary/40 text-muted-foreground">
                      {g.kind}
                    </span>
                  </div>
                  <div className="text-xs text-muted-foreground tabular truncate">
                    {g.member_count !== null ? `${g.member_count.toLocaleString()} members · ` : ""}
                    {g.last_message_at
                      ? <>last message {formatRelativeDate(g.last_message_at)}</>
                      : <span className="opacity-60">no messages observed yet</span>}
                    {g.msg_count > 0 && (
                      <>
                        {" · "}
                        <span className="text-emerald-500/90 font-medium">
                          {g.msg_count.toLocaleString()} ingested
                        </span>
                      </>
                    )}
                  </div>
                </div>
                {/* Backfill action — only visible on enabled rows. Long-
                    running (the subprocess can take minutes for big chats)
                    so we lean on the per-row busy state instead of any
                    confirmation dialog; toast carries the {seen, new}
                    summary when it lands. */}
                {enabled && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => backfill(g)}
                    disabled={backfillBusy.has(g.chat_id)}
                    title="Pull message history into raw.telegram_message"
                    className="h-7 px-2 sm:px-3 text-xs shrink-0"
                  >
                    <History className={cn("h-3.5 w-3.5 sm:mr-1", backfillBusy.has(g.chat_id) && "animate-pulse")} />
                    <span className="hidden sm:inline">
                      {backfillBusy.has(g.chat_id) ? "Pulling…" : "Backfill"}
                    </span>
                  </Button>
                )}
                {/* Toggle switch — bare button styled as iOS-y pill. */}
                <button
                  type="button"
                  role="switch"
                  aria-checked={enabled}
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

function KindIcon({ kind }: { kind: string }) {
  if (kind === "channel") return <Megaphone className="h-3.5 w-3.5" />;
  if (kind === "supergroup" || kind === "group") return <UsersIcon className="h-3.5 w-3.5" />;
  return <MessageSquare className="h-3.5 w-3.5" />;
}
