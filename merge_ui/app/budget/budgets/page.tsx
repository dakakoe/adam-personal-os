import { cookies } from "next/headers";
import { Wallet } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type FinBudget, type FinCategory, type NetWorth } from "@/lib/api";
import { BudgetTabs } from "@/components/budget/budget-tabs";
import { BudgetsClient } from "@/components/budget/budgets-client";

async function fetchData() {
  const cookie = (await cookies()).toString();
  const opts = { cookieHeader: cookie };
  let budgets: FinBudget[] = [];
  let categories: FinCategory[] = [];
  let netWorth: NetWorth | null = null;
  try {
    [budgets, categories, netWorth] = await Promise.all([
      api.finance.listBudgets(undefined, opts),
      api.finance.listCategories(opts),
      api.finance.netWorth(opts),
    ]);
  } catch { /* empty */ }
  return { budgets, categories, netWorth };
}

export default async function BudgetBudgetsPage() {
  const { budgets, categories, netWorth } = await fetchData();
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-3xl">
        <header className="mb-4">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Wallet className="h-5 w-5 text-muted-foreground" /> Budget
          </h1>
        </header>
        <BudgetTabs active="budgets" />
        <BudgetsClient initialBudgets={budgets} categories={categories} usdThbRate={netWorth?.usd_thb_rate || 0} />
      </div>
    </AppShell>
  );
}
