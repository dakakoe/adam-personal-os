import { cookies } from "next/headers";
import { ClipboardList } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { PlanClient } from "@/components/plan-client";
import { api, type TaskRow, type OpportunityRow, type ProjectRow } from "@/lib/api";

async function fetchData() {
  const cookie = (await cookies()).toString();
  const opts = { cookieHeader: cookie };
  try {
    const [open, doing, opps, projects] = await Promise.all([
      api.listTasks({ status: "open", limit: 200 }, opts),
      api.listTasks({ status: "doing", limit: 200 }, opts),
      api.listOpportunities({ limit: 200 }, opts),
      api.listProjects({}, opts),
    ]);
    const tasks = [...doing, ...open];
    const liveOpps = opps.filter((o) => o.stage !== "lost" && !o.closed_at);
    return { tasks, opps: liveOpps, projects };
  } catch {
    return { tasks: [] as TaskRow[], opps: [] as OpportunityRow[], projects: [] as ProjectRow[] };
  }
}

export default async function PlanPage() {
  const { tasks, opps, projects } = await fetchData();

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-6xl">
        <header className="mb-4 sm:mb-6">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <ClipboardList className="h-5 w-5 text-muted-foreground" />
            Plan
          </h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-1">
            Everything open, organized by project — tasks on the left, deals on the right. Quick-add under any project, or drag a task or deal between projects to reassign it.
          </p>
        </header>

        <PlanClient tasks={tasks} opps={opps} projects={projects} />
      </div>
    </AppShell>
  );
}
