import Link from "next/link";
import { cookies } from "next/headers";
import { cn } from "@/lib/utils";

const TABS = [
  { key: "overview", label: "Overview", href: "/budget" },
  { key: "transactions", label: "Transactions", href: "/budget/transactions" },
  { key: "debts", label: "Debts", href: "/budget/debts" },
  { key: "planned", label: "Planned", href: "/budget/planned" },
  { key: "portfolio", label: "Portfolio", href: "/budget/portfolio" },
  { key: "reports", label: "Reports", href: "/budget/reports" },
  { key: "budgets", label: "Budgets", href: "/budget/budgets" },
  { key: "categories", label: "Categories", href: "/budget/categories" },
  { key: "payees", label: "Payees", href: "/budget/payees" },
  { key: "import", label: "Import", href: "/budget/import" },
];
// owner-only tabs (sharing PR1/PR3): members admin + the approvals queue
const ADMIN_TABS = [
  { key: "approvals", label: "Approvals", href: "/budget/approvals" },
  { key: "members", label: "Members", href: "/budget/members" },
];

export async function BudgetTabs({ active }: { active: string }) {
  // the readable merge_role cookie mirrors the API's real enforcement; budget
  // (member) users never see the Members tab
  const isAdmin = (await cookies()).get("merge_role")?.value !== "budget";
  const tabs = isAdmin ? [...TABS, ...ADMIN_TABS] : TABS;
  return (
    // Full-bleed, horizontally scrollable on mobile so the tabs never overflow the
    // viewport; a normal inline bar from sm up. -mx-4/px-4 lets it scroll to the
    // screen edge while keeping the page gutter as the resting position.
    <div className="mb-4 -mx-4 px-4 sm:mx-0 sm:px-0 overflow-x-auto no-scrollbar">
      <div className="inline-flex rounded-md border border-border overflow-hidden text-xs whitespace-nowrap">
        {tabs.map((t) => (
          <Link key={t.key} href={t.href}
            className={cn("shrink-0 px-3 py-2 sm:py-1.5", active === t.key
              ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/40")}>
            {t.label}
          </Link>
        ))}
      </div>
    </div>
  );
}
