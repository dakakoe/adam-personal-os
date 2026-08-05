"use client";

// Read-only mail reader (backlog #2 Phase 1). Thread list + reader over the
// already-ingested raw.gmail_message. HTML bodies render in a SANDBOXED iframe
// (no scripts, no same-origin) — untrusted email is never executed. Plain text is
// preferred; "Show original" reveals the HTML.

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Search, Mail, ChevronLeft, Paperclip, Reply, PenSquare, Send, Star, Archive, ArchiveRestore, Download, RefreshCw, ShieldAlert, CheckCheck, Inbox, Forward, MailOpen, Users, Trash2, Clock, SlidersHorizontal, ListX, type LucideIcon } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { api, type MailThreadRow, type MailMessage, type MailAccount, type MailTriageSignals, type MailSenderRow } from "@/lib/api";
import { toast } from "sonner";

type Compose = {
  account_email: string; to: string; subject: string; body: string;
  in_reply_to?: string | null; references?: string | null; thread_id?: string | null;
};

// Phase C content class (SetFit) — the single triage taxonomy the UI filters and
// badges by (the sidebar Categories select these; rows badge them).
const CONTENT_META: Record<string, { label: string; cls: string }> = {
  newsletter: { label: "Newsletter", cls: "text-amber-400 border-amber-500/40" },
  transactional: { label: "Transactional", cls: "text-emerald-400 border-emerald-500/40" },
  personal: { label: "Personal", cls: "text-blue-400 border-blue-500/40" },
};

type MailView = "inbox" | "starred" | "archived" | "trash" | "snoozed";

// One thread-state change (the POST /api/mail/thread/state body sans keys).
// snoozed_until is tri-state: absent = untouched, null = unsnooze, ISO = snooze.
type StateChange = { archived?: boolean; starred?: boolean; read?: boolean; trashed?: boolean; snoozed_until?: string | null };

// The list-endpoint filter set each view maps to (single source for both the
// initial fetch and infinite-scroll pages).
function viewQuery(v: MailView): { archived: "hide" | "only" | "all"; trashed: "hide" | "only" | "all"; snoozed: "hide" | "only" | "all"; starred?: boolean } {
  switch (v) {
    case "starred": return { archived: "all", trashed: "hide", snoozed: "all", starred: true };
    case "archived": return { archived: "only", trashed: "hide", snoozed: "all" };
    case "trash": return { archived: "all", trashed: "only", snoozed: "all" };
    case "snoozed": return { archived: "all", trashed: "hide", snoozed: "only" };
    default: return { archived: "hide", trashed: "hide", snoozed: "hide" };
  }
}

function snoozeActive(t: { snoozed_until: string | null }): boolean {
  return !!t.snoozed_until && new Date(t.snoozed_until) > new Date();
}

// How long an archive/trash sits pending (undoable) before the POST fires.
const UNDO_MS = 5000;

function fmtDate(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString(undefined, sameYear
    ? { month: "short", day: "numeric" }
    : { year: "numeric", month: "short", day: "numeric" });
}

