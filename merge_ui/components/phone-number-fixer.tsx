"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, ChevronDown, ChevronRight, Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, type UnplacedNumber } from "@/lib/api";
import { toast } from "sonner";

type Fix = { digits: string; action: "set" | "ignore"; international?: string };

const MANUAL = "Needs a country code";
const PREVIEW = 6;

/** Lists phone-book numbers without a country code, grouped by shape. A group
 *  with an unambiguous reading ("Russian numbers written without +") applies in
 *  one click; anything else is typed per number. Every change is one request,
 *  and the rows it covers disappear as soon as it succeeds. */
export function PhoneNumberFixer({ initial }: { initial: UnplacedNumber[] }) {
  const router = useRouter();
  const [rows, setRows] = useState<UnplacedNumber[]>(initial);
  const [busy, setBusy] = useState<string | null>(null);

  const groups = useMemo(() => {
    const byLabel = new Map<string, UnplacedNumber[]>();
    for (const r of rows) {
      const label = r.suggestion?.label ?? MANUAL;
      byLabel.set(label, [...(byLabel.get(label) ?? []), r]);
    }
    // Biggest one-click groups first; numbers needing typing last.
    return [...byLabel.entries()].sort(([a, x], [b, y]) =>
      Number(a === MANUAL) - Number(b === MANUAL) || y.length - x.length);
  }, [rows]);

  async function apply(key: string, fixes: Fix[]) {
    if (fixes.length === 0) return;
    setBusy(key);
    try {
      const r = await api.fixNumbers(fixes);
      const done = new Set(fixes.map((f) => f.digits));
      setRows((cur) => cur.filter((x) => !done.has(x.digits)));
      const parts = [];
      if (r.fixed) parts.push(`fixed ${r.fixed} number${r.fixed === 1 ? "" : "s"}`);
      if (r.ignored) parts.push(`deleted ${r.ignored}`);
      if (r.joined) parts.push(`${r.joined} contact${r.joined === 1 ? "" : "s"} joined an existing person`);
      toast.success(`${parts.join(", ")}. Contacts update within about 5 minutes.`);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't save");
    } finally {
      setBusy(null);
    }
  }

  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
        Every phone-book number has a country code. Nothing to fix.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground tabular">
        {rows.length} number{rows.length === 1 ? "" : "s"} on{" "}
        {new Set(rows.flatMap((r) => r.cards.map((c) => c.uid))).size} contacts
      </p>
      {groups.map(([label, items]) => (
        <Group key={label} label={label} items={items} busy={busy} onApply={apply} />
      ))}
    </div>
  );
}

function Group({ label, items, busy, onApply }: {
  label: string;
  items: UnplacedNumber[];
  busy: string | null;
  onApply: (key: string, fixes: Fix[]) => Promise<void>;
}) {
  const manual = label === MANUAL;
  const [open, setOpen] = useState(manual);
  const [showAll, setShowAll] = useState(false);
  const deletes = items.every((i) => i.suggestion?.action === "ignore");
  const shown = showAll ? items : items.slice(0, PREVIEW);
  const groupKey = `group:${label}`;

  const groupFixes: Fix[] = items.map((i) =>
    i.suggestion?.action === "set"
      ? { digits: i.digits, action: "set", international: `+${i.suggestion.international}` }
      : { digits: i.digits, action: "ignore" });

  return (
    <section className="rounded-lg border border-border bg-card/40">
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        <button type="button" onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 text-sm font-medium text-left flex-1 min-w-0">
          {open ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
          <span className="truncate">{label}</span>
          <span className="text-muted-foreground tabular font-normal">· {items.length}</span>
        </button>
        {!manual && (
          <Button size="sm" variant={deletes ? "outline" : "default"} disabled={busy !== null}
            onClick={() => onApply(groupKey, groupFixes)}>
            {busy === groupKey
              ? <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
              : deletes ? <Trash2 className="h-3.5 w-3.5 mr-1.5" /> : <Check className="h-3.5 w-3.5 mr-1.5" />}
            {deletes ? `Delete all ${items.length}` : `Apply to all ${items.length}`}
          </Button>
        )}
      </div>
      {open && (
        <ul className="divide-y divide-border border-t border-border">
          {shown.map((n) => <NumberRow key={n.digits} n={n} busy={busy} onApply={onApply} />)}
          {items.length > PREVIEW && (
            <li className="px-4 py-2">
              <button type="button" onClick={() => setShowAll((v) => !v)}
                className="text-xs text-muted-foreground hover:text-foreground underline underline-offset-2">
                {showAll ? "Show fewer" : `Show all ${items.length}`}
              </button>
            </li>
          )}
        </ul>
      )}
    </section>
  );
}

function NumberRow({ n, busy, onApply }: {
  n: UnplacedNumber;
  busy: string | null;
  onApply: (key: string, fixes: Fix[]) => Promise<void>;
}) {
  const s = n.suggestion;
  const [typed, setTyped] = useState("+");
  const key = `row:${n.digits}`;
  const names = n.cards.map((c) => c.name || "Unnamed contact").join(", ");

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="text-sm truncate">{names}</div>
        <div className="text-xs text-muted-foreground tabular">
          {n.written}
          {s?.action === "set" && <> → <span className="text-foreground">+{s.international}</span></>}
        </div>
      </div>
      {s?.action === "set" ? (
        <Button size="sm" variant="outline" disabled={busy !== null}
          onClick={() => onApply(key, [{ digits: n.digits, action: "set", international: `+${s.international}` }])}>
          {busy === key ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Apply"}
        </Button>
      ) : !s ? (
        <form className="flex items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            onApply(key, [{ digits: n.digits, action: "set", international: typed }]);
          }}>
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} inputMode="tel"
            aria-label={`Number for ${names} with country code`}
            className="h-8 w-44 text-sm tabular" placeholder="+7 916 123 45 67" />
          <Button size="sm" type="submit" disabled={busy !== null || typed.replace(/\D/g, "").length < 8}>
            {busy === key ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Save"}
          </Button>
        </form>
      ) : null}
      <Button size="sm" variant="ghost" disabled={busy !== null} title="Stop listing and matching this number"
        onClick={() => onApply(`del:${n.digits}`, [{ digits: n.digits, action: "ignore" }])}>
        {busy === `del:${n.digits}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
        <span className="sr-only">Delete</span>
      </Button>
    </li>
  );
}
