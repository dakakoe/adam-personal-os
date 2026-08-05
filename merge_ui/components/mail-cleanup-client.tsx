"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Search, Trash2, BellOff, Loader2, Sparkles, AlertTriangle, RefreshCw, Pin, PinOff, ListX } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { api, type MailAccount, type MailCleanupSenderRow } from "@/lib/api";

type Filter = "all" | "recommended" | "newsletter" | "transactional" | "unsub" | "clear";
const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "recommended", label: "Recommended" },
  { key: "newsletter", label: "Newsletters" },
  { key: "transactional", label: "Transactional" },
  { key: "unsub", label: "Unsubscribable" },
  { key: "clear", label: "Clear list" },
];

const CONTENT_META: Record<string, { label: string; cls: string }> = {
  newsletter: { label: "Newsletter", cls: "text-amber-400 border-amber-500/40" },
  transactional: { label: "Transactional", cls: "text-emerald-400 border-emerald-500/40" },
  personal: { label: "Personal", cls: "text-blue-400 border-blue-500/40" },
};

const NOISE_ADDR = /(no-?reply|noreply|notif|no_reply|mailer|bounce|updates?|newsletter|digest|do-?not-?reply)/i;

// A sender is "recommended for cleanup" = clearly automated noise you've never
// emailed back: has an unsubscribe link OR is a newsletter/transactional OR looks
// like a no-reply address — and you have NOT replied to it (keeps humans out).
function isRecommended(r: MailCleanupSenderRow): boolean {
  if (r.replied || r.kept) return false;   // kept = you pinned it; never recommend
  return Boolean(r.unsubscribe_url) || r.content_class === "newsletter"
    || r.content_class === "transactional" || NOISE_ADDR.test(r.from_address);
}

function matchesFilter(r: MailCleanupSenderRow, f: Filter): boolean {
  switch (f) {
    case "recommended": return isRecommended(r);
    case "newsletter": return r.content_class === "newsletter";
    case "transactional": return r.content_class === "transactional";
    case "unsub": return Boolean(r.unsubscribe_url);
    default: return true;
  }
}

function fmtDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}

