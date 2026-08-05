import { cookies } from "next/headers";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, Briefcase } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { ProjectMembers } from "@/components/project-members";
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

export default async function ProjectDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const budgetOnly = (await cookies()).get("merge_role")?.value === "budget";
  const data = await fetchData(slug, budgetOnly);
  if (!data) notFound();
  const { project, tasks, opps, allProjects, recaps } = data;

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-5xl">
        <Link href="/projects" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mb-4 transition-colors">
          <ArrowLeft className="h-3.5 w-3.5" />
          All projects
        </Link>

        <header className="mb-6 flex items-start gap-3">
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
            <p className="text-[11px] text-muted-foreground/70 mt-1 font-mono">
              slug: {project.slug}
            </p>
          </div>
        </header>

        <ProjectMembers projectId={project.id} initial={project.members} canEdit={!budgetOnly} />

        {recaps.length > 0 && (
          <section className="mb-6">
            <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
              Meeting recaps ({recaps.length})
            </h2>
            <ul className="space-y-2">
              {recaps.map((r) => (
                <li key={r.id} className="rounded-md border border-border bg-card/40 p-3">
                  <div className="flex items-baseline justify-between gap-2 mb-1">
                    <span className="text-sm font-medium truncate">{r.title || "(untitled meeting)"}</span>
                    {r.meeting_date && (
                      <span className="text-[11px] text-muted-foreground tabular shrink-0">
                        {new Date(r.meeting_date).toLocaleDateString("en", { year: "numeric", month: "short", day: "numeric" })}
                      </span>
                    )}
                  </div>
                  {r.recap && <p className="text-xs text-foreground/80 leading-relaxed">{r.recap}</p>}
                  {r.attendees && r.attendees.length > 0 && (
                    <div className="mt-1.5 text-[11px] text-muted-foreground truncate">
                      {r.attendees.map((a) => a.name || a.email).filter(Boolean).join(", ")}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="mb-6">
          <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
            Tasks ({tasks.length})
          </h2>
          {budgetOnly
            ? <ProjectTasksBudget projectId={project.id} initial={tasks} />
            : <TasksClient initialTasks={tasks} projects={allProjects} projectId={project.id} />}
        </section>

        {!budgetOnly && (
          <section>
            <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">
              Opportunities ({opps.length})
            </h2>
            <OpportunitiesClient initialOpps={opps} projects={allProjects} currentStage={undefined} />
          </section>
        )}
      </div>
    </AppShell>
  );
}
