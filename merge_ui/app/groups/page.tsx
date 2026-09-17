import { cookies } from "next/headers";
import Link from "next/link";
import { ChevronLeft, ChevronRight, Users, Search } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { GroupsList } from "@/components/groups-list";
import { GroupSuggestions } from "@/components/group-suggestions";
import { api, type TelegramGroup, type GroupSuggestion } from "@/lib/api";

const PAGE_SIZE = 50;

const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: "members_asc",  label: "Members — small first" },
  { value: "members_desc", label: "Members — large first" },
  { value: "recent",       label: "Recently active" },
  { value: "title",        label: "Title (A–Z)" },
];

type SP = {
  q?: string; kind?: string; enabled?: string;
  min?: string; max?: string; sort?: string;
  offset?: string;
};

async function fetchData(
  q: string | undefined,
  kind: string | undefined,
  enabled: boolean | undefined,
  minMembers: number | undefined,
  maxMembers: number | undefined,
  sort: string,
  offset: number,
) {
  const cookie = (await cookies()).toString();
  try {
    const [rows, total, enabledCount, suggestions] = await Promise.all([
      api.listTelegramGroups(
        { q, kind, enabled, min_members: minMembers, max_members: maxMembers, sort, limit: PAGE_SIZE, offset },
        { cookieHeader: cookie },
      ),
      api.countTelegramGroups(
        { q, kind, enabled, min_members: minMembers, max_members: maxMembers },
        { cookieHeader: cookie },
      ),
      api.countTelegramGroups({ enabled: true }, { cookieHeader: cookie }),
      api.listGroupSuggestions({ cookieHeader: cookie }).catch(() => [] as GroupSuggestion[]),
    ]);
    return { rows, total: total.count, enabledCount: enabledCount.count, suggestions };
  } catch {
    return { rows: [] as TelegramGroup[], total: 0, enabledCount: 0, suggestions: [] as GroupSuggestion[] };
  }
}

