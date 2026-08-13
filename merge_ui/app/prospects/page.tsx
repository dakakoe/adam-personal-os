import { AppShell } from "@/components/app-shell";
import { ProspectsClient } from "@/components/prospects-client";

export const dynamic = "force-dynamic";

export default function ProspectsPage() {
  return (
    <AppShell>
      <div className="max-w-4xl mx-auto w-full">
        <ProspectsClient />
      </div>
    </AppShell>
  );
}
