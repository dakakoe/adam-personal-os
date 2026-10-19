import { cookies } from "next/headers";
import { Wallet } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type FinPayee } from "@/lib/api";
import { BudgetTabs } from "@/components/budget/budget-tabs";
import { PayeesClient } from "@/components/budget/payees-client";

export default async function BudgetPayeesPage() {
  const cookie = (await cookies()).toString();
  let payees: FinPayee[] = [];
  try { payees = await api.finance.listPayees({}, { cookieHeader: cookie }); } catch { /* empty */ }
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-4xl">
        <header className="mb-4">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Wallet className="h-5 w-5 text-muted-foreground" /> Budget
          </h1>
        </header>
        <BudgetTabs active="payees" />
        <PayeesClient initial={payees} />
      </div>
    </AppShell>
  );
}
