import { cookies } from "next/headers";
import { Wallet } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type FinCategory } from "@/lib/api";
import { BudgetTabs } from "@/components/budget/budget-tabs";
import { CategoriesClient } from "@/components/budget/categories-client";

export default async function BudgetCategoriesPage() {
  const cookie = (await cookies()).toString();
  let categories: FinCategory[] = [];
  try { categories = await api.finance.listCategories({ cookieHeader: cookie }); } catch { /* empty */ }
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-3xl">
        <header className="mb-4">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Wallet className="h-5 w-5 text-muted-foreground" /> Budget
          </h1>
        </header>
        <BudgetTabs active="categories" />
        <CategoriesClient initial={categories} />
      </div>
    </AppShell>
  );
}
