import { cookies } from "next/headers";
import Link from "next/link";
import { Sparkles } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { SuggestionsInbox } from "@/components/suggestions-inbox";
import { api, type SuggestionRow } from "@/lib/api";

async function fetchSuggestions(statusFilter: string, kind: string | undefined): Promise<SuggestionRow[]> {
  const cookie = (await cookies()).toString();
  try {
    return await api.listSuggestions(
      { status_filter: statusFilter, kind, limit: 200 },
      { cookieHeader: cookie },
    );
  } catch {
    return [];
  }
}

export default async function SuggestionsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; kind?: string }>;
}) {
  const sp = await searchParams;
  const statusFilter = sp.status ?? "pending";
  const suggestions = await fetchSuggestions(statusFilter, sp.kind);

  const chip = (label: string, status: string, kind?: string) => {
    const active = statusFilter === status && (sp.kind ?? "") === (kind ?? "");
    const params = new URLSearchParams();
    if (status !== "pending") params.set("status", status);
    else params.set("status", "pending");
    if (kind) params.set("kind", kind);
    return (
      <Link
        key={label}
        href={`/suggestions?${params.toString()}`}
        className={`inline-flex items-center h-7 px-2.5 rounded-md border text-xs ${
          active ? "border-primary text-foreground bg-accent/40" : "border-border text-muted-foreground hover:text-foreground hover:bg-accent/20"
        }`}
      >
        {label}
      </Link>
    );
  };

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-4xl">
        <header className="mb-4 sm:mb-6">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-muted-foreground" />
            Inbox
          </h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-1">
            Auto-detected tasks, opportunities & follow-ups from your meetings. Accept to materialize; dismiss to ignore.
          </p>
        </header>

        <div className="mb-3 flex flex-wrap gap-2">
          {chip("Pending", "pending")}
          {chip("Tasks", "pending", "task")}
          {chip("Opportunities", "pending", "opportunity")}
          {chip("Follow-ups", "pending", "person_mention")}
          <span className="text-muted-foreground/40 self-center">·</span>
          {chip("Accepted", "accepted")}
          {chip("Dismissed", "dismissed")}
          {chip("All", "all")}
        </div>

        <SuggestionsInbox initial={suggestions} />
      </div>
    </AppShell>
  );
}
