"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Upload, FileText, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, type FinAccount, type FinImportBatch } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

export function ImportClient({ accounts, batches }: { accounts: FinAccount[]; batches: FinImportBatch[] }) {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadAcct, setUploadAcct] = useState("");

  const pending = batches.filter((b) => b.status === "pending");

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await api.finance.uploadStatement(file, uploadAcct || undefined);
      toast.success("Statement parsed — review below");
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Upload failed"); }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = ""; }
  }

  return (
    <div className="space-y-5">
      {/* Bank statement upload */}
      <div className="rounded-lg border border-border bg-card/40 p-4">
        <div className="text-sm font-medium mb-1">Import a bank statement</div>
        <p className="text-xs text-muted-foreground mb-3">Upload a PDF or CSV (any Thai/RU/EN bank). Rows are parsed for review before they post to the account you choose.</p>
        <div className="flex items-center gap-2">
          <select value={uploadAcct} onChange={(e) => setUploadAcct(e.target.value)}
            className="h-8 text-xs px-2 rounded-md border border-border bg-background text-muted-foreground">
            <option value="">Account…</option>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <input ref={fileRef} type="file" accept=".pdf,.csv" onChange={onUpload} className="hidden" />
          <Button size="sm" onClick={() => fileRef.current?.click()} disabled={uploading}>
            <Upload className="h-3.5 w-3.5 mr-1.5" />{uploading ? "Parsing…" : "Upload PDF / CSV"}
          </Button>
        </div>
      </div>

      {/* Pending review batches */}
      {pending.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Review ({pending.length})</div>
          <div className="space-y-3">
            {pending.map((b) => <BatchReview key={b.id} batch={b} accounts={accounts} />)}
          </div>
        </div>
      )}

      {/* History */}
      {batches.some((b) => b.status !== "pending") && (
        <div>
          <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Recent imports</div>
          <ul className="rounded-md border border-border bg-card/40 divide-y divide-border text-sm">
            {batches.filter((b) => b.status !== "pending").slice(0, 10).map((b) => (
              <li key={b.id} className="flex items-center gap-2 px-3 py-2 text-muted-foreground">
                <FileText className="h-3.5 w-3.5" />
                <span className="flex-1 truncate">{b.filename || b.kind} · {b.row_count} rows</span>
                <span className={cn("text-[11px]", b.status === "confirmed" ? "text-emerald-400" : "text-muted-foreground/60")}>{b.status}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function BatchReview({ batch, accounts }: { batch: FinImportBatch; accounts: FinAccount[] }) {
  const router = useRouter();
  const rows = batch.parsed?.rows ?? [];
  const [acct, setAcct] = useState(batch.account_id ?? "");
  const [skip, setSkip] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);

  function toggle(i: number) {
    setSkip((prev) => { const n = new Set(prev); n.has(i) ? n.delete(i) : n.add(i); return n; });
  }

  async function confirm() {
    if (!acct) { toast.error("Pick an account to import into"); return; }
    setBusy(true);
    try {
      const r = await api.finance.confirmImport(batch.id, { account_id: acct, skip_indices: [...skip] });
      toast.success(r.skipped > 0
        ? `Imported ${r.created} · skipped ${r.skipped} duplicate${r.skipped === 1 ? "" : "s"}`
        : `Imported ${r.created} transactions`);
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Import failed"); }
    finally { setBusy(false); }
  }

  async function discard() {
    setBusy(true);
    try { await api.finance.discardImport(batch.id); toast.success("Discarded"); router.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
    finally { setBusy(false); }
  }

  return (
    <div className="rounded-lg border border-border bg-card/40 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-card/60">
        <FileText className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-sm font-medium flex-1 truncate">{batch.filename || batch.kind}</span>
        <span className="text-[11px] text-muted-foreground">{rows.length - skip.size}/{rows.length} rows</span>
        <select value={acct} onChange={(e) => setAcct(e.target.value)}
          className="h-7 text-xs px-2 rounded-md border border-border bg-background">
          <option value="">Account…</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <Button size="sm" onClick={confirm} disabled={busy}><Check className="h-3.5 w-3.5 mr-1" />Import</Button>
        <Button size="sm" variant="outline" onClick={discard} disabled={busy}><X className="h-3.5 w-3.5" /></Button>
      </div>
      <div className="max-h-72 overflow-y-auto">
        <table className="w-full text-xs">
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={cn("border-b border-border/50", skip.has(i) && "opacity-40 line-through")}>
                <td className="px-2 py-1.5 w-8">
                  <input type="checkbox" checked={!skip.has(i)} onChange={() => toggle(i)}
                    className="h-3.5 w-3.5 rounded border-border accent-emerald-500" />
                </td>
                <td className="px-2 py-1.5 tabular text-muted-foreground whitespace-nowrap">{fmtDate(r.date)}</td>
                <td className="px-2 py-1.5 truncate max-w-[16rem]">{r.description}</td>
                <td className={cn("px-2 py-1.5 tabular text-right whitespace-nowrap",
                  r.direction === "debit" ? "text-rose-400" : "text-emerald-400")}>
                  {r.direction === "debit" ? "-" : "+"}{Math.abs(r.amount).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
