"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, type StreamRow } from "@/lib/api";
import { toast } from "sonner";

/** Self-serve stream editor: rename inline (cascades to every deal carrying
 *  the tag), add, and retire. Mirrors the stage manager, minus ordering —
 *  the filter bar lists streams alphabetically. */
export function StreamsManager({ initial }: { initial: StreamRow[] }) {
  const router = useRouter();
  const [streams, setStreams] = useState<StreamRow[]>(initial);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);

  function applied(next: StreamRow[]) {
    setStreams([...next].sort((a, b) => a.name.localeCompare(b.name)));
    router.refresh();
  }

  async function mutate(fn: () => Promise<void>) {
    setBusy(true);
    try { await fn(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Update failed"); }
    finally { setBusy(false); }
  }

  // `el` is the field itself: it holds a defaultValue, so a cancelled or
  // failed rename has to be put back by hand or it keeps showing text that
  // was never saved.
  const rename = (s: StreamRow, el: HTMLInputElement) => {
    const next = el.value.trim().toLowerCase();
    if (!next || next === s.name) {
      el.value = s.name;
      return;
    }
    const merging = streams.some((x) => x.name === next);
    if (merging && !confirm(
      `"${next}" already exists — merge "${s.name}" into it? ` +
      `Deals tagged "${s.name}" will carry "${next}" instead.`
    )) {
      el.value = s.name;
      return;
    }
    void mutate(async () => {
      try {
        const row = await api.renameStream(s.name, next);
        applied([...streams.filter((x) => x.name !== s.name && x.name !== next), row]);
        toast.success(merging ? `Merged into "${row.name}"` : `Renamed to "${row.name}"`);
      } catch (e) {
        el.value = s.name;
        throw e;
      }
    });
  };

  const remove = (s: StreamRow) => {
    // Deleting a stream in use strips the tag off those deals, so the count
    // goes in the prompt rather than blocking the action outright.
    const msg = s.in_use > 0
      ? `Delete stream "${s.name}" and remove it from ${s.in_use} deal${s.in_use === 1 ? "" : "s"}?`
      : `Delete stream "${s.name}"?`;
    if (!confirm(msg)) return;
    void mutate(async () => {
      await api.deleteStream(s.name, { force: s.in_use > 0 });
      applied(streams.filter((x) => x.name !== s.name));
      toast.success("Stream deleted");
    });
  };

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    const name = newName.trim().toLowerCase();
    if (!name) return;
    if (streams.some((x) => x.name === name)) {
      toast.error(`"${name}" already exists`);
      return;
    }
    void mutate(async () => {
      const row = await api.createStream(name);
      applied([...streams, row]);
      setNewName("");
      toast.success(`Stream "${row.name}" added`);
    });
  };

  return (
    <div className="space-y-4 max-w-2xl">
      <Link href="/opportunities" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" /> Back to opportunities
      </Link>

      {streams.length === 0 ? (
        <p className="rounded-md border border-border bg-card/40 px-3 py-6 text-center text-sm text-muted-foreground">
          No streams yet — add the first one below.
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border bg-card/40">
          {streams.map((s) => (
            <li key={s.name} className="flex items-center gap-2 px-3 py-2">
              <span className="inline-flex items-center h-7 px-2.5 rounded-full border border-border text-xs text-muted-foreground shrink-0">
                {s.name}
              </span>

              <Input defaultValue={s.name} disabled={busy}
                onBlur={(e) => rename(s, e.target)}
                onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                className="h-7 text-sm flex-1 min-w-[8rem]" />

              <span className="text-[11px] text-muted-foreground tabular w-14 text-right shrink-0">
                {s.in_use} deal{s.in_use === 1 ? "" : "s"}
              </span>

              <button disabled={busy} onClick={() => remove(s)} title="Delete stream"
                className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition-colors disabled:opacity-25 shrink-0">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={add} className="flex items-center gap-2">
        <Input value={newName} onChange={(e) => setNewName(e.target.value)}
          placeholder="New stream name…" className="h-8 text-sm flex-1" disabled={busy} />
        <Button type="submit" size="sm" disabled={busy || !newName.trim()}>
          <Plus className="h-3.5 w-3.5 mr-1" /> Add stream
        </Button>
      </form>

      <p className="text-xs text-muted-foreground">
        Streams are stored lower-case, so names are normalised as you type them. Renaming
        one onto an existing stream merges the two — the way to clean up
        &apos;job&apos; vs &apos;jobs&apos;. Deleting removes the tag from its deals; the deals
        themselves are untouched.
      </p>
    </div>
  );
}
