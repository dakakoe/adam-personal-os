import { AppShell } from "@/components/app-shell";
import { FollowupsClient } from "@/components/followups-client";

export const dynamic = "force-dynamic";

export default function FollowupsPage() {
  return (
    <AppShell>
      <div className="max-w-3xl mx-auto w-full p-4 sm:p-6">
        <FollowupsClient />
      </div>
    </AppShell>
  );
}
