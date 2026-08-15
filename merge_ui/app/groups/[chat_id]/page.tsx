import { cookies } from "next/headers";
import Link from "next/link";
import { notFound } from "next/navigation";
import {
  ChevronLeft, ChevronRight, Send, Megaphone, Users as UsersIcon, MessageSquare,
  ArrowLeft,
} from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { PersonAvatar } from "@/components/person-avatar";
import { api } from "@/lib/api";
import { formatRelativeDate } from "@/lib/utils";

const PAGE_SIZE = 50;

async function fetchData(chatId: number, offset: number) {
  const cookie = (await cookies()).toString();
  try {
    return await api.getTelegramGroup(
      chatId,
      { limit: PAGE_SIZE, offset },
      { cookieHeader: cookie },
    );
  } catch {
    return null;
  }
}

export default async function GroupDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ chat_id: string }>;
  searchParams: Promise<{ offset?: string }>;
}) {
  const { chat_id } = await params;
  const sp = await searchParams;
  const chatId = parseInt(chat_id, 10);
  if (!Number.isFinite(chatId)) notFound();
  const offset = Math.max(0, parseInt(sp.offset ?? "0", 10) || 0);

  const data = await fetchData(chatId, offset);
  if (!data) notFound();
  const { group, senders, recent_messages } = data;

  const hasPrev = offset > 0;
  // We don't know total from this endpoint; use msg_count from group meta.
  const hasNext = offset + recent_messages.length < group.msg_count;
  const prevHref = hasPrev
    ? `/groups/${chatId}?offset=${Math.max(0, offset - PAGE_SIZE)}`
    : "#";
  const nextHref = hasNext
    ? `/groups/${chatId}?offset=${offset + PAGE_SIZE}`
    : "#";

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-5xl">
        <Link
          href="/groups"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mb-4 transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          All groups
        </Link>

        <header className="mb-4 sm:mb-6">
          <div className="flex items-start gap-3">
            <span
              title={group.kind}
              className="grid place-items-center h-10 w-10 rounded-md bg-secondary/60 shrink-0 text-muted-foreground"
            >
              <KindIcon kind={group.kind} />
            </span>
            <div className="min-w-0 flex-1">
              <h1 className="text-xl sm:text-2xl font-semibold tracking-tight truncate">
                {group.title || `(untitled ${group.kind} ${group.chat_id})`}
              </h1>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground tabular">
                <span className="font-mono text-[10px] px-1 rounded border border-border bg-secondary/40">
                  {group.kind}
                </span>
                {group.member_count !== null && (
                  <span>{group.member_count.toLocaleString()} members</span>
                )}
                <span className={group.enabled ? "text-emerald-500" : ""}>
                  {group.enabled ? "● ingesting" : "○ disabled"}
                </span>
                <span>
                  <span className="text-foreground font-medium">
                    {group.msg_count.toLocaleString()}
                  </span>{" "}
                  messages stored
                </span>
                {group.last_message_at && (
                  <span>last message {formatRelativeDate(group.last_message_at)}</span>
                )}
              </div>
            </div>
          </div>
        </header>

        {/* Top senders */}
        {senders.length > 0 && (
          <section className="mb-6">
            <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
              Top {senders.length} senders
            </h2>
            <ul className="divide-y divide-border rounded-md border border-border overflow-hidden bg-card/40">
              {senders.map((s) => {
                const row = (
                  <div className="flex items-center gap-3 px-3 sm:px-4 py-2 hover:bg-accent/20 transition-colors">
                    {s.person_id ? (
                      <PersonAvatar
                        personId={s.person_id}
                        displayName={s.display_name}
                        className="h-7 w-7 shrink-0"
                      />
                    ) : (
                      <span className="grid place-items-center h-7 w-7 rounded-full bg-secondary/60 shrink-0 text-muted-foreground text-[10px]">
                        ?
                      </span>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate">
                        {s.display_name}
                      </div>
                      <div className="text-[11px] text-muted-foreground tabular">
                        last {formatRelativeDate(s.latest_at)} ·{" "}
                        <span className="font-mono">tg:{s.sender_telegram_id}</span>
                      </div>
                    </div>
                    <div className="text-right text-xs text-muted-foreground tabular shrink-0">
                      <span className="text-foreground font-medium">{s.msg_count.toLocaleString()}</span>
                    </div>
                  </div>
                );
                return (
                  <li key={s.sender_telegram_id}>
                    {s.person_id ? (
                      <Link href={`/persons/${s.person_id}`}>{row}</Link>
                    ) : (
                      <div title="No canonical.person mapping for this sender">{row}</div>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {/* Recent message stream */}
        <section>
          <div className="flex items-center justify-between mb-3 gap-3">
            <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Recent messages
            </h2>
            <nav className="flex items-center gap-1 shrink-0">
              <Link
                href={prevHref}
                aria-disabled={!hasPrev}
                className={`inline-flex items-center gap-1 h-7 px-2 rounded-md border border-border text-[11px] ${hasPrev ? "hover:bg-accent" : "opacity-40 pointer-events-none"}`}
              >
                <ChevronLeft className="h-3 w-3" />
                Prev
              </Link>
              <Link
                href={nextHref}
                aria-disabled={!hasNext}
                className={`inline-flex items-center gap-1 h-7 px-2 rounded-md border border-border text-[11px] ${hasNext ? "hover:bg-accent" : "opacity-40 pointer-events-none"}`}
              >
                Next
                <ChevronRight className="h-3 w-3" />
              </Link>
            </nav>
          </div>

          {recent_messages.length === 0 ? (
            <div className="rounded-md border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
              {group.msg_count === 0
                ? group.enabled
                  ? "No messages yet — enable and wait for traffic, or backfill from the groups list."
                  : "Group disabled — enable on /groups, then backfill."
                : "No messages on this page."}
            </div>
          ) : (
            <ul className="divide-y divide-border rounded-md border border-border overflow-hidden bg-card/40">
              {recent_messages.map((m) => (
                <li key={m.id} className="flex gap-3 px-3 sm:px-4 py-2.5">
                  <div className="shrink-0 w-14 text-[11px] text-muted-foreground tabular pt-0.5">
                    {formatRelativeDate(m.message_date)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs mb-0.5">
                      {m.sender_person_id ? (
                        <Link
                          href={`/persons/${m.sender_person_id}`}
                          className="font-medium text-foreground hover:text-primary"
                        >
                          {m.sender_display_name}
                        </Link>
                      ) : (
                        <span className="font-medium text-muted-foreground">{m.sender_display_name}</span>
                      )}
                      {m.kind !== "text" && (
                        <span className="ml-2 text-[10px] font-mono text-muted-foreground/70">[{m.kind}]</span>
                      )}
                    </div>
                    <p className="text-sm text-foreground/85 whitespace-pre-wrap break-words line-clamp-4">
                      {m.body_excerpt || <span className="italic text-muted-foreground/60">no text</span>}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {(hasPrev || hasNext) && (
            <nav className="mt-3 flex items-center justify-between gap-3 text-xs">
              <Link
                href={prevHref}
                aria-disabled={!hasPrev}
                className={`inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border ${hasPrev ? "hover:bg-accent" : "opacity-40 pointer-events-none"}`}
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                Prev {PAGE_SIZE}
              </Link>
              <span className="text-muted-foreground tabular">
                {(offset + 1).toLocaleString()}–{(offset + recent_messages.length).toLocaleString()} of {group.msg_count.toLocaleString()}
              </span>
              <Link
                href={nextHref}
                aria-disabled={!hasNext}
                className={`inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-border ${hasNext ? "hover:bg-accent" : "opacity-40 pointer-events-none"}`}
              >
                Next {PAGE_SIZE}
                <ChevronRight className="h-3.5 w-3.5" />
              </Link>
            </nav>
          )}
        </section>
      </div>
    </AppShell>
  );
}

function KindIcon({ kind }: { kind: string }) {
  if (kind === "channel") return <Megaphone className="h-4 w-4" />;
  if (kind === "supergroup" || kind === "group") return <UsersIcon className="h-4 w-4" />;
  return <MessageSquare className="h-4 w-4" />;
}
