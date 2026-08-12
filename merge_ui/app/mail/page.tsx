import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import Link from "next/link";
import { Mail, Sparkles } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type MailThreadRow, type MailAccount } from "@/lib/api";
import { MailClient } from "@/components/mail-client";

// Backlog #2 Phase 1 — read-only mail. Admin-only (budget users never reach the
// CRM/mail; bounce them to budget).
export default async function MailPage() {
  if ((await cookies()).get("merge_role")?.value === "budget") redirect("/budget");
  const cookie = (await cookies()).toString();
  let threads: MailThreadRow[] = [];
  let accounts: MailAccount[] = [];
  try {
    [threads, accounts] = await Promise.all([
      api.listMailThreads({ limit: 50 }, { cookieHeader: cookie }),
      api.listMailAccounts({ cookieHeader: cookie }).catch(() => []),
    ]);
  } catch { /* empty */ }
  return (
    <AppShell>
      {/* full-width: mail is a three-pane workspace, not a document — no max-w cap.
          On desktop the page is pinned to the viewport height (a flex column) so
          the three panes scroll independently instead of scrolling the whole page;
          mobile keeps natural page scroll since only one pane shows at a time. */}
      <div className="p-4 sm:p-6 w-full md:h-dvh md:flex md:flex-col md:overflow-hidden">
        <header className="mb-4 md:shrink-0 flex items-center justify-between gap-2">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Mail className="h-5 w-5 text-muted-foreground" /> Mail
          </h1>
          <Link href="/mail/cleanup"
            className="inline-flex items-center gap-1.5 text-sm px-2.5 h-9 rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-accent/40 transition-colors">
            <Sparkles className="h-4 w-4 text-amber-400" /> Cleanup
          </Link>
        </header>
        <MailClient initialThreads={threads} accounts={accounts} />
      </div>
    </AppShell>
  );
}
