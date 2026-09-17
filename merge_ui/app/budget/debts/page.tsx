import { cookies } from "next/headers";
import { Wallet } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type FinTransaction, type FinAccount } from "@/lib/api";
import { BudgetTabs } from "@/components/budget/budget-tabs";
import { DebtsClient } from "@/components/budget/debts-client";

// Unified debts view: both debt accounts ("I owe" / "Owed to me") netted per
// person into a single flow, so a settled debt (loan + repayment) reads as one
// square line instead of two rows across two filtered lists.
async function fetchData() {
  const cookie = (await cookies()).toString();
  const opts = { cookieHeader: cookie };
  let debtAccounts: FinAccount[] = [];
  let txns: FinTransaction[] = [];
  try {
    const accounts = await api.finance.listAccounts({}, opts);
    debtAccounts = accounts.filter((a) => a.kind === "debt");
    // one fetch per debt account (account_id matches either leg); merge + dedupe
    const lists = await Promise.all(
      debtAccounts.map((a) => api.finance.listTransactions({ account_id: a.id, limit: 1000 }, opts)));
    const seen = new Set<string>();
    for (const list of lists) for (const t of list) if (!seen.has(t.id)) { seen.add(t.id); txns.push(t); }
  } catch { /* empty state */ }
  return { debtAccounts, txns };
}

export default async function BudgetDebtsPage() {
  const { debtAccounts, txns } = await fetchData();
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-6xl">
        <header className="mb-4">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Wallet className="h-5 w-5 text-muted-foreground" /> Budget
          </h1>
        </header>
        <BudgetTabs active="debts" />
        <DebtsClient accounts={debtAccounts} txns={txns} />
      </div>
    </AppShell>
  );
}
