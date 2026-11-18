import { AppShell } from "@/components/app-shell";
import { CirclesClient } from "@/components/circles-client";

export const dynamic = "force-dynamic";

export default function CirclesPage() {
  return (
    <AppShell>
      <div className="max-w-3xl mx-auto w-full">
        <CirclesClient />
      </div>
    </AppShell>
  );
}
