import { cookies } from "next/headers";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, ChevronLeft, ChevronRight, Search } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { PersonAvatar } from "@/components/person-avatar";
import { api, type PersonDetail, type PersonMessagesPage } from "@/lib/api";
import { channelDisplayName, emptyBodyLabel } from "@/lib/channels";
import { cn } from "@/lib/utils";

/**
 * One contact's whole history, which the contact page shows the last ten of.
 *
 * Server-rendered with the filters in the URL rather than a client component
 * with state: every view of this page — "everything they sent me about the
 * fund", page 4 of a two-year thread — is then a link you can keep, send to
 * yourself, or reload without losing your place.
 */

export const dynamic = "force-dynamic";

const PAGE_SIZE = 50;

async function fetchAll(id: string, params: { offset: number; channel?: string; q?: string }) {
  const cookieHeader = (await cookies()).toString();
  try {
    const [person, page] = await Promise.all([
      api.getPerson(id, { cookieHeader }),
      api.personMessages(id, { limit: PAGE_SIZE, ...params }, { cookieHeader }),
    ]);
    return { person, page } as { person: PersonDetail; page: PersonMessagesPage };
  } catch {
    return null;
  }
}

export default async function PersonMessagesRoute({
  params, searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ offset?: string; channel?: string; q?: string }>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const offset = Math.max(0, parseInt(sp.offset ?? "0", 10) || 0);
  const channel = sp.channel?.trim() || undefined;
  const q = sp.q?.trim() || undefined;

  const data = await fetchAll(id, { offset, channel, q });
  if (!data) notFound();
  const { person, page } = data;

  // Filters live in the URL, so every control is a link and paging keeps
  // whatever filter you're under.
  const href = (next: { offset?: number; channel?: string | null; q?: string | null }) => {
    const u = new URLSearchParams();
    const o = next.offset ?? 0;
    if (o > 0) u.set("offset", String(o));
    const c = next.channel === undefined ? channel : next.channel;
    if (c) u.set("channel", c);
    const s = next.q === undefined ? q : next.q;
    if (s) u.set("q", s);
    const qs = u.toString();
    return `/persons/${id}/messages${qs ? `?${qs}` : ""}`;
  };

  const shown = page.messages.length;
  const hasPrev = offset > 0;
  const hasNext = offset + shown < page.total;
  const filtered = Boolean(channel || q);

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-4xl">
        <Link href={`/persons/${id}`}
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mb-4 transition-colors">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to {person.display_name}
        </Link>

        <header className="mb-4 flex items-start gap-3">
          <PersonAvatar personId={id} displayName={person.display_name} className="h-10 w-10 shrink-0" />
          <div className="min-w-0">
            <h1 className="text-xl sm:text-2xl font-semibold tracking-tight truncate">
              {person.display_name}
            </h1>
            <p className="text-xs text-muted-foreground mt-1 tabular">
              <span className="text-foreground font-medium">{page.total.toLocaleString()}</span>
              {filtered ? " matching" : ""} message{page.total === 1 ? "" : "s"}
              {shown > 0 && page.total > shown && (
                <> · showing {offset + 1}–{offset + shown}</>
              )}
            </p>
          </div>
        </header>

        {/* Search. A plain GET form: no client JS, and the result is a URL. */}
        <form action={`/persons/${id}/messages`} method="get" className="flex items-center gap-2 mb-3">
          {channel && <input type="hidden" name="channel" value={channel} />}
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <input
              type="search" name="q" defaultValue={q ?? ""}
              placeholder={`Search everything ${person.display_name} and you have said…`}
              className="w-full h-8 pl-8 pr-2 rounded-md border border-border bg-background text-sm"
            />
          </div>
          <button type="submit"
            className="h-8 px-3 rounded-md border border-border text-sm hover:bg-accent">
            Search
          </button>
          {q && (
            <Link href={href({ offset: 0, q: null })}
              className="h-8 px-3 grid place-items-center rounded-md border border-border text-sm text-muted-foreground hover:bg-accent">
              Clear
            </Link>
          )}
        </form>

        {/* Channel filter. Counts are under the current search, so a chip
            says what it would actually show. */}
        {page.channels.length > 1 && (
          <div className="flex items-center gap-1 flex-wrap text-xs mb-4">
            <Link href={href({ offset: 0, channel: null })}
              className={cn("h-7 px-2.5 grid place-items-center rounded-md border border-border",
                !channel ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50")}>
              All
            </Link>
            {page.channels.map((c) => (
              <Link key={c.channel} href={href({ offset: 0, channel: c.channel })}
                title={c.channel}
                className={cn("inline-flex items-center gap-1 h-7 px-2.5 rounded-md border border-border",
                  channel === c.channel ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50")}>
                {channelDisplayName(c.channel)}
                <span className="opacity-60 tabular">{c.count}</span>
              </Link>
            ))}
          </div>
        )}

        {page.messages.length === 0 ? (
          <div className="rounded-md border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
            {filtered
              ? "Nothing matches that. Clear the filter to see the whole history."
              : "No messages with this contact yet."}
          </div>
        ) : (
          <ul className="divide-y divide-border rounded-md border border-border overflow-hidden bg-card/40">
            {page.messages.map((m) => {
              const mine = m.direction === "outbound";
              const when = new Date(m.occurred_at);
              return (
                <li key={m.id} className="flex gap-3 px-3 sm:px-4 py-2.5">
                  <div className="shrink-0 w-24 sm:w-28 pt-0.5">
                    <div className="text-[11px] text-muted-foreground tabular">
                      {when.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" })}
                    </div>
                    <div className="text-[10px] text-muted-foreground/70 tabular">
                      {when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </div>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className={cn("text-[10px] uppercase tracking-wide rounded border px-1",
                        mine ? "text-primary border-primary/40" : "text-muted-foreground border-border")}>
                        {mine ? "you" : person.display_name}
                      </span>
                      <span className="text-[10px] text-muted-foreground/70">
                        {channelDisplayName(m.channel)}
                      </span>
                      {m.group_title && m.group_chat_id && (
                        <Link href={`/groups/${m.group_chat_id}`}
                          className="text-[10px] text-sky-400 hover:text-sky-300">
                          via {m.group_title}
                        </Link>
                      )}
                    </div>
                    {m.body ? (
                      <p className="text-sm text-foreground/90 whitespace-pre-wrap break-words">
                        {m.body}
                        {m.truncated && (
                          <span className="text-muted-foreground italic"> … (truncated)</span>
                        )}
                      </p>
                    ) : (
                      // Not a blank row: the message happened, it just had no text.
                      <p className="text-sm text-muted-foreground italic">
                        {emptyBodyLabel(m.channel)}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {(hasPrev || hasNext) && (
          <nav className="flex items-center justify-between gap-2 mt-4">
            <Link href={hasPrev ? href({ offset: Math.max(0, offset - PAGE_SIZE) }) : "#"}
              aria-disabled={!hasPrev}
              className={cn("inline-flex items-center gap-1 h-8 px-3 rounded-md border border-border text-xs",
                hasPrev ? "hover:bg-accent" : "opacity-40 pointer-events-none")}>
              <ChevronLeft className="h-3.5 w-3.5" /> Newer
            </Link>
            <span className="text-[11px] text-muted-foreground tabular">
              {Math.floor(offset / PAGE_SIZE) + 1} of {Math.max(1, Math.ceil(page.total / PAGE_SIZE))}
            </span>
            <Link href={hasNext ? href({ offset: offset + PAGE_SIZE }) : "#"}
              aria-disabled={!hasNext}
              className={cn("inline-flex items-center gap-1 h-8 px-3 rounded-md border border-border text-xs",
                hasNext ? "hover:bg-accent" : "opacity-40 pointer-events-none")}>
              Older <ChevronRight className="h-3.5 w-3.5" />
            </Link>
          </nav>
        )}
      </div>
    </AppShell>
  );
}
