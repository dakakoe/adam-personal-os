import { cookies } from "next/headers";
import { Wallet } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type FinPlanned, type FinAccount, type FinCategory } from "@/lib/api";
import { BudgetTabs } from "@/components/budget/budget-tabs";
import { PlannedClient } from "@/components/budget/planned-client";

async function fetchData() {
  const cookie = (await cookies()).toString();
  const opts = { cookieHeader: cookie };
  let planned: FinPlanned[] = [];
  let accounts: FinAccount[] = [];
  let categories: FinCategory[] = [];
  try {
    [planned, accounts, categories] = await Promise.all([
      api.finance.listPlanned(opts),
      api.finance.listAccounts({}, opts),
      api.finance.listCategories(opts),
    ]);
  } catch { /* empty */ }
  return { planned, accounts, categories };
}

export default async function BudgetPlannedPage() {
  const { planned, accounts, categories } = await fetchData();
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-4xl">
        <header className="mb-4">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Wallet className="h-5 w-5 text-muted-foreground" /> Budget
          </h1>
        </header>
        <BudgetTabs active="planned" />
        <PlannedClient initial={planned} accounts={accounts} categories={categories} />
      </div>
    </AppShell>
  );
}
