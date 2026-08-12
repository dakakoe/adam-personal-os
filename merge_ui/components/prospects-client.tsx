"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Search, X, Send, Target, RefreshCw, Users, Sparkles } from "lucide-react";
import { api, type ProspectRow } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

const DORMANCY = [
  { label: "3+ months", days: 90 },
  { label: "6+ months", days: 182 },
  { label: "1+ year", days: 365 },
  { label: "2+ years", days: 730 },
];
const MIN_MSGS = [
  { label: "20+", n: 20 },
  { label: "50+", n: 50 },
  { label: "200+", n: 200 },
];
// ICP-fit preset lenses — one tap fills the semantic query.
const PRESETS = [
  { label: "Funds & investors", q: "crypto/web3 fund partner, VC, angel or investment decision-maker who allocates capital" },
  { label: "Founders", q: "founder or CEO of a crypto/web3 startup or protocol, building a product" },
  { label: "Exchanges / protocols", q: "business development or leadership at a crypto exchange, L1/L2 protocol, or infrastructure provider" },
  { label: "Connectors", q: "well-connected operator or partner who makes warm introductions across crypto and business networks" },
];

function ago(iso: string | null, days: number | null): string {
  if (days == null || iso == null) return "never";
  if (days < 45) return `${days}d ago`;
  if (days < 365) return `${Math.round(days / 30)}mo ago`;
  const y = (days / 365).toFixed(days < 730 ? 1 : 0);
  return `${y}y ago`;
}

