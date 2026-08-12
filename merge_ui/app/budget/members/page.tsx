import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { Wallet } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type FinMember } from "@/lib/api";
import { BudgetTabs } from "@/components/budget/budget-tabs";
import { MembersClient } from "@/components/budget/members-client";

export default async function BudgetMembersPage() {
  const cookie = (await cookies()).toString();
  // owner-only surface; budget (member) users are bounced to the overview
  if ((await cookies()).get("merge_role")?.value === "budget") redirect("/budget");
  let members: FinMember[] = [];
  try { members = await api.finance.listMembers({ cookieHeader: cookie }); } catch { /* empty */ }
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-4xl">
        <header className="mb-4">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Wallet className="h-5 w-5 text-muted-foreground" /> Budget
          </h1>
        </header>
        <BudgetTabs active="members" />
        <MembersClient initial={members} />
      </div>
    </AppShell>
  );
}
