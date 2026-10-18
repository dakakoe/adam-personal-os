import { cookies } from "next/headers";
import { Tags } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { api, type StreamRow } from "@/lib/api";
import { StreamsManager } from "@/components/streams-manager";

export default async function StreamsPage() {
  const cookie = (await cookies()).toString();
  let streams: StreamRow[] = [];
  try {
    streams = await api.listStreams({ cookieHeader: cookie });
  } catch {
    // manager shows an empty state; the client refetches on mutation
  }

  return (
    <AppShell>
      <div className="mb-5">
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <Tags className="h-5 w-5 text-muted-foreground" /> Streams
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          The pipelines sharing one board — BD, a job hunt, a venture. Add, rename and
          retire them; changes apply to every deal immediately.
        </p>
      </div>
      <StreamsManager initial={streams} />
    </AppShell>
  );
}