export function MailCleanupClient({ accounts }: { accounts: MailAccount[] }) {
  const [rows, setRows] = useState<MailCleanupSenderRow[] | null>(null);
  const [clearRows, setClearRows] = useState<MailCleanupSenderRow[] | null>(null);
  const [q, setQ] = useState("");
  const [account, setAccount] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const onClear = filter === "clear";

  const load = useCallback(async (search?: string) => {
    setRows(null);
    setSel(new Set());
    try {
      setRows(await api.listMailCleanupSenders({
        account: account || undefined, q: (search ?? "").trim() || undefined, limit: 500 }));
    } catch { toast.error("Couldn't load senders"); setRows([]); }
  }, [account]);
  useEffect(() => { load(); }, [load]);

  // The clear list is a separate server view (all flagged senders, any volume),
  // fetched on demand when its tab is open.
  const loadClear = useCallback(async () => {
    setClearRows(null);
    setSel(new Set());
    try {
      setClearRows(await api.listMailClearList({ account: account || undefined }));
    } catch { toast.error("Couldn't load the clear list"); setClearRows([]); }
  }, [account]);
  useEffect(() => { if (onClear) loadClear(); }, [onClear, loadClear]);

  const source = onClear ? clearRows : rows;
  const visible = useMemo(() => onClear ? (clearRows ?? [])
    : (rows ?? []).filter((r) => matchesFilter(r, filter)), [onClear, clearRows, rows, filter]);
  // "Select recommended" acts on what's shown — so it respects the active filter.
  const recommendedVisible = useMemo(() => visible.filter(isRecommended), [visible]);
  const selTotals = useMemo(() => {
    const chosen = (source ?? []).filter((r) => sel.has(r.from_address));
    return { senders: chosen.length, messages: chosen.reduce((n, r) => n + r.messages, 0) };
  }, [source, sel]);

  function toggle(addr: string) {
    setSel((s) => { const n = new Set(s); n.has(addr) ? n.delete(addr) : n.add(addr); return n; });
  }
  function selectRecommended() {
    setSel(new Set(recommendedVisible.map((r) => r.from_address)));
  }
  function selectAll() {
    setSel(new Set(visible.map((r) => r.from_address)));
  }
  // Patch a sender's flags in whichever list(s) hold it.
  function patchRow(addr: string, patch: Partial<MailCleanupSenderRow>) {
    const apply = (xs: MailCleanupSenderRow[] | null) =>
      xs?.map((x) => x.from_address === addr ? { ...x, ...patch } : x) ?? null;
    setRows(apply); setClearRows(apply);
  }

  async function keep(r: MailCleanupSenderRow) {
    const next = !r.kept;
    patchRow(r.from_address, { kept: next, ...(next ? { on_clear_list: false } : {}) });
    setSel((s) => { if (!next) return s; const n = new Set(s); n.delete(r.from_address); return n; });
    try {
      await api.keepMailSender({ from_address: r.from_address, keep: next });
      toast.success(next ? `Keeping ${r.from_name || r.from_address} — won't recommend it` : "No longer kept");
    } catch (e) {
      patchRow(r.from_address, { kept: r.kept });
      toast.error(e instanceof Error ? e.message : "Failed");
    }
  }

  async function clearList(r: MailCleanupSenderRow) {
    const next = !r.on_clear_list;
    patchRow(r.from_address, { on_clear_list: next, ...(next ? { kept: false } : {}) });
    // In the clear-list view, removing takes it out of sight.
    if (!next && onClear) setClearRows((xs) => xs?.filter((x) => x.from_address !== r.from_address) ?? null);
    try {
      await api.clearListMailSender({ from_address: r.from_address, clear: next });
      toast.success(next ? `Added ${r.from_name || r.from_address} to the clear list` : "Removed from clear list");
    } catch (e) {
      patchRow(r.from_address, { on_clear_list: r.on_clear_list });
      toast.error(e instanceof Error ? e.message : "Failed");
    }
  }

  // Drop trashed senders from both lists and the selection.
  function removeRows(addrs: string[]) {
    const gone = new Set(addrs);
    const drop = (xs: MailCleanupSenderRow[] | null) => xs?.filter((x) => !gone.has(x.from_address)) ?? null;
    setRows(drop); setClearRows(drop);
    setSel(new Set());
  }

  // The actual trash, no confirm (callers confirm). alsoUnsub fires one-click
  // unsubscribes for the senders that offer them, first.
  async function doTrash(addrs: string[], alsoUnsub: boolean) {
    if (addrs.length === 0) return;
    const chosen = (source ?? []).filter((r) => addrs.includes(r.from_address));
    setBusy(true);
    try {
      let unsubOk = 0;
      if (alsoUnsub) {
        const targets = chosen.filter((r) => r.unsubscribe_url);
        const res = await Promise.allSettled(targets.map((r) =>
          api.unsubscribeMailSender({ from_address: r.from_address, account: account || undefined })));
        unsubOk = res.filter((x) => x.status === "fulfilled" && x.value.ok).length;
      }
      const res = await api.bulkActOnMailSenders({
        from_addresses: addrs, account: account || undefined, action: "trash" });
      removeRows(addrs);
      toast.success(
        `Trashed ${res.threads} threads from ${res.senders} sender${res.senders === 1 ? "" : "s"}`
        + (alsoUnsub ? ` · unsubscribed ${unsubOk}` : "")
        + (res.gmail_pushed ? ` · ${res.gmail_pushed} pushed to Gmail` : ""),
        res.gmail_errors ? { description: res.gmail_errors.slice(0, 3).join("; ") } : undefined);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
    setBusy(false);
  }

  function bulkTrash(addrs: string[], alsoUnsub: boolean) {
    if (addrs.length === 0) return;
    const chosen = (source ?? []).filter((r) => addrs.includes(r.from_address));
    const msgs = chosen.reduce((n, r) => n + r.messages, 0);
    const withReplied = chosen.filter((r) => r.replied).length;
    const warn = withReplied > 0
      ? `\n\n⚠️ ${withReplied} of these are senders you've emailed before.` : "";
    if (!confirm(
      `Move ALL ${msgs} messages from ${addrs.length} sender${addrs.length === 1 ? "" : "s"} to Gmail Trash?`
      + (alsoUnsub ? "\n(Unsubscribes first where possible.)" : "")
      + "\n(Recoverable in Gmail for 30 days.)" + warn)) return;
    void doTrash(addrs, alsoUnsub);
  }

  async function unsubscribe(r: MailCleanupSenderRow) {
    setRowBusy(r.from_address);
    let done = false;
    try {
      const res = await api.unsubscribeMailSender({ from_address: r.from_address, account: account || undefined });
      if (res.method === "one-click") {
        if (res.ok) { toast.success(`Unsubscribed from ${r.from_name || r.from_address}`); done = true; }
        else toast.error("Unsubscribe request failed", { description: res.error });
      } else if (res.method === "link" && res.url) {
        window.open(res.url, "_blank", "noopener,noreferrer");
        toast.message("Opened the unsubscribe page in a new tab"); done = true;
      } else if (res.method === "mailto" && res.url) {
        window.location.href = res.url; done = true;
      } else {
        toast.error("This sender offers no unsubscribe link");
      }
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
    setRowBusy(null);
    // Unsubscribing stops FUTURE mail; offer to clear the EXISTING backlog too.
    if (done && confirm(`Unsubscribed. Also move all ${r.messages} existing messages from ${r.from_name || r.from_address} to Trash?`)) {
      void doTrash([r.from_address], false);
    }
  }

  return (
    <div className="min-w-0">
      {/* controls */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <form onSubmit={(e) => { e.preventDefault(); load(q); }}
          className="flex items-center gap-1.5 px-2 h-9 rounded-md border border-border bg-background flex-1 min-w-[180px] max-w-sm">
          <Search className="h-4 w-4 text-muted-foreground shrink-0" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search senders…"
            className="bg-transparent outline-none text-sm w-full" />
        </form>
        {accounts.length > 1 && (
          <select value={account} onChange={(e) => setAccount(e.target.value)}
            className="h-9 rounded-md border border-border bg-background text-sm px-2">
            <option value="">All accounts</option>
            {accounts.map((a) => <option key={a.account_email} value={a.account_email}>{a.account_email}</option>)}
          </select>
        )}
        <Button variant="outline" size="sm" onClick={() => onClear ? loadClear() : load(q)} disabled={source === null}>
          <RefreshCw className={cn("h-3.5 w-3.5", source === null && "animate-spin")} />
        </Button>
        {onClear ? (visible.length > 0 && (
          <Button variant="outline" size="sm" onClick={selectAll}>Select all ({visible.length})</Button>
        )) : (recommendedVisible.length > 0 && (
          <Button variant="outline" size="sm" onClick={selectRecommended}>
            <Sparkles className="h-3.5 w-3.5 mr-1 text-amber-400" />
            Select recommended ({recommendedVisible.length})
          </Button>
        ))}
      </div>

      {/* filter chips */}
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        {FILTERS.map((f) => (
          <button key={f.key} type="button" onClick={() => setFilter(f.key)}
            className={cn("text-xs px-2.5 h-7 rounded-full border transition-colors",
              filter === f.key
                ? "border-amber-500/50 bg-amber-500/15 text-amber-300"
                : "border-border text-muted-foreground hover:text-foreground hover:bg-accent/40")}>
            {f.label}
          </button>
        ))}
      </div>

      {/* sticky action bar when something is selected */}
      {selTotals.senders > 0 && (
        <div className="sticky top-2 z-10 mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card/95 backdrop-blur px-3 py-2 shadow-sm">
          <span className="text-sm font-medium">
            {selTotals.senders} sender{selTotals.senders === 1 ? "" : "s"} · {selTotals.messages} emails
          </span>
          <span className="flex-1" />
          <Button size="sm" variant="ghost" onClick={() => setSel(new Set())} disabled={busy}>Clear</Button>
          <Button size="sm" variant="outline" disabled={busy}
            onClick={() => bulkTrash([...sel], true)}>
            <BellOff className="h-3.5 w-3.5 mr-1" /> Unsubscribe + Trash
          </Button>
          <Button size="sm" variant="destructive" disabled={busy}
            onClick={() => bulkTrash([...sel], false)}>
            {busy ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Trash2 className="h-3.5 w-3.5 mr-1" />}
            Trash selected
          </Button>
        </div>
      )}

      {/* sender list */}
      <div className="rounded-lg border border-border bg-card/40 divide-y divide-border overflow-hidden">
        {source === null ? (
          <div className="p-8 text-center text-sm text-muted-foreground">Loading…</div>
        ) : visible.length === 0 ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            {onClear ? "Clear list is empty. Add senders with the 🗑 button while reading mail."
              : `No senders${filter !== "all" ? " match this filter" : ""}.`}
          </div>
        ) : visible.map((r) => {
          const checked = sel.has(r.from_address);
          const rec = isRecommended(r);
          return (
            <label key={r.from_address}
              className={cn("flex items-center gap-3 px-3 py-2 hover:bg-accent/20 transition-colors cursor-pointer",
                rowBusy === r.from_address && "opacity-50 pointer-events-none",
                checked && "bg-accent/30")}>
              <input type="checkbox" checked={checked} onChange={() => toggle(r.from_address)}
                className="h-4 w-4 shrink-0 accent-rose-500" />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <span className="truncate text-sm font-medium">{r.from_name || r.from_address}</span>
                  {rec && (
                    <span className="shrink-0 text-[10px] px-1 rounded border border-amber-500/40 text-amber-400">rec</span>
                  )}
                  {r.replied && (
                    <span className="shrink-0 inline-flex items-center gap-0.5 text-[10px] px-1 rounded border border-orange-500/40 text-orange-400"
                      title="You've emailed this address before">
                      <AlertTriangle className="h-2.5 w-2.5" /> you replied
                    </span>
                  )}
                  {r.kept && (
                    <span className="shrink-0 inline-flex items-center gap-0.5 text-[10px] px-1 rounded border border-sky-500/40 text-sky-400"
                      title="Kept — excluded from recommendations">
                      <Pin className="h-2.5 w-2.5" /> kept
                    </span>
                  )}
                  {r.on_clear_list && !onClear && (
                    <span className="shrink-0 inline-flex items-center gap-0.5 text-[10px] px-1 rounded border border-rose-500/40 text-rose-400"
                      title="On the clear list">
                      <ListX className="h-2.5 w-2.5" /> clear list
                    </span>
                  )}
                </span>
                <span className="block truncate text-xs text-muted-foreground">{r.from_address}</span>
              </span>
              {r.content_class && CONTENT_META[r.content_class] && (
                <span className={cn("hidden sm:inline shrink-0 text-[10px] px-1 rounded border", CONTENT_META[r.content_class].cls)}>
                  {CONTENT_META[r.content_class].label}
                </span>
              )}
              {r.unsubscribe_url && (
                <span className="hidden sm:inline shrink-0 text-[10px] px-1 rounded border border-border text-muted-foreground"
                  title={r.one_click ? "One-click unsubscribe available" : "Unsubscribe link available"}>
                  {r.one_click ? "1-click unsub" : "unsub"}
                </span>
              )}
              {r.unread > 0 && (
                <span className="hidden md:inline shrink-0 text-[10px] px-1.5 py-0.5 rounded-full bg-sky-500/15 text-sky-400 border border-sky-500/30">
                  {r.unread} unread
                </span>
              )}
              <span className="shrink-0 text-xs text-muted-foreground tabular-nums w-16 text-right">{r.messages} msgs</span>
              <span className="hidden md:inline shrink-0 text-[11px] text-muted-foreground w-14 text-right">{fmtDate(r.last_at)}</span>
              <span className="flex items-center gap-0.5 shrink-0" onClick={(e) => e.preventDefault()}>
                <Button variant="ghost" size="icon" className="h-7 w-7"
                  title={r.on_clear_list ? "On clear list — click to remove" : "Add to clear list (bulk-trash later)"}
                  onClick={() => clearList(r)}>
                  <ListX className={cn("h-4 w-4", r.on_clear_list && "text-rose-400")} />
                </Button>
                <Button variant="ghost" size="icon" className="h-7 w-7"
                  title={r.kept ? "Kept — click to allow recommending again" : "Keep — never recommend this sender"}
                  onClick={() => keep(r)}>
                  {r.kept ? <PinOff className="h-4 w-4 text-sky-400" /> : <Pin className="h-4 w-4" />}
                </Button>
                {r.unsubscribe_url && (
                  <Button variant="ghost" size="icon" className="h-7 w-7" title="Unsubscribe (then optionally trash)"
                    onClick={() => unsubscribe(r)}>
                    <BellOff className="h-4 w-4" />
                  </Button>
                )}
                <Button variant="ghost" size="icon" className="h-7 w-7" title="Trash all from this sender"
                  onClick={() => bulkTrash([r.from_address], false)}>
                  <Trash2 className="h-4 w-4 text-rose-400/80" />
                </Button>
              </span>
            </label>
          );
        })}
      </div>
      {source && source.length > 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          {onClear
            ? <>The <span className="text-rose-400">clear list</span> — senders you flagged (🗑) while reading. Select all → Trash to empty it.</>
            : <>Showing {visible.length} of {rows?.length ?? 0} senders. <span className="text-amber-400">rec</span> = recommended (automated / has unsubscribe / never replied). <ListX className="inline h-3 w-3" /> adds a sender to the clear list; <Pin className="inline h-3 w-3" /> keeps it out of recommendations.</>}
          {" "}Trash moves mail to Gmail Trash — recoverable for 30 days.
        </p>
      )}
    </div>
  );
}
