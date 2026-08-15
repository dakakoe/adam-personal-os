import { cookies } from "next/headers";
import { Target } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type OpportunityRow, type ProjectRow, type PipelineSummary } from "@/lib/api";
import { OpportunitiesClient } from "@/components/opportunities-client";

function fmtUsd(n: number): string {
  if (!n) return "$0";
  if (n >= 1_000_000) return `$${(n / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 2 })}M`;
  if (n >= 1_000) return `$${(n / 1_000).toLocaleString(undefined, { maximumFractionDigits: 1 })}k`;
  return `$${n.toLocaleString()}`;
}

async function fetchData(project_id: string | undefined) {
  const cookie = (await cookies()).toString();
  try {
    // Fetch ALL opps (no stage filter) — the board needs every column; the
    // list view filters by the stage chip client-side.
    const [opps, projects, pipeline] = await Promise.all([
      api.listOpportunities({ project_id, limit: 300 }, { cookieHeader: cookie }),
      api.listProjects({}, { cookieHeader: cookie }),
      api.getPipeline({ project_id }, { cookieHeader: cookie }),
    ]);
    return { opps, projects, pipeline };
  } catch {
    return { opps: [] as OpportunityRow[], projects: [] as ProjectRow[], pipeline: null as PipelineSummary | null };
  }
}

export default async function OpportunitiesPage({
  searchParams,
}: {
  searchParams: Promise<{ stage?: string; project_id?: string; view?: string }>;
}) {
  const sp = await searchParams;
  const view = sp.view === "list" ? "list" : "board";
  const { opps, projects, pipeline } = await fetchData(sp.project_id);

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-7xl">
        <header className="mb-4 sm:mb-6">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Target className="h-5 w-5 text-muted-foreground" />
            Opportunities
          </h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-1">
            {opps.length} total{sp.stage ? ` in stage "${sp.stage}"` : ""}. Custom stages: intro → conversations → MoU → contract → active (or lost).
          </p>
        </header>

        {/* Pipeline rollup */}
        {pipeline && pipeline.total_count > 0 && (
          <div className="mb-4 rounded-lg border border-border bg-card/60 p-3 sm:p-4">
            <div className="flex items-baseline justify-between mb-2">
              <span className="text-xs uppercase tracking-wider text-muted-foreground">Live pipeline</span>
              <span className="text-sm">
                <span className="font-semibold tabular">{fmtUsd(pipeline.total_usd)}</span>
                <span className="text-muted-foreground"> · {pipeline.total_count} deals</span>
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {pipeline.by_stage.filter((s) => s.count > 0).map((s) => (
                <span key={s.stage} className="inline-flex items-center gap-1.5 text-xs rounded-md border border-border bg-secondary/40 px-2 py-1">
                  <span className="text-muted-foreground">{s.stage}</span>
                  <span className="tabular">{s.count}</span>
                  {s.usd > 0 && <span className="text-foreground tabular">{fmtUsd(s.usd)}</span>}
                </span>
              ))}
            </div>
          </div>
        )}

        <OpportunitiesClient initialOpps={opps} projects={projects} currentStage={sp.stage} view={view} />
      </div>
    </AppShell>
  );
}
