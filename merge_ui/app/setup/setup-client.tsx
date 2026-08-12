"use client";

// Setup wizard stepper. The STEPS registry is the extension point: a future
// connector = one entry here (+ a SOURCES row in merge_api and, if needed, its
// own endpoints). Step "connected" state is DERIVED from GET /api/sources on a
// poll — no persisted wizard state, refresh-safe by construction.

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  CheckCircle2, ChevronLeft, ChevronRight, CircleDashed, ExternalLink,
  KeyRound, Loader2, Send, Upload,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, type SourceStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

type StepProps = {
  sources: SourceStatus[] | null;
  refresh: () => void;
};

type Step = {
  key: string;
  title: string;
  blurb: string;
  /** /api/sources key whose liveness marks this step connected (null = session-only). */
  sourceKey: string | null;
  Body: (props: StepProps) => ReactNode;
};

function src(sources: SourceStatus[] | null, key: string): SourceStatus | undefined {
  return sources?.find((s) => s.key === key);
}

function stepConnected(step: Step, sources: SourceStatus[] | null): boolean | null {
  if (!step.sourceKey) return null;                      // no live signal (linkedin/crypto/finish)
  const s = src(sources, step.sourceKey);
  if (!s) return null;
  if (step.sourceKey === "telegram") return s.unit_ok === true && !s.needs_reconnect;
  return (s.count ?? 0) > 0 && !s.needs_reconnect;
}

// ---------------------------------------------------------------- steps ----

function GoogleStep({ sources }: StepProps) {
  const gmail = src(sources, "gmail");
  const accounts = gmail?.oauth_accounts ?? [];
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Connects Gmail, Google Calendar, and Contacts. You&apos;ll pick the account on
        Google&apos;s consent screen; add as many accounts as you like — each lands here
        with read scopes and starts syncing on the next run.
      </p>
      {accounts.length > 0 && (
        <ul className="text-sm space-y-1">
          {accounts.map((a) => (
            <li key={a} className="flex items-center gap-2">
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> {a}
            </li>
          ))}
        </ul>
      )}
      <Button asChild size="sm">
        {/* full navigation on purpose — the OAuth dance is a top-level redirect;
            the callback lands on /sources, which links back here */}
        <a href="/api/sources/oauth/start?new=1">
          <ExternalLink className="h-3.5 w-3.5 mr-1.5" />
          Connect a Google account
        </a>
      </Button>
      {(gmail?.count ?? 0) > 0 && (
        <p className="text-xs text-muted-foreground">
          {gmail!.count!.toLocaleString()} emails ingested so far.
        </p>
      )}
    </div>
  );
}

