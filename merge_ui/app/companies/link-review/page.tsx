import { cookies } from "next/headers";
import Link from "next/link";
import { GitMerge, ChevronLeft } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { LinkReviewClient } from "@/components/link-review-client";
import { api, type LinkSuggestion } from "@/lib/api";

async function fetchSuggestions(): Promise<LinkSuggestion[]> {
  const cookie = (await cookies()).toString();
  try {
    return await api.listLinkSuggestions({ limit: 200 }, { cookieHeader: cookie });
  } catch {
    return [];
  }
}

export default async function LinkReviewPage() {
  const suggestions = await fetchSuggestions();

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-3xl">
        <header className="mb-4 sm:mb-6">
          <Link href="/companies" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mb-2">
            <ChevronLeft className="h-3.5 w-3.5" /> Companies
          </Link>
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <GitMerge className="h-5 w-5 text-muted-foreground" />
            Link review
          </h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-1">
            {suggestions.length} LinkedIn employer{suggestions.length === 1 ? "" : "s"} that
            fuzzy-match an existing company but aren&apos;t linked yet. Green % = high
            confidence; amber = check before linking. Link or dismiss each.
          </p>
        </header>

        <LinkReviewClient initial={suggestions} />
      </div>
    </AppShell>
  );
}
