"use client";

import { useEffect, useState } from "react";
import { ArrowUpCircle, X } from "lucide-react";
import { api, type VersionInfo } from "@/lib/api";

/**
 * "A newer ADAM is available" — shown to anyone running a published snapshot
 * when the public mirror cuts a newer Release.
 *
 * Invisible unless there's genuinely something to do: a dev/source checkout has
 * no VERSION file, and an offline/rate-limited check reports no update, so this
 * renders nothing rather than nagging. Dismissal is remembered per version, so
 * skipping one release doesn't silence the next.
 */
export function UpdateBanner() {
  const [info, setInfo] = useState<VersionInfo | null>(null);
  const [dismissed, setDismissed] = useState(true);   // assume hidden until we know

  useEffect(() => {
    let cancel = false;
    api.getVersion()
      .then((v) => {
        if (cancel || !v.update_available) return;
        setInfo(v);
        setDismissed(localStorage.getItem("adam.update.dismissed") === v.latest);
      })
      .catch(() => { /* update checks never surface errors */ });
    return () => { cancel = true; };
  }, []);

  if (!info?.update_available || dismissed) return null;

  return (
    <div className="mb-3 rounded-lg border border-primary/40 bg-primary/5 px-3 py-2 text-sm">
      <div className="flex items-start gap-2">
        <ArrowUpCircle className="h-4 w-4 mt-0.5 text-primary shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="font-medium">
            ADAM {info.latest} is available
            <span className="ml-1.5 font-normal text-muted-foreground">
              (you&apos;re on {info.current})
            </span>
          </div>
          {info.notes && (
            <p className="mt-0.5 text-xs text-muted-foreground whitespace-pre-line line-clamp-4">
              {info.notes}
            </p>
          )}
          <div className="mt-1.5 flex items-center gap-3 text-xs">
            {info.url && (
              <a href={info.url} target="_blank" rel="noopener noreferrer"
                 className="text-primary hover:underline">
                Release notes →
              </a>
            )}
            <code className="rounded bg-muted px-1.5 py-0.5 text-[11px]">git pull &amp;&amp; ./deploy.sh</code>
          </div>
        </div>
        <button
          type="button"
          title="Dismiss until the next release"
          onClick={() => {
            if (info.latest) localStorage.setItem("adam.update.dismissed", info.latest);
            setDismissed(true);
          }}
          className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
