import { cookies } from "next/headers";
import Link from "next/link";
import { Building2, GitMerge } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { CompaniesClient } from "@/components/companies-client";
import { api, type CompanyRow } from "@/lib/api";

async function fetchData(): Promise<{ companies: CompanyRow[]; suggestions: number }> {
  const cookie = (await cookies()).toString();
  try {
    const [companies, count] = await Promise.all([
      api.listCompanies({ limit: 500 }, { cookieHeader: cookie }),
      api.countLinkSuggestions({ cookieHeader: cookie }).catch(() => ({ count: 0 })),
    ]);
    return { companies, suggestions: count.count };
  } catch {
    return { companies: [], suggestions: 0 };
  }
}

export default async function CompaniesPage() {
  const { companies, suggestions } = await fetchData();
  const isAdmin = (await cookies()).get("merge_role")?.value !== "budget";

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-4xl">
        <header className="mb-4 sm:mb-6 flex items-start justify-between gap-3">
          <div>
            <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
              <Building2 className="h-5 w-5 text-muted-foreground" />
              Companies
            </h1>
            <p className="text-xs sm:text-sm text-muted-foreground mt-1">
              {companies.length} companies · seeded from LinkedIn/profile data, connected to people &amp; deals.
            </p>
          </div>
          {suggestions > 0 && (
            <Link href="/companies/link-review"
              className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-md border border-amber-500/40 text-amber-400 text-xs hover:bg-amber-500/10 shrink-0">
              <GitMerge className="h-3.5 w-3.5" />
              Review {suggestions} link{suggestions === 1 ? "" : "s"}
            </Link>
          )}
        </header>

        <CompaniesClient initial={companies} isAdmin={isAdmin} />
      </div>
    </AppShell>
  );
}