function TelegramStep({ sources, refresh }: StepProps) {
  const tg = src(sources, "telegram");
  const [phase, setPhase] = useState<"idle" | "code_sent" | "need_2fa">("idle");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [hash, setHash] = useState("");
  const [busy, setBusy] = useState(false);
  const [floodUntil, setFloodUntil] = useState<number | null>(null);
  const [floodLeft, setFloodLeft] = useState(0);

  useEffect(() => {
    if (!floodUntil) return;
    const t = setInterval(() => {
      const left = Math.max(0, Math.ceil((floodUntil - Date.now()) / 1000));
      setFloodLeft(left);
      if (left === 0) setFloodUntil(null);
    }, 1000);
    return () => clearInterval(t);
  }, [floodUntil]);

  async function sendCode() {
    setBusy(true);
    try {
      const r = await api.setupTelegramSendCode(phone.trim() || undefined);
      if (r.already_authorized) {
        toast.success("Telegram is already connected");
        refresh();
      } else if (r.phone_code_hash) {
        setHash(r.phone_code_hash);
        setPhase("code_sent");
        toast.success("Code sent — check your Telegram app (or SMS)");
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed";
      const m = msg.match(/retry in (\d+)s/);
      if (m) { setFloodUntil(Date.now() + parseInt(m[1], 10) * 1000); }
      toast.error(msg);
    }
    setBusy(false);
  }

  async function signIn() {
    setBusy(true);
    try {
      const r = await api.setupTelegramSignIn({
        code: code.trim(), phone_code_hash: hash,
        phone: phone.trim() || undefined,
        password: password || undefined,
      });
      if (r.need_2fa) {
        setPhase("need_2fa");
        toast("Two-factor password needed", { description: "Enter your Telegram cloud password." });
      } else if (r.ok) {
        toast.success(`Signed in${r.user ? ` — ${r.user}` : ""}. Listener restarted.`);
        setPhase("idle"); setCode(""); setPassword("");
        refresh();
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Sign-in failed");
    }
    setBusy(false);
  }

  async function cancel() {
    setBusy(true);
    try {
      await api.setupTelegramCancel();
      setPhase("idle"); setCode(""); setPassword("");
      toast("Cancelled — listener restarted");
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
    setBusy(false);
  }

  const connected = tg?.unit_ok === true && !tg?.needs_reconnect && (tg?.count ?? 0) > 0;
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Signs your Telegram account in on the server (Telethon) so private chats are
        ingested live. The listener is paused during sign-in and restarted after.
        Group chats are opt-in later on the <a href="/groups" className="underline">Groups</a> page.
      </p>
      {connected && (
        <p className="text-sm flex items-center gap-2">
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
          Connected — {tg!.count!.toLocaleString()} messages ingested.
        </p>
      )}
      {phase === "idle" && (
        <div className="flex items-center gap-2">
          <Input value={phone} onChange={(e) => setPhone(e.target.value)}
            placeholder="+66… (blank = server's TELEGRAM_PHONE)" className="h-9 max-w-xs" />
          <Button size="sm" onClick={sendCode} disabled={busy || floodUntil !== null}>
            {busy ? <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" /> : <Send className="h-3.5 w-3.5 mr-1.5" />}
            {floodUntil ? `Flood-wait ${floodLeft}s` : "Send code"}
          </Button>
        </div>
      )}
      {(phase === "code_sent" || phase === "need_2fa") && (
        <div className="space-y-2 max-w-xs">
          <Input value={code} onChange={(e) => setCode(e.target.value)}
            placeholder="Login code" className="h-9" autoFocus={phase === "code_sent"} />
          {phase === "need_2fa" && (
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder="Two-factor cloud password" className="h-9" autoFocus />
          )}
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={signIn}
              disabled={busy || !code.trim() || (phase === "need_2fa" && !password)}>
              {busy && <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />} Sign in
            </Button>
            <Button size="sm" variant="ghost" onClick={cancel} disabled={busy}>Cancel</Button>
          </div>
          <p className="text-[11px] text-muted-foreground">
            The listener stays paused until you sign in or cancel.
          </p>
        </div>
      )}
    </div>
  );
}

function GranolaStep({ sources, refresh }: StepProps) {
  const g = src(sources, "granola");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [configured, setConfigured] = useState<{ configured: boolean; updated_at: string | null } | null>(null);

  useEffect(() => {
    api.getSecretStatus("granola").then(setConfigured).catch(() => {});
  }, []);

  async function save() {
    setBusy(true);
    try {
      await api.putSecret("granola", key.trim());
      setKey("");
      setConfigured({ configured: true, updated_at: new Date().toISOString() });
      toast.success("Key saved — starting a sync now");
      await api.syncSource("granola").catch(() => {});
      setTimeout(refresh, 4000);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
    setBusy(false);
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Meeting recaps from Granola. Create an API key in Granola under
        Settings → Connectors → API keys (starts with <code className="text-xs">grn_</code>)
        and paste it here — it&apos;s stored server-side and never shown again.
      </p>
      {configured?.configured && (
        <p className="text-sm flex items-center gap-2">
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
          Key configured{configured.updated_at ? ` (${new Date(configured.updated_at).toLocaleDateString()})` : ""}
          {(g?.count ?? 0) > 0 && ` — ${g!.count!.toLocaleString()} recaps ingested`}
        </p>
      )}
      <div className="flex items-center gap-2 max-w-md">
        <Input type="password" value={key} onChange={(e) => setKey(e.target.value)}
          placeholder="grn_…" className="h-9" />
        <Button size="sm" onClick={save} disabled={busy || !key.trim()}>
          {busy ? <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5 mr-1.5" />}
          Save key
        </Button>
      </div>
    </div>
  );
}

function LinkedinStep(_: StepProps) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  async function upload() {
    const f = fileRef.current?.files?.[0];
    if (!f) { toast.error("Pick your LinkedIn export first"); return; }
    setBusy(true);
    try {
      const r = await api.uploadLinkedinExport(f);
      const c = r.counts;
      const text = c
        ? `Imported: ${c.connections ?? 0} connections, ${c.messages ?? 0} messages, ${c.imported ?? 0} imported contacts`
        : "Import finished (no summary line — check the log)";
      setResult(text);
      toast.success(text);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Upload failed"); }
    setBusy(false);
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        One-off import of your LinkedIn data export. Request it at LinkedIn →
        Settings → Data privacy → &quot;Get a copy of your data&quot; (connections at
        minimum), then upload the ZIP here — a bare <code className="text-xs">Connections.csv</code> works too.
      </p>
      <div className="flex items-center gap-2">
        <input ref={fileRef} type="file" accept=".zip,.csv"
          className="text-sm file:mr-2 file:rounded-md file:border file:border-border file:bg-secondary/40 file:px-2.5 file:py-1 file:text-xs file:text-foreground text-muted-foreground" />
        <Button size="sm" onClick={upload} disabled={busy}>
          {busy ? <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" /> : <Upload className="h-3.5 w-3.5 mr-1.5" />}
          Upload &amp; import
        </Button>
      </div>
      {result && (
        <p className="text-sm flex items-center gap-2">
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> {result}
        </p>
      )}
    </div>
  );
}

function CryptoStep({ sources }: StepProps) {
  const c = src(sources, "crypto");
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        On-chain balances for the budget module. Wallet addresses are configured
        server-side for now (env vars on the droplet — see
        <code className="text-xs mx-1">deploy</code> docs); an in-app editor is a later
        connector. Skip this unless you track crypto.
      </p>
      {(c?.count ?? 0) > 0 && (
        <p className="text-sm flex items-center gap-2">
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
          {c!.count!.toLocaleString()} holdings tracked.
        </p>
      )}
    </div>
  );
}

function FinishStep({ sources }: StepProps) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Live status of everything. Counts grow as the workers run (Gmail and
        Calendar sync on timers — first data can take a few minutes).
      </p>
      <div className="grid sm:grid-cols-2 gap-2">
        {(sources ?? []).map((s) => (
          <div key={s.key} className="rounded-md border border-border bg-card/40 px-3 py-2 flex items-center justify-between">
            <span className="text-sm">{s.label}</span>
            <span className="text-xs text-muted-foreground">
              {s.count != null ? `${s.count.toLocaleString()} ${s.noun}` : "no data yet"}
            </span>
          </div>
        ))}
      </div>
      <p className="text-sm text-muted-foreground">
        Next stops: <a href="/groups" className="underline">Groups</a> to pick which Telegram
        groups to follow, and <a href="/sources" className="underline">Sources</a> for ongoing health.
      </p>
    </div>
  );
}

// Registry — a future connector is one entry here.
const STEPS: Step[] = [
  { key: "google", title: "Google", blurb: "Gmail · Calendar · Contacts", sourceKey: "gmail", Body: GoogleStep },
  { key: "telegram", title: "Telegram", blurb: "Live chat ingestion", sourceKey: "telegram", Body: TelegramStep },
  { key: "granola", title: "Granola", blurb: "Meeting recaps", sourceKey: "granola", Body: GranolaStep },
  { key: "linkedin", title: "LinkedIn", blurb: "One-off contact import", sourceKey: null, Body: LinkedinStep },
  { key: "crypto", title: "Crypto", blurb: "On-chain balances", sourceKey: "crypto", Body: CryptoStep },
  { key: "finish", title: "Done", blurb: "Live status", sourceKey: null, Body: FinishStep },
];

// ---------------------------------------------------------------- shell ----

export function SetupClient() {
  const [idx, setIdx] = useState(0);
  const [sources, setSources] = useState<SourceStatus[] | null>(null);

  const refresh = useCallback(() => {
    api.getSources().then((r) => setSources(r.sources)).catch(() => {});
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const step = STEPS[idx];
  return (
    <div className="space-y-4">
      {/* progress rail */}
      <ol className="flex flex-wrap items-center gap-1.5">
        {STEPS.map((s, i) => {
          const connected = stepConnected(s, sources);
          return (
            <li key={s.key}>
              <button type="button" onClick={() => setIdx(i)}
                className={cn("flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
                  i === idx ? "border-primary/50 bg-primary/10 text-foreground"
                    : "border-border text-muted-foreground hover:bg-accent/40")}>
                {connected === true
                  ? <CheckCircle2 className="h-3 w-3 text-emerald-400" />
                  : <CircleDashed className="h-3 w-3 opacity-60" />}
                {s.title}
              </button>
            </li>
          );
        })}
      </ol>

      {/* step card */}
      <div className="rounded-lg border border-border bg-card/40 p-4 sm:p-5">
        <div className="mb-3">
          <h2 className="font-semibold">{step.title}</h2>
          <p className="text-xs text-muted-foreground">{step.blurb}</p>
        </div>
        <step.Body sources={sources} refresh={refresh} />
      </div>

      {/* nav */}
      <div className="flex items-center justify-between">
        <Button size="sm" variant="ghost" disabled={idx === 0} onClick={() => setIdx(idx - 1)}>
          <ChevronLeft className="h-3.5 w-3.5 mr-1" /> Back
        </Button>
        {idx < STEPS.length - 1 ? (
          <Button size="sm" onClick={() => setIdx(idx + 1)}>
            {stepConnected(step, sources) === true ? "Next" : "Skip for now"}
            <ChevronRight className="h-3.5 w-3.5 ml-1" />
          </Button>
        ) : (
          <Button size="sm" asChild><a href="/today">Go to Today →</a></Button>
        )}
      </div>
    </div>
  );
}
