import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { Wallet } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type FinApproval } from "@/lib/api";
import { BudgetTabs } from "@/components/budget/budget-tabs";
import { ApprovalsClient } from "@/components/budget/approvals-client";

export default async function BudgetApprovalsPage() {
  const cookie = (await cookies()).toString();
  // owner-only surface; budget (member) users are bounced to the overview
  if ((await cookies()).get("merge_role")?.value === "budget") redirect("/budget");
  let approvals: FinApproval[] = [];
  try { approvals = await api.finance.listApprovals({}, { cookieHeader: cookie }); } catch { /* empty */ }
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-4xl">
        <header className="mb-4">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Wallet className="h-5 w-5 text-muted-foreground" /> Budget
          </h1>
        </header>
        <BudgetTabs active="approvals" />
        <ApprovalsClient initial={approvals} />
      </div>
    </AppShell>
  );
}
