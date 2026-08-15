import Link from "next/link";
import { cookies } from "next/headers";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { AppShell } from "@/components/app-shell";
import { PersonAvatar } from "@/components/person-avatar";
import { PersonsCompanyFilter } from "@/components/persons-company-filter";
import { PersonsCircleFilter } from "@/components/persons-circle-filter";
import { PersonCircleTag } from "@/components/person-circle-tag";
import { PersonRowActions } from "@/components/person-row-actions";
import { api, type PersonRow, type ContactCircle } from "@/lib/api";
import { formatRelativeDate } from "@/lib/utils";
import { Mail, Linkedin, Send, ChevronLeft, ChevronRight, GitMerge, Trash2 } from "lucide-react";

// Reserved circle key for "in no circle at all" — the untagged backlog.
// Slugified keys are [a-z0-9-], so a real circle can never collide.
const NO_CIRCLE = "__none__";

const PAGE_SIZE = 100;

async function fetchData(
  q: string | undefined, companyId: string | undefined,
  circle: string | undefined, offset: number,
) {
  const cookie = (await cookies()).toString();
  try {
    // Fetch the page slice and the total count in parallel so the
    // "Showing N–M of TOTAL" header is honest about the corpus size
    // (the previous "100 shown" implied "100 total" and was a lie).
    const [rows, count] = await Promise.all([
      api.listPersons({ q, company_id: companyId, circle, limit: PAGE_SIZE, offset }, { cookieHeader: cookie }),
      api.countPersons({ q, company_id: companyId, circle }, { cookieHeader: cookie }),
    ]);
    return { rows, total: count.count };
  } catch {
    return { rows: [] as PersonRow[], total: 0 };
  }
}

