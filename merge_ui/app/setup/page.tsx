import { Sparkles } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { SetupClient } from "./setup-client";

// First-run setup wizard (Phase 4 remainder / Phase 6 friend-install prep):
// a guided, skippable, refresh-safe walk through connecting every source.
// All state is DERIVED live from /api/sources — nothing persisted.
export default function SetupPage() {
  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-3xl">
        <header className="mb-4 sm:mb-6">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-muted-foreground" />
            Setup
          </h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-1">
            Connect your sources one by one. Every step is skippable — come back any time; progress is read live, nothing is lost on refresh.
          </p>
        </header>
        <SetupClient />
      </div>
    </AppShell>
  );
}
