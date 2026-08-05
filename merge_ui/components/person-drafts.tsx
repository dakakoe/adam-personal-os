"use client";

import { useEffect, useState } from "react";
import { PenLine, Loader2, Copy, Check, Trash2, Send, MessageSquare, Mail } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, type DraftRow, type DraftChannel } from "@/lib/api";
import { formatRelativeDate, cn } from "@/lib/utils";
import { toast } from "sonner";

/**
 * Draft outreach on the person card. Generate a Haiku follow-up (grounded in
 * the recent thread + an open task/opp), edit it, copy it. DRAFT ONLY — there
 * is intentionally no send button; "Mark sent" just records that you sent it
 * yourself elsewhere.
 */
export function PersonDrafts({ personId }: { personId: string }) {
  const [drafts, setDrafts] = useState<DraftRow[] | null>(null);
  const [channel, setChannel] = useState<DraftChannel>("telegram");
  const [generating, setGenerating] = useState(false);

  async function reload() {
    try { setDrafts(await api.listDrafts(personId)); } catch { setDrafts([]); }
  }
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [personId]);

  async function generate() {
    setGenerating(true);
    try {
      await api.generateDraft(personId, { channel });
      toast.success("Draft ready — review before sending");
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Generate failed");
    } finally {
      setGenerating(false);
    }
  }

  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
        <h2 className="text-sm font-semibold flex items-center gap-2">
          <PenLine className="h-4 w-4 text-muted-foreground" /> Drafts
          <span className="text-[10px] font-normal text-muted-foreground border border-border rounded px-1 py-0.5">review-first · nothing auto-sends</span>
        </h2>
        <div className="flex items-center gap-1.5">
          <div className="flex rounded-md border border-border overflow-hidden text-xs">
            {(["telegram", "email"] as DraftChannel[]).map((c) => (
              <button
                key={c}
                onClick={() => setChannel(c)}
                className={cn("px-2 py-1 flex items-center gap-1", channel === c ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}
              >
                {c === "telegram" ? <MessageSquare className="h-3 w-3" /> : <Mail className="h-3 w-3" />}
                {c}
              </button>
            ))}
          </div>
          <Button size="sm" variant="outline" className="h-7 px-2 text-xs" disabled={generating} onClick={generate}>
            {generating ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <PenLine className="h-3.5 w-3.5 mr-1" />}
            Draft a follow-up
          </Button>
        </div>
      </div>

      {drafts === null ? (
        <div className="text-xs text-muted-foreground">Loading…</div>
      ) : drafts.length === 0 ? (
        <p className="text-xs text-muted-foreground">No drafts yet. Generate one — it uses your recent thread + any open task or deal with this person.</p>
      ) : (
        <ul className="space-y-3">
          {drafts.map((d) => <DraftItem key={d.id} draft={d} onChanged={reload} />)}
        </ul>
      )}
    </section>
  );
}

function DraftItem({ draft, onChanged }: { draft: DraftRow; onChanged: () => void }) {
  const [body, setBody] = useState(draft.body);
  const [subject, setSubject] = useState(draft.subject ?? "");
  const [copied, setCopied] = useState(false);
  const dirty = body !== draft.body || subject !== (draft.subject ?? "");

  async function save() {
    try {
      await api.patchDraft(draft.id, { body, subject: draft.channel === "email" ? subject : undefined });
      toast.success("Saved");
      onChanged();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Save failed"); }
  }
  async function setStatus(status: "sent" | "discarded") {
    try {
      await api.patchDraft(draft.id, { status });
      toast.success(status === "sent" ? "Marked sent" : "Discarded");
      onChanged();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
  }
  async function sendNow() {
    const via = draft.channel === "email" ? "email" : "Telegram";
    if (!confirm(`Send this ${via} message now? It goes from your account.`)) return;
    try {
      if (dirty) await api.patchDraft(draft.id, { body, subject: draft.channel === "email" ? subject : undefined });
      const r = await api.sendDraft(draft.id);
      toast.success(draft.channel === "email" ? `Email sent${r.to ? ` to ${r.to}` : ""}` : "Sending via Telegram…");
      onChanged();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Send failed"); }
  }
  async function copy() {
    const text = draft.channel === "email" && subject ? `Subject: ${subject}\n\n${body}` : body;
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { toast.error("Copy failed"); }
  }

  return (
    <li className="rounded-md border border-border bg-background p-2.5">
      <div className="flex items-center gap-2 mb-1.5 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1 uppercase font-mono">
          {draft.channel === "telegram" ? <MessageSquare className="h-3 w-3" /> : <Mail className="h-3 w-3" />}
          {draft.channel}
        </span>
        {draft.status === "sent" && <span className="text-emerald-400">sent</span>}
        <span className="ml-auto">{formatRelativeDate(draft.created_at)}</span>
      </div>
      {draft.channel === "email" && (
        <input
          value={subject} onChange={(e) => setSubject(e.target.value)}
          placeholder="Subject"
          className="w-full mb-1.5 text-sm font-medium bg-transparent border-b border-border outline-none pb-1"
        />
      )}
      <textarea
        value={body} onChange={(e) => setBody(e.target.value)}
        rows={Math.min(8, Math.max(3, body.split("\n").length + 1))}
        className="w-full text-sm bg-transparent outline-none resize-y"
      />
      <div className="flex items-center gap-1.5 mt-2">
        <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={copy}>
          {copied ? <Check className="h-3.5 w-3.5 mr-1 text-emerald-400" /> : <Copy className="h-3.5 w-3.5 mr-1" />}
          Copy
        </Button>
        {dirty && (
          <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={save}>Save edits</Button>
        )}
        {draft.status === "draft" && (
          <Button size="sm" className="h-7 px-2 text-xs" onClick={sendNow}>
            <Send className="h-3.5 w-3.5 mr-1" /> {draft.channel === "email" ? "Send email" : "Send via Telegram"}
          </Button>
        )}
        <button onClick={() => setStatus("sent")} title="Mark as sent (you sent it yourself / by email)"
          className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-emerald-400">
          <Send className="h-3 w-3" /> mark sent
        </button>
        <button onClick={() => setStatus("discarded")} title="Discard"
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-destructive">
          <Trash2 className="h-3 w-3" /> discard
        </button>
      </div>
    </li>
  );
}
