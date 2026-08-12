"use client";

// Owner approvals queue (sharing PR3). A member's edit/delete of a SHARED
// transaction lands here as a pending request; the owner approves (the stored
// change then applies) or rejects. Transactions-only first cut.

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, X, ShieldCheck, Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { fmtMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import { api, type FinApproval } from "@/lib/api";
import { toast } from "sonner";

const usd = (n: number | null | undefined) => (n == null ? "—" : fmtMoney(n, "USD"));

function changeSummary(a: FinApproval): string {
  if (a.action === "delete_txn") return "Delete this transaction";
  const keys = Object.keys(a.payload || {});
  if (keys.length === 0) return "Edit transaction";
  // human-ish list of fields being changed
  const label: Record<string, string> = {
    txn_date: "date", outflow_amount: "amount", inflow_amount: "amount",
    category_key: "category", payee_text: "payee", note: "note",
    outflow_account_id: "account", inflow_account_id: "account",
  };
  const fields = Array.from(new Set(keys.map((k) => label[k] ?? k)));
  return `Change ${fields.join(", ")}`;
}

export function ApprovalsClient({ initial }: { initial: FinApproval[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const pending = initial.filter((a) => a.status === "pending");
  const decided = initial.filter((a) => a.status !== "pending");

  async function decide(a: FinApproval, approve: boolean) {
    if (!approve && !confirm("Reject this request?")) return;
    setBusy(a.id);
    try {
      if (approve) { await api.finance.approveApproval(a.id); toast.success("Approved & applied"); }
      else { await api.finance.rejectApproval(a.id); toast.success("Rejected"); }
      router.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
    setBusy(null);
  }

  return (
    <section>
      <h2 className="text-sm font-medium text-muted-foreground flex items-center gap-1.5 mb-3">
        <ShieldCheck className="h-4 w-4" /> Pending approvals
      </h2>

      {pending.length === 0 ? (
        <div className="rounded-md border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
          Nothing waiting on you.
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-card/40 divide-y divide-border">
          {pending.map((a) => (
            <div key={a.id} className="p-3 flex items-center gap-3 text-sm">
              {a.action === "delete_txn"
                ? <Trash2 className="h-4 w-4 text-rose-400 shrink-0" />
                : <Pencil className="h-4 w-4 text-amber-400 shrink-0" />}
              <div className="min-w-0 flex-1">
                <div className="font-medium truncate">{changeSummary(a)}</div>
                <div className="text-xs text-muted-foreground truncate">
                  {a.requested_by_name ?? "member"} · {a.txn_date ?? "—"}
                  {a.payee_text ? ` · ${a.payee_text}` : ""}
                  {a.amount != null ? ` · ${usd(a.amount)}` : ""}
                  {a.account_name ? ` · ${a.account_name}` : ""}
                </div>
              </div>
              <Button size="sm" variant="outline" disabled={busy === a.id}
                      onClick={() => decide(a, true)}>
                <Check className="h-3.5 w-3.5 mr-1" /> Approve
              </Button>
              <Button size="sm" variant="ghost" className="text-rose-400" disabled={busy === a.id}
                      onClick={() => decide(a, false)}>
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {decided.length > 0 && (
        <>
          <h3 className="text-xs font-medium text-muted-foreground mt-6 mb-2">Recently decided</h3>
          <div className="rounded-lg border border-border bg-card/40 divide-y divide-border">
            {decided.slice(0, 30).map((a) => (
              <div key={a.id} className="p-2.5 flex items-center gap-3 text-xs text-muted-foreground">
                <span className={cn("inline-block h-1.5 w-1.5 rounded-full shrink-0",
                  a.status === "approved" ? "bg-emerald-400" : "bg-rose-400")} />
                <span className="min-w-0 flex-1 truncate">
                  {changeSummary(a)} — {a.requested_by_name ?? "member"}
                  {a.payee_text ? ` · ${a.payee_text}` : ""}
                </span>
                <span>{a.status}{a.decided_by_name ? ` by ${a.decided_by_name}` : ""}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