export function ProspectsClient() {
  const [mode, setMode] = useState<"reconnect" | "icp">("reconnect");
  const [rows, setRows] = useState<ProspectRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [dormancy, setDormancy] = useState(90);
  const [minMsgs, setMinMsgs] = useState(20);
  const [q, setQ] = useState("");
  const [qLive, setQLive] = useState("");
  const [includePipeline, setIncludePipeline] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setQLive(q.trim()), 350);
    return () => clearTimeout(t);
  }, [q]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (mode === "icp") {
        if (!qLive) { setRows(null); return; }   // needs a description
        setRows(await api.searchProspects({
          q: qLive, min_interactions: minMsgs, dormant_after_days: dormancy,
          include_pipeline: includePipeline, limit: 100,
        }));
      } else {
        setRows(await api.listProspects({
          min_interactions: minMsgs, dormant_after_days: dormancy,
          include_pipeline: includePipeline, q: qLive || undefined, limit: 100,
        }));
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to load prospects");
      setRows([]);
    } finally { setLoading(false); }
  }, [mode, minMsgs, dormancy, includePipeline, qLive]);

  useEffect(() => { load(); }, [load]);

  async function dismiss(p: ProspectRow) {
    setBusy(p.person_id);
    setRows((cur) => cur?.filter((x) => x.person_id !== p.person_id) ?? cur);
    try {
      await api.dismissProspect(p.person_id);
      toast(`Dismissed ${p.display_name}`, {
        action: { label: "Undo", onClick: async () => { await api.undismissProspect(p.person_id); load(); } },
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Dismiss failed");
      load();
    } finally { setBusy(null); }
  }

  const icp = mode === "icp";

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        <h1 className="text-xl font-semibold inline-flex items-center gap-2 mr-auto">
          <Target className="h-5 w-5 text-primary" /> Prospects
          {loading && <RefreshCw className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        </h1>
        <div className="inline-flex rounded-md border border-border overflow-hidden text-sm">
          <button onClick={() => setMode("reconnect")}
            className={cn("h-8 px-3", !icp ? "bg-primary text-primary-foreground" : "hover:bg-accent")}>Reconnect</button>
          <button onClick={() => setMode("icp")}
            className={cn("h-8 px-3 inline-flex items-center gap-1", icp ? "bg-primary text-primary-foreground" : "hover:bg-accent")}>
            <Sparkles className="h-3.5 w-3.5" /> ICP fit
          </button>
        </div>
      </div>
      <p className="text-sm text-muted-foreground -mt-1">
        {icp
          ? "Describe who you sell to — ranked by how closely a contact's profile matches, within your dormant pool."
          : "Contacts worth reaching back out to — ranked by relationship depth × how long it's gone quiet."}
      </p>

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap text-sm">
        <div className="inline-flex rounded-md border border-border overflow-hidden">
          {DORMANCY.map((d) => (
            <button key={d.days} onClick={() => setDormancy(d.days)}
              className={cn("h-8 px-2.5", dormancy === d.days ? "bg-primary text-primary-foreground" : "hover:bg-accent")}>
              {d.label}
            </button>
          ))}
        </div>
        <div className="inline-flex rounded-md border border-border overflow-hidden">
          {MIN_MSGS.map((m) => (
            <button key={m.n} onClick={() => setMinMsgs(m.n)}
              className={cn("h-8 px-2.5", minMsgs === m.n ? "bg-primary text-primary-foreground" : "hover:bg-accent")}
              title="Minimum past messages">
              {m.label}
            </button>
          ))}
        </div>
        <label className="inline-flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" checked={includePipeline} onChange={(e) => setIncludePipeline(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-border accent-primary" />
          include pipeline
        </label>
        <div className="flex items-center gap-1.5 h-8 px-2 rounded-md border border-border bg-background ml-auto">
          {icp ? <Sparkles className="h-3.5 w-3.5 text-muted-foreground" /> : <Search className="h-3.5 w-3.5 text-muted-foreground" />}
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder={icp ? "Describe your ideal contact…" : "Filter by profile keyword…"}
            className="bg-transparent outline-none text-sm w-72 max-w-[55vw]" />
          {q && <button onClick={() => setQ("")}><X className="h-3.5 w-3.5 text-muted-foreground hover:text-foreground" /></button>}
        </div>
      </div>

      {icp && (
        <div className="flex items-center gap-1.5 flex-wrap -mt-1">
          {PRESETS.map((p) => (
            <button key={p.label} onClick={() => setQ(p.q)}
              className="text-xs h-7 px-2.5 rounded-full border border-dashed border-border text-muted-foreground hover:text-foreground hover:border-foreground/40">
              {p.label}
            </button>
          ))}
        </div>
      )}
      {icp && qLive && (
        <p className="text-[11px] text-muted-foreground -mt-1">
          Semantic match runs over built profiles (~46% of your base, growing) — contacts without a profile yet won't appear here.
        </p>
      )}

      {/* List */}
      {rows === null ? (
        <div className="rounded-lg border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
          {icp ? "Describe who you sell to (or pick a lens above) to rank your dormant contacts by fit." : "Loading…"}
        </div>
      ) : rows.length === 0 ? (
        <div className="rounded-lg border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
          No matches. {icp ? "Try a broader description" : "Loosen the dormancy window or lower the message threshold"}.
        </div>
      ) : (
        <ul className="rounded-lg border border-border overflow-hidden bg-card/40 divide-y divide-border">
          {rows.map((p, i) => (
            <li key={p.person_id} className="flex items-start gap-3 px-3 sm:px-4 py-2.5 hover:bg-accent/20 transition-colors">
              <span className="text-xs text-muted-foreground tabular w-5 shrink-0 pt-0.5">{i + 1}</span>
              <Link href={`/persons/${p.person_id}`} className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="font-medium truncate">{p.display_name}</span>
                  {p.telegram_username && <span className="text-[11px] text-sky-400">@{p.telegram_username}</span>}
                  {icp && p.distance != null && (
                    <span className="text-[10px] text-violet-300 border border-violet-500/40 rounded px-1" title="ICP match">
                      {Math.max(0, Math.round((1 - p.distance) * 100))}% fit
                    </span>
                  )}
                  {p.in_pipeline && <span className="text-[10px] uppercase tracking-wide text-emerald-400 border border-emerald-500/40 rounded px-1">in pipeline</span>}
                </div>
                <div className="text-xs text-muted-foreground flex flex-wrap items-center gap-x-2.5 gap-y-0.5 mt-0.5">
                  <span className="inline-flex items-center gap-1"><Users className="h-3 w-3" />{p.total_interactions.toLocaleString()} msgs</span>
                  <span>· last {ago(p.last_interaction_at, p.days_since)}</span>
                  {p.email && <span className="truncate">· {p.email}</span>}
                </div>
                {p.summary && <p className="text-xs text-muted-foreground/90 mt-1 line-clamp-2">{p.summary}</p>}
              </Link>
              <div className="flex items-center gap-1 shrink-0">
                <Link href={`/persons/${p.person_id}`} title="Open — draft a reconnect message"
                  className="inline-flex items-center gap-1 h-7 px-2 rounded-md border border-border text-xs hover:bg-accent">
                  <Send className="h-3 w-3" /> Draft
                </Link>
                <button type="button" onClick={() => dismiss(p)} disabled={busy === p.person_id}
                  title="Not a prospect — hide (family, personal, dead lead)"
                  className="inline-flex items-center h-7 w-7 justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground">
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
