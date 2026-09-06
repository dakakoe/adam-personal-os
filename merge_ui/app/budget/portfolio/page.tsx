import { cookies } from "next/headers";
import { Wallet } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type FinHolding, type FinAccount, type NetWorth, type FinAsset, type WalletsSummary } from "@/lib/api";
import { BudgetTabs } from "@/components/budget/budget-tabs";
import { PortfolioClient } from "@/components/budget/portfolio-client";
import { PositionsSection } from "@/components/budget/positions-section";
import { WalletsSummarySection } from "@/components/budget/wallets-summary-section";

async function fetchData() {
  const cookie = (await cookies()).toString();
  const opts = { cookieHeader: cookie };
  let holdings: FinHolding[] = [];
  let accounts: FinAccount[] = [];
  let netWorth: NetWorth | null = null;
  let assets: FinAsset[] = [];
  let walletsSummary: WalletsSummary | null = null;
  try {
    [holdings, accounts, netWorth, assets, walletsSummary] = await Promise.all([
      api.finance.listHoldings({}, opts),
      api.finance.listAccounts({ account_class: "investment" }, opts),
      api.finance.netWorth(opts),
      api.finance.listAssets(opts),
      api.finance.getWalletsSummary(opts).catch(() => null),
    ]);
  } catch { /* empty */ }
  return { holdings, accounts, netWorth, assets, walletsSummary };
}

export default async function BudgetPortfolioPage() {
  const { holdings, accounts, netWorth, assets, walletsSummary } = await fetchData();
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-4xl">
        <header className="mb-4">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Wallet className="h-5 w-5 text-muted-foreground" /> Budget
          </h1>
        </header>
        <BudgetTabs active="portfolio" />
        <PortfolioClient initial={holdings} accounts={accounts} assets={assets} usdThbRate={netWorth?.usd_thb_rate || 0} />
        <WalletsSummarySection summary={walletsSummary} />
        <PositionsSection accounts={accounts} />
      </div>
    </AppShell>
  );
}
