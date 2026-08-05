import { cookies } from "next/headers";
import { Wallet } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type SpendingLine, type CashflowMonth, type NetWorth } from "@/lib/api";
import { BudgetTabs } from "@/components/budget/budget-tabs";
import { ReportsClient } from "@/components/budget/reports-client";

function monthStartISO(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}
function todayISO(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(new Date());
}

async function fetchData() {
  const cookie = (await cookies()).toString();
  const opts = { cookieHeader: cookie };
  let spending: SpendingLine[] = [];
  let cashflow: CashflowMonth[] = [];
  let netWorth: NetWorth | null = null;
  try {
    [spending, cashflow, netWorth] = await Promise.all([
      api.finance.reportSpending(monthStartISO(), todayISO(), opts),
      api.finance.reportCashflow(6, opts),
      api.finance.netWorth(opts),
    ]);
  } catch { /* empty */ }
  return { spending, cashflow, netWorth };
}

export default async function BudgetReportsPage() {
  const { spending, cashflow, netWorth } = await fetchData();
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-5xl">
        <header className="mb-4">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Wallet className="h-5 w-5 text-muted-foreground" /> Budget
          </h1>
        </header>
        <BudgetTabs active="reports" />
        <ReportsClient
          initialSpending={spending} cashflow={cashflow} netWorth={netWorth}
          usdThbRate={netWorth?.usd_thb_rate || 0} />
      </div>
    </AppShell>
  );
}
