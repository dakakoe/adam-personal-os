import { cookies } from "next/headers";
import { Wallet } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type FinAccount, type FinImportBatch } from "@/lib/api";
import { BudgetTabs } from "@/components/budget/budget-tabs";
import { ImportClient } from "@/components/budget/import-client";

async function fetchData() {
  const cookie = (await cookies()).toString();
  const opts = { cookieHeader: cookie };
  let accounts: FinAccount[] = [];
  let batches: FinImportBatch[] = [];
  try {
    [accounts, batches] = await Promise.all([
      api.finance.listAccounts({}, opts),
      api.finance.listImports(opts),
    ]);
  } catch { /* empty */ }
  return { accounts, batches };
}

export default async function BudgetImportPage() {
  const { accounts, batches } = await fetchData();
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-4xl">
        <header className="mb-4">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Wallet className="h-5 w-5 text-muted-foreground" /> Budget
          </h1>
        </header>
        <BudgetTabs active="import" />
        <ImportClient accounts={accounts} batches={batches} />
      </div>
    </AppShell>
  );
}
