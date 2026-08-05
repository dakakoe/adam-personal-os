import { cookies } from "next/headers";
import Link from "next/link";
import { ChevronLeft, ChevronRight, Trash2 } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { CleanupList } from "@/components/cleanup-list";
import { api, type CleanupCandidate } from "@/lib/api";

const PAGE_SIZE = 50;

async function fetchData(q: string | undefined, offset: number) {
  const cookie = (await cookies()).toString();
  try {
    const [rows, count] = await Promise.all([
      api.listCleanupCandidates({ q, limit: PAGE_SIZE, offset }, { cookieHeader: cookie }),
      api.countCleanupCandidates({ q }, { cookieHeader: cookie }),
    ]);
    return { rows, total: count.count };
  } catch {
    return { rows: [] as CleanupCandidate[], total: 0 };
  }
}

export default async function CleanupPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; offset?: string }>;
}) {
  const { q, offset: offsetStr } = await searchParams;
  const offset = Math.max(0, parseInt(offsetStr ?? "0", 10) || 0);
  const { rows, total } = await fetchData(q, offset);
  const from = total === 0 ? 0 : offset + 1;
  const to = offset + rows.length;
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;
  const queryString = (newOffset: number) => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (newOffset > 0) params.set("offset", String(newOffset));
    const qs = params.toString();
    return qs ? `?${qs}` : "";
  };

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-5xl">
        <header className="mb-4 sm:mb-6 flex flex-wrap items-baseline justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
              <Trash2 className="h-5 w-5 text-muted-foreground" />
              Cleanup
            </h1>
            <p className="text-xs sm:text-sm text-muted-foreground mt-1 tabular">
              {total === 0 ? (
                "No automation senders detected — looks clean."
              ) : (
                <>
                  Showing{" "}
                  <span className="text-foreground font-medium">
                    {from.toLocaleString()}–{to.toLocaleString()}
                  </span>{" "}
                  of{" "}
                  <span className="text-foreground font-medium">
                    {total.toLocaleString()}
                  </span>{" "}
                  <span className="hidden sm:inline">
                    automation candidates (newsletters, noreply, brand senders).
                    Sorted busiest-first.
                  </span>
                  <span className="sm:hidden">automation candidates.</span>
                </>
              )}
            </p>
          </div>
          {(hasPrev || hasNext) && (
            <nav className="flex items-center gap-1 shrink-0">
              <Link
                href={hasPrev ? `/cleanup${queryString(Math.max(0, offset - PAGE_SIZE))}` : "#"}
                aria-disabled={!hasPrev}
                className={`inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-xs ${hasPrev ? "hover:bg-accent" : "opacity-40 pointer-events-none"}`}
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                Prev
              </Link>
              <Link
                href={hasNext ? `/cleanup${queryString(offset + PAGE_SIZE)}` : "#"}
                aria-disabled={!hasNext}
                className={`inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-xs ${hasNext ? "hover:bg-accent" : "opacity-40 pointer-events-none"}`}
              >
                Next
                <ChevronRight className="h-3.5 w-3.5" />
              </Link>
            </nav>
          )}
        </header>

        <CleanupList rows={rows} />

        {(hasPrev || hasNext) && (
          <nav className="mt-4 flex items-center justify-between gap-3 text-xs">
            <Link
              href={hasPrev ? `/cleanup${queryString(Math.max(0, offset - PAGE_SIZE))}` : "#"}
              aria-disabled={!hasPrev}
              className={`inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border ${hasPrev ? "hover:bg-accent" : "opacity-40 pointer-events-none"}`}
            >
              <ChevronLeft className="h-3.5 w-3.5" />
              Prev {PAGE_SIZE}
            </Link>
            <span className="text-muted-foreground tabular">
              page {Math.floor(offset / PAGE_SIZE) + 1} of {Math.max(1, Math.ceil(total / PAGE_SIZE))}
            </span>
            <Link
              href={hasNext ? `/cleanup${queryString(offset + PAGE_SIZE)}` : "#"}
              aria-disabled={!hasNext}
              className={`inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border ${hasNext ? "hover:bg-accent" : "opacity-40 pointer-events-none"}`}
            >
              Next {PAGE_SIZE}
              <ChevronRight className="h-3.5 w-3.5" />
            </Link>
          </nav>
        )}
      </div>
    </AppShell>
  );
}