export default async function GroupsPage({
  searchParams,
}: {
  searchParams: Promise<SP>;
}) {
  const sp = await searchParams;
  const q = sp.q || undefined;
  const kind = sp.kind || undefined;
  const enabled = sp.enabled === "true" ? true : sp.enabled === "false" ? false : undefined;
  const minMembers = sp.min ? Math.max(0, parseInt(sp.min, 10) || 0) : undefined;
  const maxMembers = sp.max ? Math.max(0, parseInt(sp.max, 10) || 0) : undefined;
  const sort = SORT_OPTIONS.find((s) => s.value === sp.sort)?.value ?? "members_asc";
  const offset = Math.max(0, parseInt(sp.offset ?? "0", 10) || 0);

  const { rows, total, enabledCount, suggestions } = await fetchData(
    q, kind, enabled, minMembers, maxMembers, sort, offset,
  );
  const from = total === 0 ? 0 : offset + 1;
  const to = offset + rows.length;
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  // Preserve query params when building pagination + chip URLs
  const buildQs = (overrides: Partial<SP>) => {
    const params = new URLSearchParams();
    const merged = { q, kind, enabled: sp.enabled, min: sp.min, max: sp.max, sort, ...overrides } as SP;
    for (const [k, v] of Object.entries(merged)) {
      if (v !== undefined && v !== "" && v !== null) params.set(k, String(v));
    }
    // Don't carry stale offset across non-pagination changes
    if (!("offset" in overrides)) params.delete("offset");
    const qs = params.toString();
    return qs ? `?${qs}` : "";
  };

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-5xl">
        <header className="mb-4 sm:mb-6 flex flex-wrap items-baseline justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
              <Users className="h-5 w-5 text-muted-foreground" />
              Telegram groups
            </h1>
            <p className="text-xs sm:text-sm text-muted-foreground mt-1 tabular">
              {total === 0
                ? "No groups match these filters."
                : <>
                    Showing <span className="text-foreground font-medium">{from.toLocaleString()}–{to.toLocaleString()}</span> of <span className="text-foreground font-medium">{total.toLocaleString()}</span>
                    {" · "}
                    <span className="text-emerald-500">{enabledCount.toLocaleString()} enabled</span>
                  </>}
            </p>
          </div>
          {(hasPrev || hasNext) && (
            <nav className="flex items-center gap-1 shrink-0">
              <Link
                href={hasPrev ? `/groups${buildQs({ offset: String(Math.max(0, offset - PAGE_SIZE)) })}` : "#"}
                aria-disabled={!hasPrev}
                className={`inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-xs ${hasPrev ? "hover:bg-accent" : "opacity-40 pointer-events-none"}`}
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                Prev
              </Link>
              <Link
                href={hasNext ? `/groups${buildQs({ offset: String(offset + PAGE_SIZE) })}` : "#"}
                aria-disabled={!hasNext}
                className={`inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border text-xs ${hasNext ? "hover:bg-accent" : "opacity-40 pointer-events-none"}`}
              >
                Next
                <ChevronRight className="h-3.5 w-3.5" />
              </Link>
            </nav>
          )}
        </header>

        {suggestions.length > 0 && <GroupSuggestions initial={suggestions} />}

        {/* Filter form. Native GET form so the server re-renders with the
            new query params — no client state needed. Hidden inputs
            preserve the chip-selected kind/enabled across submits. */}
        <form action="/groups" method="get" className="mb-3 rounded-md border border-border bg-card/40 p-3 space-y-2">
          <input type="hidden" name="kind" value={kind ?? ""} />
          <input type="hidden" name="enabled" value={sp.enabled ?? ""} />
          <div className="flex flex-wrap gap-2 items-end">
            <label className="flex-1 min-w-[160px]">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Search title</span>
              <div className="relative mt-0.5">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
                <input
                  type="text"
                  name="q"
                  defaultValue={q ?? ""}
                  placeholder="e.g. founders, family…"
                  className="w-full h-8 pl-7 pr-2 text-sm rounded-md border border-border bg-background outline-none focus:border-primary"
                />
              </div>
            </label>
            <label className="w-[100px]">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Min members</span>
              <input
                type="number"
                inputMode="numeric"
                min="0"
                name="min"
                defaultValue={sp.min ?? ""}
                placeholder="0"
                className="mt-0.5 w-full h-8 px-2 text-sm rounded-md border border-border bg-background outline-none focus:border-primary tabular"
              />
            </label>
            <label className="w-[100px]">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Max members</span>
              <input
                type="number"
                inputMode="numeric"
                min="0"
                name="max"
                defaultValue={sp.max ?? ""}
                placeholder="∞"
                className="mt-0.5 w-full h-8 px-2 text-sm rounded-md border border-border bg-background outline-none focus:border-primary tabular"
              />
            </label>
            <label className="min-w-[170px]">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Sort</span>
              <select
                name="sort"
                defaultValue={sort}
                className="mt-0.5 w-full h-8 px-2 text-sm rounded-md border border-border bg-background outline-none focus:border-primary"
              >
                {SORT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              className="h-8 px-3 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90"
            >
              Apply
            </button>
            {(q || sp.min || sp.max || sort !== "members_asc") && (
              <Link
                href={`/groups${buildQs({ q: undefined, min: undefined, max: undefined, sort: "members_asc" })}`}
                className="h-8 px-2 text-xs inline-flex items-center text-muted-foreground hover:text-foreground"
              >
                Reset
              </Link>
            )}
          </div>
        </form>

        {/* Filter chips — kind + enabled. Each is a Link that flips one
            facet while preserving the rest via buildQs. */}
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <FilterChip href={`/groups${buildQs({ enabled: "" })}`} active={enabled === undefined}>All</FilterChip>
          <FilterChip href={`/groups${buildQs({ enabled: "true" })}`} active={enabled === true}>Enabled only</FilterChip>
          <FilterChip href={`/groups${buildQs({ enabled: "false" })}`} active={enabled === false}>Disabled only</FilterChip>
          <span className="text-muted-foreground/50 mx-1">·</span>
          <FilterChip href={`/groups${buildQs({ kind: "" })}`} active={!kind}>Any type</FilterChip>
          <FilterChip href={`/groups${buildQs({ kind: "group" })}`} active={kind === "group"}>Groups</FilterChip>
          <FilterChip href={`/groups${buildQs({ kind: "supergroup" })}`} active={kind === "supergroup"}>Supergroups</FilterChip>
          <FilterChip href={`/groups${buildQs({ kind: "channel" })}`} active={kind === "channel"}>Channels</FilterChip>
        </div>

        <GroupsList rows={rows} />

        {(hasPrev || hasNext) && (
          <nav className="mt-4 flex items-center justify-between gap-3 text-xs">
            <Link
              href={hasPrev ? `/groups${buildQs({ offset: String(Math.max(0, offset - PAGE_SIZE)) })}` : "#"}
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
              href={hasNext ? `/groups${buildQs({ offset: String(offset + PAGE_SIZE) })}` : "#"}
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

function FilterChip({
  href, active, children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={`inline-flex items-center h-7 px-2.5 rounded-md border ${active ? "border-primary text-foreground bg-accent/40" : "border-border text-muted-foreground hover:text-foreground hover:bg-accent/20"}`}
    >
      {children}
    </Link>
  );
}
