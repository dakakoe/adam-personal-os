import { cookies } from "next/headers";
import { CheckSquare } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type TaskRow, type ProjectRow } from "@/lib/api";
import { TasksClient } from "@/components/tasks-client";

async function fetchData() {
  const cookie = (await cookies()).toString();
  try {
    // Fetch ALL tasks (status/project/search filtering happens client-side so
    // the due-date buckets + filters update instantly).
    const [tasks, projects] = await Promise.all([
      api.listTasks({ limit: 300 }, { cookieHeader: cookie }),
      api.listProjects({}, { cookieHeader: cookie }),
    ]);
    return { tasks, projects };
  } catch {
    return { tasks: [] as TaskRow[], projects: [] as ProjectRow[] };
  }
}

export default async function TasksPage() {
  const { tasks, projects } = await fetchData();

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-5xl">
        <header className="mb-4 sm:mb-6">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <CheckSquare className="h-5 w-5 text-muted-foreground" />
            Tasks
          </h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-1">
            Grouped by due date — Overdue, Today, This week, Later. Toggle to group by project.
          </p>
        </header>

        <TasksClient initialTasks={tasks} projects={projects} />
      </div>
    </AppShell>
  );
}
