import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import Link from "next/link";
import { Sparkles, ChevronLeft } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type MailAccount } from "@/lib/api";
import { MailCleanupClient } from "@/components/mail-cleanup-client";

// Mail Cleanup — recommend + bulk-clear notification noise. Admin-only, like the
// rest of /mail (budget users bounce to budget).
export default async function MailCleanupPage() {
  if ((await cookies()).get("merge_role")?.value === "budget") redirect("/budget");
  const cookie = (await cookies()).toString();
  let accounts: MailAccount[] = [];
  try {
    accounts = await api.listMailAccounts({ cookieHeader: cookie });
  } catch { /* empty */ }
  return (
    <AppShell>
      <div className="p-4 sm:p-6 w-full max-w-5xl mx-auto">
        <header className="mb-4">
          <Link href="/mail" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mb-2">
            <ChevronLeft className="h-3.5 w-3.5" /> Mail
          </Link>
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-muted-foreground" /> Mail Cleanup
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Clear notification noise fast. Pick the senders you don&apos;t need, unsubscribe,
            and move everything to Gmail Trash (recoverable for 30 days).
          </p>
        </header>
        <MailCleanupClient accounts={accounts} />
      </div>
    </AppShell>
  );
}
