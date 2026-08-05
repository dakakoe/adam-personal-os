import { cookies } from "next/headers";
import { Settings2 } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type StageConfig } from "@/lib/api";
import { StagesManager } from "@/components/stages-manager";

export default async function StagesPage() {
  const cookie = (await cookies()).toString();
  let stages: StageConfig[] = [];
  try {
    stages = await api.listStages({ cookieHeader: cookie });
  } catch {
    // manager shows an empty state; the client refetches on mutation
  }

  return (
    <AppShell>
      <div className="mb-5">
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <Settings2 className="h-5 w-5 text-muted-foreground" /> Deal stages
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Add, rename, recolor and reorder the funnel columns. Changes apply everywhere immediately.
        </p>
      </div>
      <StagesManager initial={stages} />
    </AppShell>
  );
}
