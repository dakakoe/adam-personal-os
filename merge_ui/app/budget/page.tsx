import { cookies } from "next/headers";
import { Wallet } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type NetWorth, type FinAccount, type FinAsset, type FinHolding } from "@/lib/api";
import { BudgetDashboard } from "@/components/budget/budget-dashboard";
import { BudgetTabs } from "@/components/budget/budget-tabs";

async function fetchData() {
  const cookie = (await cookies()).toString();
  const opts = { cookieHeader: cookie };
  let netWorth: NetWorth | null = null;
  let accounts: FinAccount[] = [];
  let assets: FinAsset[] = [];
  let holdings: FinHolding[] = [];
  try {
    [netWorth, accounts, assets, holdings] = await Promise.all([
      api.finance.netWorth(opts),
      api.finance.listAccounts({ account_class: "operational" }, opts),
      api.finance.listAssets(opts),
      api.finance.listHoldings({}, opts),
    ]);
  } catch { /* empty state */ }
  return { netWorth, accounts, assets, holdings };
}

export default async function BudgetPage() {
  const { netWorth, accounts, assets, holdings } = await fetchData();
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-6xl">
        <header className="mb-4">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Wallet className="h-5 w-5 text-muted-foreground" /> Budget
          </h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-1">
            Net worth across banks, cash, crypto and brokerages — in THB or USD.
          </p>
        </header>
        <BudgetTabs active="overview" />
        <BudgetDashboard netWorth={netWorth} accounts={accounts} assets={assets} holdings={holdings} />
      </div>
    </AppShell>
  );
}
