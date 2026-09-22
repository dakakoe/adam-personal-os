import { cookies } from "next/headers";
import { Activity, CheckCircle2, XCircle, MinusCircle, Database, Sparkles } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type SystemHealth } from "@/lib/api";

async function fetchHealth(): Promise<SystemHealth | null> {
  const cookie = (await cookies()).toString();
  try {
    return await api.getSystemHealth({ cookieHeader: cookie });
  } catch {
    return null;
  }
}

function StatusDot({ ok }: { ok: boolean | null }) {
  if (ok === null) return <MinusCircle className="h-4 w-4 text-muted-foreground/50" />;
  return ok
    ? <CheckCircle2 className="h-4 w-4 text-emerald-400" />
    : <XCircle className="h-4 w-4 text-rose-400" />;
}

export default async function HealthPage() {
  const h = await fetchHealth();

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-3xl">
        <header className="mb-4 sm:mb-6">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Activity className="h-5 w-5 text-muted-foreground" />
            System health
          </h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-1">
            Last run &amp; exit status of every worker — so a stalled scanner, a failed backup, or an out-of-credits run shows up here instead of going unnoticed.
          </p>
        </header>

        {!h ? (
          <div className="rounded-md border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
            Couldn&apos;t load health.
          </div>
        ) : (
          <>
            <div className={`mb-4 rounded-lg border p-3 sm:p-4 flex items-center gap-3 ${h.ok ? "border-emerald-500/30 bg-emerald-500/5" : "border-rose-500/40 bg-rose-500/5"}`}>
              {h.ok ? <CheckCircle2 className="h-5 w-5 text-emerald-400" /> : <XCircle className="h-5 w-5 text-rose-400" />}
              <div className="text-sm">
                <span className="font-medium">{h.ok ? "All systems healthy" : "Something needs attention"}</span>
                <span className="text-muted-foreground"> — review the workers below.</span>
              </div>
            </div>

            <div className="flex flex-wrap gap-2 mb-4 text-xs">
              <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card/40 px-2 py-1">
                <Database className="h-3.5 w-3.5" /> Database
                {h.db_ok ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> : <XCircle className="h-3.5 w-3.5 text-rose-400" />}
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card/40 px-2 py-1">
                <Sparkles className="h-3.5 w-3.5" /> Anthropic key
                {h.anthropic_configured ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> : <XCircle className="h-3.5 w-3.5 text-rose-400" />}
              </span>
            </div>

            <ul className="divide-y divide-border rounded-md border border-border overflow-hidden bg-card/40">
              {h.units.map((u) => (
                <li key={u.unit} className="flex items-center gap-3 px-3 sm:px-4 py-2.5">
                  <StatusDot ok={u.ok} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <span className="font-medium">{u.label}</span>
                      <span className="text-[10px] font-mono uppercase text-muted-foreground/60">{u.kind === "always_on" ? "service" : "scheduled"}</span>
                      {u.ok === false && u.exit_status && u.exit_status !== "0" && (
                        <span className="text-[10px] font-mono text-rose-400">exit {u.exit_status}</span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground tabular">
                      {u.kind === "always_on"
                        ? <>state: {u.active_state ?? "?"}</>
                        : <>last run: {u.last_run ?? "never"}{u.ok === false ? ` · ${u.result ?? "failed"}` : ""}</>}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
            <p className="text-[11px] text-muted-foreground/60 mt-3">
              Scheduled workers show their most recent run; a red mark means that run exited non-zero (check its log under /srv/memory/logs).
            </p>
          </>
        )}
      </div>
    </AppShell>
  );
}
