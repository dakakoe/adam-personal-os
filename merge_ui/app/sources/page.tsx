import { cookies } from "next/headers";
import { Plug, CheckCircle2, XCircle, MinusCircle, AlertTriangle, RefreshCw, Archive } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type SourceStatus } from "@/lib/api";
import { SyncButton } from "./sync-button";
import { RetireAccountButton } from "@/components/retire-account-button";

async function fetchSources(): Promise<SourceStatus[] | null> {
  const cookie = (await cookies()).toString();
  try {
    return (await api.getSources({ cookieHeader: cookie })).sources;
  } catch {
    return null;
  }
}

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default async function SourcesPage({
  searchParams,
}: {
  searchParams: Promise<{ reconnected?: string; reconnect_error?: string }>;
}) {
  const [sources, sp] = await Promise.all([fetchSources(), searchParams]);

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-3xl">
        <header className="mb-4 sm:mb-6">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Plug className="h-5 w-5 text-muted-foreground" />
            Sources
          </h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-1">
            Where your data comes from — item counts, last sync, and the health of each connector&apos;s worker.
            {" "}<a href="/setup" className="underline">Setup wizard →</a>
          </p>
        </header>

        {sp.reconnected && (
          <div className="mb-4 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-400">
            Reconnected <span className="font-medium">{sp.reconnected}</span>. Syncing will resume on the next run.
          </div>
        )}
        {sp.reconnect_error && (
          <div className="mb-4 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-400">
            Reconnect failed ({sp.reconnect_error}). Please try again.
          </div>
        )}

        {!sources ? (
          <div className="rounded-md border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
            Couldn&apos;t load sources.
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {sources.map((s) => (
              <div key={s.key} className="rounded-lg border border-border bg-card/40 p-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{s.label}</span>
                  {/* Needs-reconnect is the actionable state, so it wins over
                      worker health (the worker can be 'up' yet have a dead token). */}
                  {s.needs_reconnect ? (
                    <AlertTriangle className="h-4 w-4 text-amber-400" />
                  ) : s.unit_ok === null ? (
                    <MinusCircle className="h-4 w-4 text-muted-foreground/50" />
                  ) : s.unit_ok ? (
                    <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                  ) : (
                    <XCircle className="h-4 w-4 text-rose-400" />
                  )}
                </div>
                <div className="mt-2 text-2xl font-semibold tabular-nums">
                  {s.count === null ? "—" : s.count.toLocaleString()}
                  <span className="ml-1 text-xs font-normal text-muted-foreground">{s.noun}</span>
                </div>
                <div className="mt-1 flex items-center justify-between text-xs text-muted-foreground">
                  <span>last sync {timeAgo(s.last_sync)}</span>
                  <span
                    className={
                      s.needs_reconnect
                        ? "text-amber-400"
                        : s.unit_ok === false
                          ? "text-rose-400"
                          : ""
                    }
                  >
                    {s.needs_reconnect
                      ? "reconnect needed"
                      : s.unit_ok === false
                        ? s.unit_state ?? "error"
                        : s.kind === "always_on"
                          ? "live"
                          : "scheduled"}
                  </span>
                </div>

                {s.needs_reconnect && (
                  <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-xs">
                    {s.reconnect_reason ? (
                      // Non-OAuth source (Telegram/Granola): recovery is manual —
                      // the worker's reason carries the instruction.
                      <p className="text-muted-foreground">{s.reconnect_reason}</p>
                    ) : (
                      <>
                    <p className="text-muted-foreground">
                      The OAuth token was revoked or expired, so syncing is paused.
                    </p>
                    {s.reconnect_url ? (
                      <>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {(s.reconnect_accounts.length
                            ? s.reconnect_accounts
                            : [""]
                          ).map((email) => (
                            <a
                              key={email}
                              href={
                                email
                                  ? `${s.reconnect_url}?account=${encodeURIComponent(email)}`
                                  : s.reconnect_url!
                              }
                              className="inline-flex items-center gap-1.5 rounded-md bg-amber-500/90 px-2.5 py-1 font-medium text-background hover:bg-amber-400 transition-colors"
                            >
                              <RefreshCw className="h-3.5 w-3.5" />
                              {email ? `Reconnect ${email}` : "Reconnect with Google"}
                            </a>
                          ))}
                        </div>
                        <p className="mt-2 text-muted-foreground">
                          Opens Google&apos;s consent screen; syncing resumes automatically after.
                        </p>
                      </>
                    ) : (
                      <details className="mt-1">
                        <summary className="flex cursor-pointer items-center gap-1.5 font-medium text-amber-400">
                          <AlertTriangle className="h-3.5 w-3.5" />
                          How to reconnect
                        </summary>
                        <div className="mt-2 space-y-2 text-muted-foreground">
                          <p>
                            Re-consent on a machine with a browser (not the droplet), then paste the
                            printed one-liner:
                          </p>
                          <code className="block overflow-x-auto rounded bg-background/60 p-2 font-mono text-[11px] leading-relaxed">
                            python -m gmail.oauth --credentials credentials.json \<br />
                            &nbsp;&nbsp;--account you@gmail.com --scopes gmail,contacts,calendar
                          </code>
                          <p>Syncing resumes automatically once the account is re-authorized.</p>
                        </div>
                      </details>
                    )}
                      </>
                    )}
                  </div>
                )}

                {/* Always-available re-consent for healthy OAuth accounts — e.g.
                    to grant a newly-added scope without waiting for a token to
                    break. Hidden while needs_reconnect (the amber block wins). */}
                {!s.needs_reconnect && s.reconnect_url && s.oauth_accounts.length > 0 && (
                  <details className="mt-3 text-xs">
                    <summary className="flex cursor-pointer items-center gap-1.5 text-muted-foreground hover:text-foreground">
                      <RefreshCw className="h-3.5 w-3.5" /> Re-consent an account
                    </summary>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {s.oauth_accounts.map((email) => (
                        <span key={email} className="inline-flex items-center gap-1">
                          <a
                            href={`${s.reconnect_url}?account=${encodeURIComponent(email)}`}
                            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 hover:bg-muted transition-colors"
                          >
                            <RefreshCw className="h-3 w-3 text-muted-foreground" />
                            {email}
                          </a>
                          <RetireAccountButton email={email} />
                        </span>
                      ))}
                    </div>
                    <p className="mt-2 text-muted-foreground">
                      Opens Google&apos;s consent screen to refresh this account&apos;s grant (and any new scopes).
                      <br />
                      <span className="text-muted-foreground/80">
                        Mailbox shut down for good? <span className="text-foreground">Retire</span> it — its mail stays
                        in your archive and searchable, it just stops syncing (and stops failing).
                      </span>
                    </p>
                  </details>
                )}

                {/* Retired = sunsetted mailbox kept as a read-only archive. */}
                {(s.retired_accounts?.length ?? 0) > 0 && (
                  <div className="mt-3 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs">
                    <div className="flex items-center gap-1.5 text-muted-foreground">
                      <Archive className="h-3.5 w-3.5" /> Archived (read-only — not synced)
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      {s.retired_accounts!.map((email) => (
                        <span key={email} className="inline-flex items-center gap-1">
                          <span className="rounded-md border border-border bg-background px-2.5 py-1 text-muted-foreground">
                            {email}
                          </span>
                          <RetireAccountButton email={email} retired />
                        </span>
                      ))}
                    </div>
                    <p className="mt-2 text-muted-foreground">
                      Everything these accounts synced is kept and stays searchable in Mail.
                    </p>
                  </div>
                )}

                {s.kind === "oneshot" && (
                  <div className="mt-3 flex justify-end">
                    <SyncButton sourceKey={s.key} />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
