import { cookies } from "next/headers";
import { Wallet } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type FinTransaction, type FinAccount, type FinCategory, type FinPayee } from "@/lib/api";
import { BudgetTabs } from "@/components/budget/budget-tabs";
import { TransactionsClient } from "@/components/budget/transactions-client";

async function fetchData(accountId: string | undefined) {
  const cookie = (await cookies()).toString();
  const opts = { cookieHeader: cookie };
  let txns: FinTransaction[] = [];
  let accounts: FinAccount[] = [];
  let categories: FinCategory[] = [];
  let payees: FinPayee[] = [];
  try {
    [txns, accounts, categories, payees] = await Promise.all([
      api.finance.listTransactions({ limit: 300, account_id: accountId }, opts),
      api.finance.listAccounts({}, opts),
      api.finance.listCategories(opts),
      api.finance.listPayees({}, opts),
    ]);
  } catch { /* empty */ }
  return { txns, accounts, categories, payees };
}

export default async function BudgetTransactionsPage({
  searchParams,
}: { searchParams: Promise<{ account?: string }> }) {
  const { account } = await searchParams;
  const { txns, accounts, categories, payees } = await fetchData(account);
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-6xl">
        <header className="mb-4">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Wallet className="h-5 w-5 text-muted-foreground" /> Budget
          </h1>
        </header>
        <BudgetTabs active="transactions" />
        <TransactionsClient initialTxns={txns} accounts={accounts} categories={categories} payees={payees} initialAccount={account ?? ""} />
      </div>
    </AppShell>
  );
}