export default async function PersonsPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; offset?: string; company?: string; circle?: string }>;
}) {
  const { q, offset: offsetStr, company, circle } = await searchParams;
  const offset = Math.max(0, parseInt(offsetStr ?? "0", 10) || 0);
  const { rows, total } = await fetchData(q, company, circle, offset);
  // budget (member) users get a read-only, scoped contacts list: no admin tools
  // (merge/cleanup) and rows don't link into the admin-only detail page.
  const isAdmin = (await cookies()).get("merge_role")?.value !== "budget";
  // Resolve the filtered company's name so the chip reads as a name, not a UUID.
  let companyName: string | null = null;
  if (company) {
    try {
      const co = await api.getCompany(company, { cookieHeader: (await cookies()).toString() });
      companyName = co?.name ?? null;
    } catch { /* keep null — chip falls back to "Company" */ }
  }
  // Same for the circle: the URL carries the key ('family'), the chip shows
  // the label ('Family'). Falls back to the key, which is still readable.
  // Fetched once for the page: the chip label AND the catalogue every row's
  // tag dropdown needs. 100 rows fetching this themselves would be 100
  // identical requests.
  let circleCatalogue: ContactCircle[] = [];
  try {
    circleCatalogue = await api.listCircles({ cookieHeader: (await cookies()).toString() });
  } catch { /* tagging degrades to "no circles yet"; the list still renders */ }
  const circleLabel = circle === NO_CIRCLE
    ? "No circle"
    : circle
      ? circleCatalogue.find((c) => c.key === circle)?.label ?? null
      : null;
  const from = total === 0 ? 0 : offset + 1;
  const to = offset + rows.length;
  const prevOffset = Math.max(0, offset - PAGE_SIZE);
  const nextOffset = offset + PAGE_SIZE;
  const hasPrev = offset > 0;
  const hasNext = nextOffset < total;
  const queryString = (newOffset: number) => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (company) params.set("company", company);
    if (circle) params.set("circle", circle);
    if (newOffset > 0) params.set("offset", String(newOffset));
    const qs = params.toString();
    return qs ? `?${qs}` : "";
  };

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-5xl">
        <header className="mb-6 flex items-baseline justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">People</h1>
            <p className="text-sm text-muted-foreground mt-1 tabular">
              {total === 0
                ? "no people found"
                : <>Showing <span className="text-foreground font-medium">{from.toLocaleString()}–{to.toLocaleString()}</span> of <span className="text-foreground font-medium">{total.toLocaleString()}</span></>}
              {" "}· press{" "}
              <kbd className="font-mono text-[10px] px-1 py-0.5 rounded border border-border bg-muted">
                ⌘K
              </kbd>{" "}
              to search
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2 shrink-0">
            <PersonsCompanyFilter currentId={company ?? null} currentName={companyName} />
            <PersonsCircleFilter currentKey={circle ?? null} currentLabel={circleLabel} />
            {isAdmin && (
              <>
                <Link
                  href="/merge"
                  className="inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-xs text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                >
                  <GitMerge className="h-3.5 w-3.5" />
                  Merge queue
                </Link>
                <Link
                  href="/cleanup"
                  className="inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-xs text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Cleanup
                </Link>
              </>
            )}
            {(hasPrev || hasNext) && (
              <nav className="flex items-center gap-1">
                <Link
                  href={hasPrev ? `/persons${queryString(prevOffset)}` : "#"}
                  aria-disabled={!hasPrev}
                  className={`inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-xs ${hasPrev ? "hover:bg-accent" : "opacity-40 pointer-events-none"}`}
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  Prev
                </Link>
                <Link
                  href={hasNext ? `/persons${queryString(nextOffset)}` : "#"}
                  aria-disabled={!hasNext}
                  className={`inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-xs ${hasNext ? "hover:bg-accent" : "opacity-40 pointer-events-none"}`}
                >
                  Next
                  <ChevronRight className="h-3.5 w-3.5" />
                </Link>
              </nav>
            )}
          </div>
        </header>

        <div className="rounded-lg border border-border overflow-hidden bg-card/40">
          {rows.length === 0 ? (
            <div className="p-10 text-center text-muted-foreground text-sm">
              No people found.
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {rows.map((p) => (
                <li key={p.person_id}>
                  {isAdmin ? (
                    // The tag control sits OUTSIDE the Link — nested inside it,
                    // every click would navigate to the contact instead of
                    // opening the dropdown.
                    <div className="group flex items-center gap-2 pr-3 hover:bg-accent/40 transition-colors">
                      <Link
                        href={`/persons/${p.person_id}`}
                        className="flex items-center gap-3 px-4 py-2.5 min-w-0 flex-1"
                      >
                        <PersonRowInner row={p} showShared />
                      </Link>
                      <PersonCircleTag
                        personId={p.person_id}
                        circles={p.circles ?? []}
                        catalogue={circleCatalogue}
                      />
                      {/* Outside the Link for the same reason as the tag: a
                          click inside it would navigate instead of acting. */}
                      <PersonRowActions personId={p.person_id} displayName={p.display_name} />
                    </div>
                  ) : (
                    <div className="flex items-center gap-3 px-4 py-2.5">
                      <PersonRowInner row={p} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {(hasPrev || hasNext) && (
          <nav className="mt-4 flex items-center justify-between gap-3 text-xs">
            <Link
              href={hasPrev ? `/persons${queryString(prevOffset)}` : "#"}
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
              href={hasNext ? `/persons${queryString(nextOffset)}` : "#"}
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

function PersonRowInner({ row: p, showShared }: { row: PersonRow; showShared?: boolean }) {
  return (
    <>
      <PersonAvatar personId={p.person_id} displayName={p.display_name} className="h-8 w-8 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="font-medium truncate">{p.display_name}</span>
          <ChannelGlyphs row={p} />
          {showShared && p.visibility === "shared" && (
            <span className="text-[10px] text-emerald-400 border border-emerald-500/40 rounded px-1">shared</span>
          )}
        </div>
        <div className="text-xs text-muted-foreground truncate font-mono">
          {[p.telegram_username && `@${p.telegram_username}`, p.email, p.linkedin]
            .filter(Boolean)
            .join(" · ") || "—"}
        </div>
      </div>
      <div className="text-right text-xs text-muted-foreground tabular shrink-0">
        <div className="text-foreground font-medium">{p.total_interactions.toLocaleString()}</div>
        <div>{formatRelativeDate(p.last_interaction_at)}</div>
      </div>
    </>
  );
}

function ChannelGlyphs({ row }: { row: PersonRow }) {
  return (
    <span className="inline-flex items-center gap-1 text-muted-foreground">
      {row.telegram_username && <Send className="h-3 w-3 text-[var(--color-channel-telegram)]" />}
      {row.email && <Mail className="h-3 w-3 text-[var(--color-channel-email)]" />}
      {row.linkedin && <Linkedin className="h-3 w-3 text-[var(--color-channel-linkedin)]" />}
    </span>
  );
}
