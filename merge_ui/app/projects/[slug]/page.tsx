import { cookies } from "next/headers";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { ProjectMembers } from "@/components/project-members";
import { ProjectRecaps } from "@/components/project-recaps";
import { ProjectTasksBudget } from "@/components/project-tasks-budget";
import { api } from "@/lib/api";
import { TasksClient } from "@/components/tasks-client";
import { OpportunitiesClient } from "@/components/opportunities-client";

async function fetchData(slug: string, budgetOnly: boolean) {
  const cookie = (await cookies()).toString();
  const opts = { cookieHeader: cookie };
  try {
    // membership is enforced server-side; a non-member project 403s → notFound
    const project = await api.getProject(slug, opts);
    const tasks = await api.listTasks({ project_id: project.id, limit: 200 }, opts);
    const allProjects = await api.listProjects({}, opts);
    if (budgetOnly) {
      // budget role can't reach opportunities/recaps endpoints (403) — skip them
      return { project, tasks, opps: [], allProjects, recaps: [] };
    }
    const [opps, recaps] = await Promise.all([
      api.listOpportunities({ project_id: project.id, limit: 200 }, opts),
      api.listProjectRecaps(project.id, opts),
    ]);
    return { project, tasks, opps, allProjects, recaps };
  } catch {
    return null;
  }
}

type Tab = "tasks" | "opportunities" | "recaps";

export default async function ProjectDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const { slug } = await params;
  const { tab: rawTab } = await searchParams;
  const budgetOnly = (await cookies()).get("merge_role")?.value === "budget";
  const data = await fetchData(slug, budgetOnly);
  if (!data) notFound();
  const { project, tasks, opps, allProjects, recaps } = data;
  const tabs: { key: Tab; label: string; count: number }[] = budgetOnly
    ? [{ key: "tasks", label: "Tasks", count: tasks.length }]
    : [
        { key: "tasks", label: "Tasks", count: tasks.length },
        { key: "opportunities", label: "Opportunities", count: opps.length },
        { key: "recaps", label: "Meeting recaps", count: recaps.length },
      ];
  const tab: Tab = tabs.some((t) => t.key === rawTab) ? (rawTab as Tab) : "tasks";

  return (
    <AppShell>
      <div className="p-4 sm:p-6 w-full">
        <Link href="/projects" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mb-3 transition-colors">
          <ArrowLeft className="h-3.5 w-3.5" />
          All projects
        </Link>

        <header className="mb-4 flex items-start gap-3">
          <span
            aria-hidden
            className="h-10 w-10 rounded-md shrink-0"
            style={{ backgroundColor: project.color ?? "var(--color-muted-foreground)" }}
          />
          <div className="min-w-0 flex-1">
            <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
              {project.name}
              <span className="text-[10px] font-mono uppercase text-muted-foreground">{project.status}</span>
            </h1>
            {project.description && (
              <p className="text-sm text-muted-foreground mt-1">{project.description}</p>
            )}
          </div>
        </header>

        <ProjectMembers projectId={project.id} initial={project.members} canEdit={!budgetOnly} />

        {/* Tabs are links so a view is addressable (?tab=…) and survives reload. */}
        <nav className="mt-2 mb-4 flex items-center gap-1 border-b border-border" aria-label="Project sections">
          {tabs.map((t) => (
            <Link
              key={t.key}
              href={t.key === "tasks" ? `/projects/${project.slug}` : `/projects/${project.slug}?tab=${t.key}`}
              aria-current={tab === t.key ? "page" : undefined}
              className={
                "px-3 py-2 -mb-px text-sm border-b-2 transition-colors " +
                (tab === t.key
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground")
              }
            >
              {t.label} <span className="ml-1 text-[11px] text-muted-foreground tabular">{t.count}</span>
            </Link>
          ))}
        </nav>

        {tab === "tasks" && (
          budgetOnly
            ? <ProjectTasksBudget projectId={project.id} initial={tasks} />
            : <TasksClient initialTasks={tasks} projects={allProjects} projectId={project.id} />
        )}
        {tab === "opportunities" && !budgetOnly && (
          <OpportunitiesClient initialOpps={opps} projects={allProjects} currentStage={undefined} projectId={project.id} />
        )}
        {tab === "recaps" && !budgetOnly && (
          <ProjectRecaps recaps={recaps} projectId={project.id} projects={allProjects} canEdit />
        )}
      </div>
    </AppShell>
  );
}
