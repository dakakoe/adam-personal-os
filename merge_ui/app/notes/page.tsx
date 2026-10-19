import { Suspense } from "react";
import { AppShell } from "@/components/app-shell";
import { NotesClient } from "@/components/notes-client";

export const dynamic = "force-dynamic";

export default function NotesPage() {
  return (
    <AppShell>
      <Suspense fallback={<p className="p-6 text-sm text-muted-foreground">Loading…</p>}>
        <NotesClient />
      </Suspense>
    </AppShell>
  );
}
