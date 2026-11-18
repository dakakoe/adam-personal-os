"use client";

// Backlog #3 — suggest following newly-seen Telegram groups. The fetcher
// auto-discovers every group (enabled=false until opted in); this surfaces the
// unfollowed, recently-active ones with one-click Follow (enable) / Ignore
// (dismiss so it stops being suggested).

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Sparkles, Check, X, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, type GroupSuggestion } from "@/lib/api";
import { toast } from "sonner";

function ago(iso: string | null): string {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function GroupSuggestions({ initial }: { initial: GroupSuggestion[] }) {
  const router = useRouter();
  const [items, setItems] = useState(initial);
  const [busy, setBusy] = useState<string | null>(null);

  async function act(g: GroupSuggestion, follow: boolean) {
    setBusy(g.chat_id);
    try {
      if (follow) await api.toggleTelegramGroup(Number(g.chat_id), true);
      else await api.dismissGroupSuggestion(Number(g.chat_id));
      setItems((xs) => xs.filter((x) => x.chat_id !== g.chat_id));
      toast.success(follow ? "Following — messages will sync" : "Dismissed");
      if (follow) router.refresh();   // refresh the list/enabled count below
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed");
    }
    setBusy(null);
  }

  if (items.length === 0) return null;

  return (
    <section className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/[0.04] p-3">
      <h2 className="text-sm font-medium text-amber-400/90 flex items-center gap-1.5 mb-2">
        <Sparkles className="h-4 w-4" /> New groups to follow
        <span className="text-xs text-muted-foreground font-normal">· {items.length}</span>
      </h2>
      <ul className="divide-y divide-border/60">
        {items.map((g) => (
          <li key={g.chat_id} className="py-2 flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium truncate">{g.title || "(untitled group)"}</div>
              <div className="text-xs text-muted-foreground flex items-center gap-2">
                <span>{g.kind}</span>
                {g.member_count != null && <span className="inline-flex items-center gap-1"><Users className="h-3 w-3" />{g.member_count.toLocaleString()}</span>}
                {g.last_message_at && <span>· active {ago(g.last_message_at)}</span>}
              </div>
            </div>
            <Button size="sm" variant="outline" disabled={busy === g.chat_id} onClick={() => act(g, true)}>
              <Check className="h-3.5 w-3.5 mr-1" /> Follow
            </Button>
            <Button size="sm" variant="ghost" className="text-muted-foreground" disabled={busy === g.chat_id} onClick={() => act(g, false)}>
              <X className="h-3.5 w-3.5" />
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
