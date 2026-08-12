import { cookies } from "next/headers";
import Link from "next/link";
import { Briefcase, Plus } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type ProjectRow } from "@/lib/api";
import { CreateProjectButton } from "@/components/create-project-button";

async function fetchProjects(status: string | undefined): Promise<ProjectRow[]> {
  const cookie = (await cookies()).toString();
  try {
    return await api.listProjects({ status }, { cookieHeader: cookie });
  } catch {
    return [];
  }
}

export default async function ProjectsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const sp = await searchParams;
  const projects = await fetchProjects(sp.status);
  const budgetOnly = (await cookies()).get("merge_role")?.value === "budget";

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-5xl">
        <header className="mb-4 sm:mb-6 flex flex-wrap items-baseline justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
              <Briefcase className="h-5 w-5 text-muted-foreground" />
              Projects
            </h1>
            <p className="text-xs sm:text-sm text-muted-foreground mt-1">
              {projects.length} {sp.status ?? "all"}.
            </p>
          </div>
          {!budgetOnly && <CreateProjectButton />}
        </header>

        {/* Status filter chips */}
        <div className="mb-3 flex flex-wrap gap-2 text-xs">
          {(["", "active", "paused", "archived"] as const).map((s) => (
            <Link
              key={s || "all"}
              href={s ? `/projects?status=${s}` : "/projects"}
              className={`inline-flex items-center h-7 px-2.5 rounded-md border ${
                (sp.status ?? "") === s
                  ? "border-primary text-foreground bg-accent/40"
                  : "border-border text-muted-foreground hover:text-foreground hover:bg-accent/20"
              }`}
            >
              {s || "All"}
            </Link>
          ))}
        </div>

        {projects.length === 0 ? (
          <div className="rounded-md border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">
            No projects. Create one to start tracking work.
          </div>
        ) : (
          <ul className="grid sm:grid-cols-2 gap-3">
            {projects.map((p) => (
              <li key={p.id}>
                <Link
                  href={`/projects/${p.slug}`}
                  className="flex h-full flex-col rounded-lg border border-border bg-card/40 p-4 hover:border-primary/40 hover:bg-card transition-colors"
                >
                  <div className="flex items-center gap-2 mb-2">
                    <span
                      aria-hidden
                      className="h-3 w-3 rounded-full shrink-0"
                      style={{ backgroundColor: p.color ?? "var(--color-muted-foreground)" }}
                    />
                    <h2 className="font-semibold tracking-tight truncate flex-1">{p.name}</h2>
                    <span className="text-[10px] font-mono uppercase text-muted-foreground">{p.status}</span>
                  </div>
                  {p.description && (
                    <p className="text-xs text-muted-foreground line-clamp-2 mb-3">{p.description}</p>
                  )}
                  <div className="mt-auto pt-1 flex items-center gap-4 text-xs text-muted-foreground tabular">
                    <span><span className="text-foreground font-medium">{p.open_task_count}</span> open tasks</span>
                    {!budgetOnly && <span><span className="text-foreground font-medium">{p.live_opp_count}</span> live opps</span>}
                    <span><span className="text-foreground font-medium">{p.member_count}</span> members</span>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </AppShell>
  );
}