export function MailClient({ initialThreads, accounts }: { initialThreads: MailThreadRow[]; accounts: MailAccount[] }) {
  const [threads, setThreads] = useState(initialThreads);
  const [q, setQ] = useState("");
  const [account, setAccount] = useState("");
  const [category, setCategory] = useState("");
  const [content, setContent] = useState("");
  const [view, setView] = useState<MailView>("inbox");
  const [pane, setPane] = useState<"threads" | "senders">("threads");
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [selected, setSelected] = useState<MailThreadRow | null>(null);
  const [messages, setMessages] = useState<MailMessage[] | null>(null);
  const [loadingThread, setLoadingThread] = useState(false);
  const [compose, setCompose] = useState<Compose | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [navOpen, setNavOpen] = useState(false);   // mobile filter drawer
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  // The thread-list scroll container. On desktop it's `md:overflow-y-auto`, so
  // the infinite-scroll observer must watch IT, not the viewport — otherwise
  // the sentinel is clipped inside it and never intersects (pagination dies
  // past page 1). On mobile the page scrolls naturally → viewport root.
  const listScrollRef = useRef<HTMLDivElement | null>(null);
  // Deferred archive/trash actions inside their undo window, keyed by
  // account:thread_key. The commit is driven by OUR setTimeout — never the
  // toast's lifecycle (a dismissed toast must still commit).
  const pending = useRef(new Map<string, {
    timer: number; row: MailThreadRow; index: number; change: StateChange; toastId: string | number;
  }>());

  const PAGE_SIZE = 50;
  // Compose can only go out from accounts whose granted scopes include gmail.send;
  // prefer the currently-selected account when it qualifies.
  const sendable = accounts.filter((a) => a.can_send);
  const defaultAccount =
    (account && sendable.find((a) => a.account_email === account)?.account_email)
    || sendable[0]?.account_email || "";

  function startCompose() {
    if (!defaultAccount) {
      toast.error("No send-capable account", { description: "Re-consent an account on Sources to enable sending." });
      return;
    }
    setCompose({ account_email: defaultAccount, to: "", subject: "", body: "" });
  }

  function startReply() {
    const msgs = messages || [];
    const last = msgs[msgs.length - 1];
    if (!last) return;
    const subj = selected?.subject || last.subject || "";
    setCompose({
      account_email: last.account_email,
      to: last.from_address || "",
      subject: /^re:/i.test(subj) ? subj : `Re: ${subj}`,
      body: "",
      in_reply_to: last.rfc822_message_id,
      references: last.rfc822_message_id,
      thread_id: last.thread_id,
    });
  }

  const fetchThreads = useCallback(async (over?: { category?: string; content?: string; account?: string; q?: string; view?: MailView }) => {
    const cat = over?.category ?? category;
    const cnt = over?.content ?? content;
    const acc = over?.account ?? account;
    const qq = over?.q ?? q;
    const v = over?.view ?? view;
    setLoading(true);
    try {
      const rows = await api.listMailThreads({
        q: qq.trim() || undefined, account: acc || undefined, category: cat || undefined,
        content: cnt || undefined,
        ...viewQuery(v), limit: PAGE_SIZE, offset: 0,
      });
      setThreads(rows);
      setHasMore(rows.length === PAGE_SIZE);
    } catch { toast.error("Couldn't load mail"); }
    setLoading(false);
  }, [q, category, content, account, view]);

  // Infinite scroll: append the next page from the current offset.
  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const rows = await api.listMailThreads({
        q: q.trim() || undefined, account: account || undefined, category: category || undefined,
        content: content || undefined,
        ...viewQuery(view), limit: PAGE_SIZE, offset: threads.length,
      });
      setThreads((xs) => [...xs, ...rows]);
      setHasMore(rows.length === PAGE_SIZE);
    } catch { /* silent — a failed page just won't append */ }
    setLoadingMore(false);
  }, [loadingMore, hasMore, q, account, category, content, view, threads.length]);

  // Trigger loadMore when the bottom sentinel scrolls into view. Root is the
  // list's own scroll container on desktop (where it's overflow-y-auto), the
  // viewport on mobile (natural page scroll).
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const desktop = typeof window !== "undefined"
      && window.matchMedia("(min-width: 768px)").matches;
    const root = desktop ? listScrollRef.current : null;
    const obs = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting) loadMore();
    }, { root, rootMargin: "300px" });
    obs.observe(el);
    return () => obs.disconnect();
  }, [loadMore]);

  // Sidebar navigation: set a full filter set (so a category click also clears
  // any stale content filter) and reload. `??` keeps unspecified fields; pass ""
  // explicitly to clear one.
  function navTo(over: { view?: MailView; category?: string; content?: string; account?: string; q?: string }) {
    const merged = {
      view: over.view ?? view, category: over.category ?? category,
      content: over.content ?? content, account: over.account ?? account,
      q: over.q ?? q,
    };
    setPane("threads");
    setView(merged.view); setCategory(merged.category);
    setContent(merged.content); setAccount(merged.account);
    setQ(merged.q);
    setSelected(null); setMessages(null);
    fetchThreads(merged);
  }

  // Mark every currently-unread thread read (app-local), optimistically.
  async function markAllRead() {
    try {
      const r = await api.markAllMailRead(account || undefined);
      setThreads((xs) => xs.map((x) => ({ ...x, unread: false })));
      toast.success(`Marked ${r.marked} read`);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
  }

  function sameThread(a: { thread_key: string; account_email: string }, b: { thread_key: string; account_email: string }) {
    return a.thread_key === b.thread_key && a.account_email === b.account_email;
  }

  // Does a thread (post-change) still belong in the current view?
  function belongsInView(next: MailThreadRow): boolean {
    if (view === "inbox") return !next.archived && !next.trashed && !snoozeActive(next);
    if (view === "starred") return next.starred && !next.trashed;
    if (view === "archived") return next.archived && !next.trashed;
    if (view === "trash") return next.trashed;
    return snoozeActive(next) && !next.trashed;   // snoozed
  }

  // Apply a state change to the local list/reader only (no network).
  function applyLocal(t: MailThreadRow, change: StateChange) {
    const unreadChange = change.read !== undefined ? { unread: !change.read } : {};
    setThreads((xs) => xs.flatMap((x) => {
      if (!sameThread(x, t)) return [x];
      const next = { ...x, ...change, ...unreadChange };
      return belongsInView(next) ? [next] : [];
    }));
    if (selected && sameThread(selected, t)) setSelected({ ...selected, ...change, ...unreadChange });
  }

  // POST the change (local overlay + best-effort Gmail push) + result toast.
  // `silent` skips the plain success toast (the undo toast already told the
  // user) but never the needs-re-consent warning. Returns false on failure.
  async function commitAct(t: MailThreadRow, change: StateChange, opts?: { silent?: boolean }): Promise<boolean> {
    try {
      const res = await api.setMailState({ account_email: t.account_email, thread_key: t.thread_key, ...change });
      const label = change.trashed !== undefined ? (change.trashed ? "Trashed" : "Restored")
        : "snoozed_until" in change ? (change.snoozed_until ? "Snoozed" : "Unsnoozed")
        : change.archived !== undefined ? (change.archived ? "Archived" : "Unarchived")
        : change.starred !== undefined ? (change.starred ? "Starred" : "Unstarred")
        : change.read === false ? "Marked unread" : "Marked read";
      // archive/star/trash push to Gmail; read + snooze are app-local (never a sync warning).
      const pushed = change.archived !== undefined || change.starred !== undefined || change.trashed !== undefined;
      if (!pushed || res.gmail_synced) {
        if (!opts?.silent) {
          toast.success(label, change.snoozed_until
            ? { description: `Until ${new Date(change.snoozed_until).toLocaleString()} — app-local, Gmail unaffected.` }
            : undefined);
        }
      } else toast.success(`${label} locally`, { description: "Re-consent gmail.modify to sync to Gmail." });
      return true;
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); return false; }
  }

  // Instant actions (star/read/snooze/restore/…): optimistic local + immediate POST.
  function act(t: MailThreadRow, change: StateChange) {
    applyLocal(t, change);
    void commitAct(t, change);
  }

  // Deferred actions (archive/trash): remove the row now, show an undo toast
  // with a visible countdown, and only POST after UNDO_MS elapses untouched.
  function deferAct(t: MailThreadRow, change: StateChange, label: string) {
    const key = `${t.account_email}:${t.thread_key}`;
    const prev = pending.current.get(key);
    if (prev) {   // repeat action on a pending thread: commit the old one first
      window.clearTimeout(prev.timer);
      toast.dismiss(prev.toastId);
      pending.current.delete(key);
      void commitAct(prev.row, prev.change, { silent: true });
    }
    const index = threads.findIndex((x) => sameThread(x, t));
    setThreads((xs) => xs.filter((x) => !sameThread(x, t)));
    if (selected && sameThread(selected, t)) { setSelected(null); setMessages(null); }
    const timer = window.setTimeout(() => commitPending(key), UNDO_MS);
    const toastId = toast(<UndoToast label={label} onUndo={() => undoPending(key)} />, { duration: UNDO_MS });
    pending.current.set(key, { timer, row: t, index: index < 0 ? 0 : index, change, toastId });
  }

  // Undo within the window: cancel the timer, restore the row — zero network.
  function undoPending(key: string) {
    const p = pending.current.get(key);
    if (!p) return;
    window.clearTimeout(p.timer);
    toast.dismiss(p.toastId);
    pending.current.delete(key);
    setThreads((xs) => {
      if (xs.some((x) => sameThread(x, p.row))) return xs;
      const i = Math.min(p.index, xs.length);
      return [...xs.slice(0, i), p.row, ...xs.slice(i)];
    });
  }

  function commitPending(key: string) {
    const p = pending.current.get(key);
    if (!p) return;
    pending.current.delete(key);
    void commitAct(p.row, p.change, { silent: true }).then((ok) => {
      if (ok) return;
      // failed POST: put the row back so the UI doesn't lie
      setThreads((xs) => {
        if (xs.some((x) => sameThread(x, p.row))) return xs;
        const i = Math.min(p.index, xs.length);
        return [...xs.slice(0, i), p.row, ...xs.slice(i)];
      });
    });
  }

  // Tab close / nav away with actions still pending: fire them immediately
  // (keepalive survives pagehide). Losing the toast is fine — the action the
  // user chose still happens.
  useEffect(() => {
    const drain = () => {
      for (const p of pending.current.values()) {
        window.clearTimeout(p.timer);
        fetch("/api/mail/thread/state", {
          method: "POST", keepalive: true,
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ account_email: p.row.account_email, thread_key: p.row.thread_key, ...p.change }),
        }).catch(() => {});
      }
      pending.current.clear();
    };
    window.addEventListener("pagehide", drain);
    return () => { window.removeEventListener("pagehide", drain); drain(); };
  }, []);

  // Mobile filter drawer: Escape closes, body scroll locks while open.
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setNavOpen(false); };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = ""; };
  }, [navOpen]);

  function openSenders() { setPane("senders"); setSelected(null); setMessages(null); }

  // User correction of the content class → ground truth the classifier never
  // overwrites. Optimistic; reverts on a failed POST.
  async function correctClass(t: MailThreadRow, cls: "newsletter" | "transactional" | "personal") {
    const prev = { content_class: t.content_class, content_confidence: t.content_confidence };
    const next = { content_class: cls, content_confidence: 1 };
    setThreads((xs) => xs.map((x) => (sameThread(x, t) ? { ...x, ...next } : x)));
    if (selected && sameThread(selected, t)) setSelected({ ...selected, ...next });
    try {
      const r = await api.setMailClass({
        account_email: t.account_email, thread_key: t.thread_key, content_class: cls });
      toast.success(`Corrected to ${CONTENT_META[cls].label}`,
        { description: `${r.messages} message${r.messages === 1 ? "" : "s"} saved as ground truth.` });
    } catch (e) {
      setThreads((xs) => xs.map((x) => (sameThread(x, t) ? { ...x, ...prev } : x)));
      if (selected && sameThread(selected, t)) setSelected({ ...selected, ...prev });
      toast.error(e instanceof Error ? e.message : "Correction failed");
    }
  }

  // Forward the open thread's latest message (quoted) via the compose dialog.
  function startForward() {
    const msgs = messages || [];
    const last = msgs[msgs.length - 1];
    if (!last) return;
    const subj = selected?.subject || last.subject || "";
    const when = new Date(last.internal_date).toLocaleString();
    const quoted = `\n\n---------- Forwarded message ----------\nFrom: ${last.from_name || last.from_address || ""}\nDate: ${when}\nSubject: ${last.subject || ""}\n\n${last.body_text || ""}`;
    setCompose({
      account_email: last.account_email,
      to: "",
      subject: /^fwd:/i.test(subj) ? subj : `Fwd: ${subj}`,
      body: quoted,
    });
  }

  // Add the open message's sender to the clear list — a batch you bulk-trash
  // later from Mail Cleanup → Clear list (stops the read-a-junk-email → go-hunt
  // the sender dance). Undoable from the toast.
  async function addToClearList() {
    const addr = selected?.from_address;
    if (!addr) return;
    try {
      await api.clearListMailSender({ from_address: addr, clear: true });
      toast.success(`Added ${selected?.from_name || addr} to the clear list`, {
        description: "Trash the whole list from Mail → Cleanup → Clear list.",
        action: { label: "Undo",
          onClick: () => { void api.clearListMailSender({ from_address: addr, clear: false }).catch(() => {}); } },
      });
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
  }

  // Trigger a real Gmail pull (Phase 3c refresh affordance), then re-query.
  // The button spins for the whole pull — not just the trigger — so it never
  // looks like nothing happened: the fetcher runs server-side after the POST
  // returns, so we hold the spinner across the re-query that surfaces new mail.
  async function syncNow() {
    if (syncing) return;
    setSyncing(true);
    try {
      await api.syncSource("gmail");
      toast.success("Syncing Gmail…", { description: "New mail appears shortly." });
      await new Promise((r) => setTimeout(r, 4000));
      await fetchThreads();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  // Batch Rspamd spam scan of recent unscored mail (Phase B2), then re-query.
  const [scanning, setScanning] = useState(false);
  async function scanSpam() {
    setScanning(true);
    try {
      const r = await api.scanMailSpam(account || undefined);
      toast.success(`Scanned ${r.scanned} · ${r.spam} spam`,
        { description: r.errors ? `${r.errors} skipped` : undefined });
      fetchThreads();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Scan failed"); }
    finally { setScanning(false); }
  }

  async function openThread(t: MailThreadRow) {
    setSelected(t);
    setMessages(null);
    setLoadingThread(true);
    // Opening a thread marks it read (app-local), optimistically clearing its
    // dot. An expired snooze also clears here — that ends its resurfaced-unread
    // state (the list flags past-due snoozes unread until opened).
    const snoozeExpired = !!t.snoozed_until && new Date(t.snoozed_until) <= new Date();
    if (t.unread || snoozeExpired) {
      setThreads((xs) => xs.map((x) =>
        x.thread_key === t.thread_key && x.account_email === t.account_email
          ? { ...x, unread: false, ...(snoozeExpired ? { snoozed_until: null } : {}) } : x));
      api.setMailState({
        account_email: t.account_email, thread_key: t.thread_key, read: true,
        ...(snoozeExpired ? { snoozed_until: null } : {}),
      }).catch(() => {});
    }
    try {
      setMessages(await api.getMailThread(t.thread_key, t.account_email));
    } catch { toast.error("Couldn't load thread"); }
    setLoadingThread(false);
  }

  return (
    <div className="grid md:grid-cols-[14rem_minmax(0,20rem)_1fr] md:grid-rows-[minmax(0,1fr)] gap-4 md:flex-1 md:min-h-0 md:overflow-hidden">
      {/* SIDEBAR — compose + folders + accounts (desktop; a drawer on mobile) */}
      <aside className="hidden md:block min-w-0 md:min-h-0 md:overflow-y-auto">
        <SidebarNav view={view} pane={pane} category={category} content={content}
          account={account} accounts={accounts} navTo={navTo}
          startCompose={startCompose} openSenders={openSenders} />
      </aside>

      {/* MOBILE FILTER DRAWER — same nav, overlaid (mirrors the AppShell drawer) */}
      {navOpen && (
        <>
          <div className="fixed inset-0 z-40 bg-background/70 backdrop-blur-sm md:hidden"
            onClick={() => setNavOpen(false)} />
          <div className="fixed inset-y-0 left-0 z-50 w-64 overflow-y-auto border-r border-border bg-card p-3 md:hidden">
            <SidebarNav view={view} pane={pane} category={category} content={content}
              account={account} accounts={accounts}
              navTo={(o) => { navTo(o); setNavOpen(false); }}
              startCompose={() => { setNavOpen(false); startCompose(); }}
              openSenders={() => { openSenders(); setNavOpen(false); }} />
          </div>
        </>
      )}

      {pane === "senders" ? (
        /* SENDERS — cleanup view spans the list + reader columns */
        <div className="min-w-0 md:col-span-2 md:min-h-0 md:overflow-y-auto">
          <SendersView account={account}
            onFilterSender={(addr) => navTo({ q: addr, view: "inbox", category: "", content: "" })} />
        </div>
      ) : (<>
      {/* LIST — when a message is open, hide on mobile but stay a flex COLUMN on
          desktop (md:block would break the inner flex-1 scroll container). */}
      <div className={cn("min-w-0 md:flex md:flex-col md:min-h-0", selected && "hidden md:flex")}>
        <div className="mb-3 flex items-center gap-1.5 md:shrink-0">
          <Button size="sm" variant="ghost" className="md:hidden" onClick={() => setNavOpen(true)}
            title="Folders & filters"><SlidersHorizontal className="h-3.5 w-3.5" /></Button>
          <form onSubmit={(e) => { e.preventDefault(); fetchThreads(); }}
            className="flex items-center gap-1.5 px-2 h-9 rounded-md border border-border bg-background flex-1 min-w-0">
            <Search className="h-4 w-4 text-muted-foreground shrink-0" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search mail…"
              className="bg-transparent outline-none text-sm w-full" />
          </form>
          <Button size="sm" variant="ghost" onClick={markAllRead} title="Mark all as read"><CheckCheck className="h-3.5 w-3.5" /></Button>
          <Button size="sm" variant="ghost" onClick={scanSpam} disabled={scanning} title="Scan for spam (Rspamd)"><ShieldAlert className={cn("h-3.5 w-3.5", scanning && "animate-pulse")} /></Button>
          <Button size="sm" variant="ghost" onClick={syncNow} disabled={syncing} title="Pull new mail from Gmail"><RefreshCw className={cn("h-3.5 w-3.5", syncing && "animate-spin")} /></Button>
          <Button size="sm" variant="outline" className="md:hidden" onClick={startCompose}><PenSquare className="h-3.5 w-3.5" /></Button>
        </div>

        <div ref={listScrollRef} className="rounded-lg border border-border bg-card/40 divide-y divide-border overflow-hidden md:flex-1 md:min-h-0 md:overflow-y-auto">
          {loading ? (
            <div className="p-8 text-center text-sm text-muted-foreground">Loading…</div>
          ) : threads.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground">No mail.</div>
          ) : threads.map((t) => (
            <button key={`${t.account_email}:${t.thread_key}`} type="button" onClick={() => openThread(t)}
              className={cn("w-full text-left px-3 py-2.5 hover:bg-accent/30 transition-colors flex gap-2.5",
                selected?.thread_key === t.thread_key && "bg-accent/40")}>
              <span className={cn("mt-1.5 h-1.5 w-1.5 rounded-full shrink-0", t.unread ? "bg-sky-400" : "bg-transparent")} />
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline justify-between gap-2">
                  <span className={cn("truncate text-sm", t.unread ? "font-semibold" : "font-medium")}>
                    {t.from_name || t.from_address || "(unknown)"}
                  </span>
                  <span className="flex items-center gap-1 shrink-0">
                    {t.starred && <Star className="h-3 w-3 fill-amber-400 text-amber-400" />}
                    {snoozeActive(t) && (
                      <span className="text-[10px] px-1 rounded border border-indigo-500/40 text-indigo-400"
                        title={`Snoozed until ${new Date(t.snoozed_until!).toLocaleString()}`}>
                        until {fmtDate(t.snoozed_until!)}
                      </span>
                    )}
                    <span className="text-[11px] text-muted-foreground">{fmtDate(t.internal_date)}</span>
                  </span>
                </span>
                <span className="block truncate text-sm">{t.subject || "(no subject)"}{t.msg_count > 1 && <span className="text-muted-foreground"> · {t.msg_count}</span>}</span>
                <span className="flex items-center gap-1.5">
                  {t.is_spam && (
                    <span className="shrink-0 text-[10px] px-1 rounded border border-rose-500/40 bg-rose-500/10 text-rose-400">spam</span>
                  )}
                  {t.content_class && CONTENT_META[t.content_class] && (
                    <span className={cn("shrink-0 text-[10px] px-1 rounded border", CONTENT_META[t.content_class].cls)}>
                      {CONTENT_META[t.content_class].label}
                    </span>
                  )}
                  <SignalChips s={t.signals} />
                  <span className="block truncate text-xs text-muted-foreground min-w-0">{t.snippet}</span>
                </span>
              </span>
            </button>
          ))}
          {/* infinite-scroll sentinel + footer */}
          {!loading && threads.length > 0 && (
            <div ref={sentinelRef} className="p-3 text-center text-xs text-muted-foreground">
              {loadingMore ? "Loading more…" : hasMore ? "" : "End of mail"}
            </div>
          )}
        </div>
      </div>

      {/* READER */}
      <div className={cn("min-w-0 md:min-h-0 md:overflow-y-auto", !selected && "hidden md:block")}>
        {!selected ? (
          <div className="rounded-lg border border-border bg-card/40 p-12 text-center text-sm text-muted-foreground grid place-items-center h-full">
            <span><Mail className="h-6 w-6 mx-auto mb-2 opacity-40" />Select a conversation</span>
          </div>
        ) : (
          <div className="rounded-lg border border-border bg-card/40 overflow-hidden">
            {/* toolbar */}
            <div className="flex items-center gap-0.5 border-b border-border px-2 py-1.5">
              <button type="button" onClick={() => { setSelected(null); setMessages(null); }}
                className="md:hidden p-1.5 rounded hover:bg-accent/40 text-muted-foreground"><ChevronLeft className="h-4 w-4" /></button>
              <ToolBtn title={selected.starred ? "Unstar" : "Star"} onClick={() => act(selected, { starred: !selected.starred })}>
                <Star className={cn("h-4 w-4", selected.starred && "fill-amber-400 text-amber-400")} />
              </ToolBtn>
              <ToolBtn title={selected.archived ? "Unarchive" : "Archive"}
                onClick={() => selected.archived
                  ? act(selected, { archived: false })
                  : deferAct(selected, { archived: true }, "Archived")}>
                {selected.archived ? <ArchiveRestore className="h-4 w-4" /> : <Archive className="h-4 w-4" />}
              </ToolBtn>
              <ToolBtn title={selected.trashed ? "Restore from Trash" : "Trash (Gmail Trash, 30-day recovery)"}
                onClick={() => selected.trashed
                  ? act(selected, { trashed: false })
                  : deferAct(selected, { trashed: true }, "Trashed")}>
                {selected.trashed ? <ArchiveRestore className="h-4 w-4 text-emerald-400/80" /> : <Trash2 className="h-4 w-4 text-rose-400/80" />}
              </ToolBtn>
              <ToolBtn title="Add sender to clear list (bulk-trash later from Cleanup)" onClick={addToClearList}>
                <ListX className="h-4 w-4" />
              </ToolBtn>
              <SnoozeMenu t={selected} onSnooze={(iso) => act(selected, { snoozed_until: iso })} />
              <ToolBtn title="Mark unread" onClick={() => act(selected, { read: false })}><MailOpen className="h-4 w-4" /></ToolBtn>
              <div className="w-px h-4 bg-border mx-1" />
              <ToolBtn title="Reply" onClick={startReply}><Reply className="h-4 w-4" /></ToolBtn>
              <ToolBtn title="Forward" onClick={startForward}><Forward className="h-4 w-4" /></ToolBtn>
              <div className="ml-auto flex items-center gap-1.5 pr-1">
                <ClassMenu t={selected} onCorrect={(cls) => correctClass(selected, cls)} />
                {selected.is_spam && <span className="text-[10px] px-1 rounded border border-rose-500/40 text-rose-400">spam</span>}
              </div>
            </div>
            {/* body — constrained to a readable column */}
            <div className="p-4 mx-auto max-w-4xl">
              <h2 className="text-lg font-semibold break-words mb-3">{selected.subject || "(no subject)"}</h2>
              {loadingThread ? (
                <div className="p-8 text-center text-sm text-muted-foreground">Loading…</div>
              ) : (
                <>
                  <div className="space-y-3">
                    {(messages || []).map((m) => <MessageView key={m.message_id} m={m} />)}
                  </div>
                  {(messages?.length ?? 0) > 0 && (
                    <div className="mt-3 flex gap-2">
                      <Button size="sm" variant="outline" onClick={startReply}><Reply className="h-3.5 w-3.5 mr-1" /> Reply</Button>
                      <Button size="sm" variant="outline" onClick={startForward}><Forward className="h-3.5 w-3.5 mr-1" /> Forward</Button>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>
      </>)}

      {compose && <ComposeDialog c={compose} accounts={accounts} onClose={() => setCompose(null)} />}
    </div>
  );
}

function ComposeDialog({ c, accounts, onClose }: { c: Compose; accounts: MailAccount[]; onClose: () => void }) {
  const [to, setTo] = useState(c.to);
  const [subject, setSubject] = useState(c.subject);
  // Only send-capable accounts appear in From; a reply on a non-sendable account
  // falls back to the first sendable one (the recipient still threads by headers).
  const sendable = accounts.filter((a) => a.can_send);
  const [from, setFrom] = useState(
    sendable.some((a) => a.account_email === c.account_email)
      ? c.account_email : (sendable[0]?.account_email ?? c.account_email));
  const [busy, setBusy] = useState(false);
  const isReply = !!c.thread_id;
  const editorRef = useRef<HTMLDivElement | null>(null);

  // Seed the WYSIWYG editor once (forward pre-fills a quoted body). contentEditable
  // is uncontrolled — we read innerHTML/innerText on send, not on every keystroke.
  useEffect(() => {
    if (editorRef.current && c.body) editorRef.current.innerText = c.body;
  }, [c.body]);

  function exec(cmd: string, arg?: string) {
    editorRef.current?.focus();
    document.execCommand(cmd, false, arg);   // simple, dependency-free rich text
  }
  function addLink() {
    const url = prompt("Link URL"); if (!url) return;
    exec("createLink", /^https?:\/\//i.test(url) ? url : `https://${url}`);
  }

  async function send() {
    if (!to.trim()) { toast.error("Add a recipient"); return; }
    const el = editorRef.current;
    const html = (el?.innerHTML ?? "").trim();
    const text = (el?.innerText ?? "").trim();
    if (!text) { toast.error("Write a message"); return; }
    setBusy(true);
    try {
      await api.sendMail({
        account_email: from, to: to.trim(), subject, body: text, html,
        in_reply_to: c.in_reply_to, references: c.references, thread_id: c.thread_id,
      });
      toast.success("Sent");
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Send failed");
    }
    setBusy(false);
  }

  const TB = "h-7 min-w-7 px-1.5 rounded text-sm text-muted-foreground hover:bg-accent hover:text-foreground";
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-background/70 backdrop-blur-sm p-4 sm:p-8" onClick={onClose}>
      <div className="flex flex-col w-full max-w-3xl h-[85vh] rounded-lg border border-border bg-card p-4" onClick={(e) => e.stopPropagation()}>
        <h3 className="font-medium mb-3 shrink-0">{isReply ? "Reply" : "New message"}</h3>
        <div className="space-y-2 shrink-0">
          {sendable.length > 1 ? (
            <select value={from} onChange={(e) => setFrom(e.target.value)}
              className="w-full h-9 px-2 text-sm rounded-md border border-border bg-background text-muted-foreground">
              {sendable.map((a) => <option key={a.account_email} value={a.account_email}>From: {a.account_email}</option>)}
            </select>
          ) : (
            <div className="text-xs text-muted-foreground">From: {from}</div>
          )}
          <Input placeholder="To" type="email" value={to} onChange={(e) => setTo(e.target.value)} />
          <Input placeholder="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
        </div>
        {/* WYSIWYG toolbar + editor */}
        <div className="mt-2 flex items-center gap-0.5 border border-b-0 border-border rounded-t-md px-1.5 py-1 shrink-0">
          <button type="button" className={cn(TB, "font-bold")} title="Bold" onClick={() => exec("bold")}>B</button>
          <button type="button" className={cn(TB, "italic")} title="Italic" onClick={() => exec("italic")}>I</button>
          <button type="button" className={cn(TB, "underline")} title="Underline" onClick={() => exec("underline")}>U</button>
          <span className="w-px h-4 bg-border mx-1" />
          <button type="button" className={TB} title="Bulleted list" onClick={() => exec("insertUnorderedList")}>•</button>
          <button type="button" className={TB} title="Numbered list" onClick={() => exec("insertOrderedList")}>1.</button>
          <button type="button" className={TB} title="Link" onClick={addLink}>🔗</button>
          <span className="w-px h-4 bg-border mx-1" />
          <button type="button" className={TB} title="Clear formatting" onClick={() => exec("removeFormat")}>⌫</button>
        </div>
        <div ref={editorRef} contentEditable suppressContentEditableWarning
          data-placeholder="Write your message…"
          className="flex-1 min-h-0 overflow-y-auto rounded-b-md border border-border bg-background p-3 text-sm leading-relaxed outline-none focus:border-ring [&:empty]:before:content-[attr(data-placeholder)] [&:empty]:before:text-muted-foreground" />
        <div className="flex justify-end gap-2 pt-3 shrink-0">
          <Button type="button" variant="ghost" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="button" size="sm" onClick={send} disabled={busy}>
            <Send className="h-3.5 w-3.5 mr-1" /> {busy ? "Sending…" : "Send"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function MessageView({ m }: { m: MailMessage }) {
  // HTML is the DEFAULT now (P2). Execution is already impossible — sandbox=""
  // blocks scripts, plugins, forms, and same-origin — so the remaining risk is
  // remote fetches (tracking pixels / fingerprinting). A CSP <meta> in the srcDoc
  // blocks all remote loads until the user clicks "Load images".
  const [showHtml, setShowHtml] = useState(!!m.body_html);
  const [loadImages, setLoadImages] = useState(false);
  const csp = loadImages
    ? "default-src 'none'; img-src * data:; style-src 'unsafe-inline'; font-src *"
    : "default-src 'none'; img-src data:; style-src 'unsafe-inline'";
  const htmlDoc = `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="${csp}"></head><body>${m.body_html ?? ""}</body></html>`;
  return (
    <div className="rounded-lg border border-border bg-card/40 p-3">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <div className="min-w-0">
          <span className="text-sm font-medium">{m.from_name || m.from_address}</span>
          {m.from_name && m.from_address && <span className="text-xs text-muted-foreground"> &lt;{m.from_address}&gt;</span>}
        </div>
        <span className="text-[11px] text-muted-foreground shrink-0">{new Date(m.internal_date).toLocaleString()}</span>
      </div>
      {(m.to_addresses?.length ?? 0) > 0 && (
        <div className="text-[11px] text-muted-foreground mb-2 truncate">to {m.to_addresses!.join(", ")}</div>
      )}
      {(m.is_spam || m.content_class || (m.signals && (m.signals.automated || m.signals.bulk || m.signals.mailing_list || m.signals.has_unsubscribe))) && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          {m.is_spam && (
            <span className="text-[10px] px-1 rounded border border-rose-500/40 bg-rose-500/10 text-rose-400"
              title={m.spam_action ? `Rspamd: ${m.spam_action}${m.spam_score != null ? ` (${m.spam_score})` : ""}` : undefined}>
              spam
            </span>
          )}
          {m.content_class && CONTENT_META[m.content_class] && (
            <span className={cn("text-[10px] px-1 rounded border", CONTENT_META[m.content_class].cls)}
              title={m.content_confidence != null ? `SetFit: ${Math.round(m.content_confidence * 100)}%` : undefined}>
              {CONTENT_META[m.content_class].label}
            </span>
          )}
          <SignalChips s={m.signals} />
          {m.signals.unsubscribe_url && (
            <a href={m.signals.unsubscribe_url} target="_blank" rel="noopener noreferrer nofollow"
              className="inline-flex items-center gap-1 text-[10px] px-1 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-muted">
              unsubscribe
            </a>
          )}
        </div>
      )}
      {showHtml && m.body_html ? (
        // sandboxed: no scripts, no same-origin → untrusted HTML can't execute or
        // read cookies; the CSP meta blocks remote loads until opted in.
        <iframe sandbox="" srcDoc={htmlDoc} title="email"
          className="w-full h-[65vh] rounded border border-border bg-white" />
      ) : (
        <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed">{m.body_text || "(no text body)"}</pre>
      )}
      <div className="mt-2 flex items-center gap-3">
        {showHtml && m.body_html && !loadImages && (
          <button type="button" onClick={() => setLoadImages(true)}
            className="text-xs text-muted-foreground hover:text-foreground">
            Load remote images
          </button>
        )}
        {m.body_html && m.body_text && (
          <button type="button" onClick={() => setShowHtml((v) => !v)}
            className="text-xs text-muted-foreground hover:text-foreground">
            {showHtml ? "Show plain text" : "Show HTML"}
          </button>
        )}
      </div>
      {(m.attachments?.length ?? 0) > 0 && (
        <div className="mt-2 flex flex-wrap gap-2 border-t border-border pt-2">
          {m.attachments.map((a) => {
            const href = `/api/mail/attachment?account=${encodeURIComponent(m.account_email)}`
              + `&message_id=${encodeURIComponent(m.message_id)}`
              + `&attachment_id=${encodeURIComponent(a.attachment_id)}`
              + `&filename=${encodeURIComponent(a.filename)}`;
            return (
              <a key={a.attachment_id} href={href} download={a.filename}
                className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs hover:bg-muted">
                <Paperclip className="h-3 w-3 shrink-0 text-muted-foreground" />
                <span className="truncate">{a.filename}</span>
                {a.size != null && <span className="shrink-0 text-muted-foreground">{formatBytes(a.size)}</span>}
                <Download className="h-3 w-3 shrink-0 text-muted-foreground" />
              </a>
            );
          })}
        </div>
      )}
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// --- sidebar + reader-toolbar primitives ------------------------------------

// The sidebar content, shared by the desktop <aside> and the mobile drawer.
function SidebarNav({ view, pane, category, content, account, accounts, navTo, startCompose, openSenders }: {
  view: MailView; pane: "threads" | "senders"; category: string; content: string; account: string;
  accounts: MailAccount[];
  navTo: (over: { view?: MailView; category?: string; content?: string; account?: string; q?: string }) => void;
  startCompose: () => void; openSenders: () => void;
}) {
  return (
    <>
      <Button variant="outline" size="sm" className="w-full mb-3 justify-start" onClick={startCompose}>
        <PenSquare className="h-3.5 w-3.5 mr-2" /> Compose
      </Button>
      <nav className="space-y-0.5 text-sm">
        <NavRow label="All mail" Icon={Inbox} active={pane === "threads" && view === "inbox" && !category && !content}
          onClick={() => navTo({ view: "inbox", category: "", content: "" })} />
        <NavRow label="Starred" Icon={Star} active={pane === "threads" && view === "starred"}
          onClick={() => navTo({ view: "starred", category: "", content: "" })} />
        <NavRow label="Archived" Icon={Archive} active={pane === "threads" && view === "archived"}
          onClick={() => navTo({ view: "archived", category: "", content: "" })} />
        <NavRow label="Snoozed" Icon={Clock} active={pane === "threads" && view === "snoozed"}
          onClick={() => navTo({ view: "snoozed", category: "", content: "" })} />
        <NavRow label="Trash" Icon={Trash2} active={pane === "threads" && view === "trash"}
          onClick={() => navTo({ view: "trash", category: "", content: "" })} />
        <NavRow label="Senders" Icon={Users} active={pane === "senders"} onClick={openSenders} />
        <NavHeading>Categories</NavHeading>
        {([
          { key: "personal", label: "Personal" },
          { key: "newsletter", label: "Newsletter" },
          { key: "transactional", label: "Transactional" },
        ]).map((c) => (
          <NavRow key={c.key} label={c.label}
            active={pane === "threads" && view === "inbox" && content === c.key && !category}
            onClick={() => navTo({ view: "inbox", content: c.key, category: "" })} />
        ))}
        <NavRow label="Spam" active={pane === "threads" && view === "inbox" && category === "spam"}
          onClick={() => navTo({ view: "inbox", category: "spam", content: "" })} />

        {accounts.length > 1 && (
          <>
            <NavHeading>Accounts</NavHeading>
            <NavRow label="All accounts" active={!account} onClick={() => navTo({ account: "" })} />
            {accounts.map((a) => (
              <NavRow key={a.account_email} label={a.account_email} title={a.account_email}
                active={account === a.account_email} onClick={() => navTo({ account: a.account_email })} />
            ))}
          </>
        )}
      </nav>
    </>
  );
}

// Undo toast body: label + Undo + a CSS-only countdown bar (no JS ticks —
// the width animation IS the countdown; commit timing lives in deferAct).
function UndoToast({ label, onUndo }: { label: string; onUndo: () => void }) {
  return (
    <div className="w-full min-w-56">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm">{label}</span>
        <button type="button" onClick={onUndo}
          className="text-sm font-medium text-primary hover:underline shrink-0">Undo</button>
      </div>
      <div className="mt-2 h-0.5 rounded bg-primary/70 mail-undo-bar" />
    </div>
  );
}

// Snooze preset menu (hand-rolled anchored card — the repo has no Popover dep).
// Presets compute in the browser's local tz and go over the wire as ISO;
// snooze is app-local only (Gmail has no snooze concept).
// Content-class badge as a corrector (triage iteration): click → pick the right
// class; stored as user ground truth (feeds retrain eval + rule tuning). Same
// hand-rolled anchored-menu pattern as SnoozeMenu; right-aligned (the badge
// sits at the toolbar's right edge).
function ClassMenu({ t, onCorrect }: {
  t: MailThreadRow; onCorrect: (cls: "newsletter" | "transactional" | "personal") => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const meta = t.content_class ? CONTENT_META[t.content_class] : null;
  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen((v) => !v)}
        title="Correct the classification (saved as ground truth)"
        className={cn("text-[10px] px-1 rounded border cursor-pointer hover:bg-accent/40",
          meta ? meta.cls : "border-border text-muted-foreground")}>
        {meta ? meta.label : "classify"}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-20 w-44 rounded-md border border-border bg-card p-1 shadow-lg">
          {(Object.keys(CONTENT_META) as Array<"newsletter" | "transactional" | "personal">).map((cls) => (
            <button key={cls} type="button"
              onClick={() => { onCorrect(cls); setOpen(false); }}
              className={cn("w-full text-left px-2 py-1.5 text-sm rounded hover:bg-accent/40 flex items-center justify-between",
                t.content_class === cls && "bg-accent/30")}>
              <span>{CONTENT_META[cls].label}</span>
              {t.content_class === cls && <span className="text-[10px] text-muted-foreground">current</span>}
            </button>
          ))}
          <div className="px-2 pt-1 pb-0.5 text-[10px] text-muted-foreground border-t border-border mt-1">
            Saved as ground truth for the whole thread
          </div>
        </div>
      )}
    </div>
  );
}

function SnoozeMenu({ t, onSnooze }: { t: MailThreadRow; onSnooze: (iso: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState("");
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const active = snoozeActive(t);
  function pick(d: Date) { onSnooze(d.toISOString()); setOpen(false); setCustom(""); }
  function laterToday() { const d = new Date(); d.setHours(d.getHours() + 4); pick(d); }
  function tomorrow9() { const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0); pick(d); }
  function nextMonday9() {
    const d = new Date();
    d.setDate(d.getDate() + (((1 - d.getDay() + 7) % 7) || 7));
    d.setHours(9, 0, 0, 0); pick(d);
  }
  function pickCustom() {
    const d = new Date(custom);
    if (isNaN(d.getTime()) || d <= new Date()) { toast.error("Pick a future time"); return; }
    pick(d);
  }

  const ITEM = "w-full text-left px-2 py-1.5 text-sm rounded hover:bg-accent/40";
  return (
    <div className="relative" ref={ref}>
      <ToolBtn
        title={active
          ? `Snoozed until ${new Date(t.snoozed_until!).toLocaleString()} (app-local)`
          : "Snooze (app-local — Gmail unaffected)"}
        onClick={() => setOpen((v) => !v)}>
        <Clock className={cn("h-4 w-4", active && "text-indigo-400")} />
      </ToolBtn>
      {open && (
        <div className="absolute left-0 top-full mt-1 z-20 w-56 rounded-md border border-border bg-card p-1 shadow-lg">
          {active && (
            <button type="button" className={cn(ITEM, "text-indigo-400")}
              onClick={() => { onSnooze(null); setOpen(false); }}>Unsnooze</button>
          )}
          <button type="button" className={ITEM} onClick={laterToday}>Later today (+4h)</button>
          <button type="button" className={ITEM} onClick={tomorrow9}>Tomorrow 9:00</button>
          <button type="button" className={ITEM} onClick={nextMonday9}>Next Monday 9:00</button>
          <div className="border-t border-border mt-1 pt-1.5 px-2 pb-1.5">
            <input type="datetime-local" value={custom} onChange={(e) => setCustom(e.target.value)}
              className="w-full h-8 px-1.5 text-xs rounded border border-border bg-background outline-none focus:border-ring" />
            <Button size="sm" variant="outline" className="w-full mt-1.5 h-7" disabled={!custom} onClick={pickCustom}>
              Snooze until
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function NavHeading({ children }: { children: ReactNode }) {
  return <div className="px-2 pt-4 pb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60">{children}</div>;
}

function NavRow({ label, Icon, active, onClick, title }: {
  label: string; Icon?: LucideIcon; active?: boolean; onClick: () => void; title?: string;
}) {
  return (
    <button type="button" onClick={onClick} title={title}
      className={cn("w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left transition-colors",
        active ? "bg-accent text-foreground font-medium" : "text-muted-foreground hover:bg-accent/40 hover:text-foreground")}>
      {Icon
        ? <Icon className="h-3.5 w-3.5 shrink-0" />
        : <span className="ml-1 mr-0.5 h-1.5 w-1.5 rounded-full bg-current opacity-40 shrink-0" />}
      <span className="truncate">{label}</span>
    </button>
  );
}

function ToolBtn({ title, onClick, children }: { title: string; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" title={title} onClick={onClick}
      className="p-1.5 rounded hover:bg-accent/40 text-muted-foreground hover:text-foreground transition-colors">
      {children}
    </button>
  );
}

// Senders cleanup view (round 2 / P3): mail grouped by sender, highest volume
// first, with bulk per-sender actions — the fast way to clear garbage mail.
function SendersView({ account, onFilterSender }: { account: string; onFilterSender: (addr: string) => void }) {
  const [rows, setRows] = useState<MailSenderRow[] | null>(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async (search?: string) => {
    setRows(null);
    try {
      setRows(await api.listMailSenders({
        account: account || undefined, q: (search ?? "").trim() || undefined, limit: 150 }));
    } catch { toast.error("Couldn't load senders"); setRows([]); }
  }, [account]);
  useEffect(() => { load(); }, [load]);

  async function act(r: MailSenderRow, action: "read" | "archive" | "trash") {
    if (action === "trash" && !confirm(
      `Move ALL ${r.messages} messages from ${r.from_address} to Gmail Trash?\n(Recoverable in Gmail for 30 days.)`)) return;
    setBusy(r.from_address);
    try {
      const res = await api.actOnMailSender({
        from_address: r.from_address, account: account || undefined, action });
      const verb = action === "read" ? "Marked read" : action === "archive" ? "Archived" : "Trashed";
      toast.success(`${verb} — ${res.threads} threads${res.gmail_pushed ? ` · ${res.gmail_pushed} pushed to Gmail` : ""}`,
        res.gmail_errors ? { description: res.gmail_errors.join("; ") } : undefined);
      setRows((xs) => xs?.map((x) => x.from_address === r.from_address
        ? { ...x, unread: 0 } : x) ?? null);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
    setBusy(null);
  }

  return (
    <div className="min-w-0">
      <form onSubmit={(e) => { e.preventDefault(); load(q); }}
        className="mb-3 flex items-center gap-1.5 px-2 h-9 rounded-md border border-border bg-background max-w-md">
        <Search className="h-4 w-4 text-muted-foreground shrink-0" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search senders…"
          className="bg-transparent outline-none text-sm w-full" />
      </form>
      <div className="rounded-lg border border-border bg-card/40 divide-y divide-border overflow-hidden">
        {rows === null ? (
          <div className="p-8 text-center text-sm text-muted-foreground">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="p-8 text-center text-sm text-muted-foreground">No senders.</div>
        ) : rows.map((r) => (
          <div key={r.from_address}
            className={cn("flex items-center gap-3 px-3 py-2 hover:bg-accent/20 transition-colors",
              busy === r.from_address && "opacity-50 pointer-events-none")}>
            <button type="button" onClick={() => onFilterSender(r.from_address)}
              className="min-w-0 flex-1 text-left" title="Show this sender's mail">
              <span className="block truncate text-sm font-medium">{r.from_name || r.from_address}</span>
              <span className="block truncate text-xs text-muted-foreground">{r.from_address}</span>
            </button>
            {r.content_class && CONTENT_META[r.content_class] && (
              <span className={cn("hidden sm:inline shrink-0 text-[10px] px-1 rounded border", CONTENT_META[r.content_class].cls)}>
                {CONTENT_META[r.content_class].label}
              </span>
            )}
            {r.unread > 0 && (
              <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full bg-sky-500/15 text-sky-400 border border-sky-500/30">
                {r.unread} unread
              </span>
            )}
            <span className="shrink-0 text-xs text-muted-foreground tabular-nums w-14 text-right">{r.messages} msgs</span>
            <span className="hidden md:inline shrink-0 text-[11px] text-muted-foreground w-16 text-right">
              {r.last_at ? fmtDate(r.last_at) : ""}
            </span>
            <span className="flex items-center gap-0.5 shrink-0">
              {r.unsubscribe_url && (
                <a href={r.unsubscribe_url} target="_blank" rel="noopener noreferrer nofollow"
                  className="text-[10px] px-1 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-muted mr-1">
                  unsubscribe
                </a>
              )}
              <ToolBtn title="Mark all read" onClick={() => act(r, "read")}><CheckCheck className="h-4 w-4" /></ToolBtn>
              <ToolBtn title="Archive all" onClick={() => act(r, "archive")}><Archive className="h-4 w-4" /></ToolBtn>
              <ToolBtn title="Trash all (Gmail Trash, 30-day recovery)" onClick={() => act(r, "trash")}>
                <Trash2 className="h-4 w-4 text-rose-400/80" />
              </ToolBtn>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Header-heuristic triage chips (Phase B) — complement the Gmail-label category.
function SignalChips({ s }: { s?: MailTriageSignals }) {
  if (!s) return null;
  const chips: string[] = [];
  if (s.automated) chips.push("auto");
  if (s.bulk) chips.push("bulk");
  if (s.mailing_list) chips.push("list");
  if (chips.length === 0) return null;
  return (
    <>
      {chips.map((c) => (
        <span key={c} className="shrink-0 text-[10px] px-1 rounded border border-border text-muted-foreground">
          {c}
        </span>
      ))}
    </>
  );
}
