import { cookies } from "next/headers";
import { AppShell } from "@/components/app-shell";
import { MergeQueue } from "@/components/merge-queue";
import { api, type MergeCandidate } from "@/lib/api";

async function fetchCandidates(): Promise<MergeCandidate[]> {
  const cookie = (await cookies()).toString();
  try {
    return await api.listCandidates({ limit: 50 }, { cookieHeader: cookie });
  } catch {
    return [];
  }
}

async function fetchFocused(id: number): Promise<MergeCandidate | null> {
  const cookie = (await cookies()).toString();
  try {
    return await api.getCandidate(id, { cookieHeader: cookie });
  } catch {
    return null;
  }
}

export default async function MergePage({
  searchParams,
}: {
  searchParams: Promise<{ focus?: string }>;
}) {
  const { focus } = await searchParams;
  const focusId = focus ? parseInt(focus, 10) : null;

  const [candidates, focused] = await Promise.all([
    fetchCandidates(),
    focusId ? fetchFocused(focusId) : Promise.resolve(null),
  ]);

  // If a focused candidate exists, move it to the front of the queue (dedup
  // if it's already there).
  let queue = candidates;
  if (focused) {
    queue = [focused, ...candidates.filter((c) => c.id !== focused.id)];
  }

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-6xl">
        <header className="mb-4 sm:mb-6">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight">Merge queue</h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-1">
            {queue.length} candidate pair{queue.length === 1 ? "" : "s"} pending.
            {/* Keyboard hints only on desktop where a keyboard exists. */}
            <span className="hidden sm:inline">
              {" "}
              <kbd className="font-mono text-[10px] px-1 py-0.5 rounded border border-border bg-muted">y</kbd>{" "}
              approve ·{" "}
              <kbd className="font-mono text-[10px] px-1 py-0.5 rounded border border-border bg-muted">n</kbd>{" "}
              reject ·{" "}
              <kbd className="font-mono text-[10px] px-1 py-0.5 rounded border border-border bg-muted">s</kbd>{" "}
              defer
            </span>
          </p>
        </header>

        <MergeQueue initial={queue} />
      </div>
    </AppShell>
  );
}
