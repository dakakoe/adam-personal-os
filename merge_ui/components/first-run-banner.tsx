"use client";

// First-run nudge (setup wizard): shown to the admin while NO source has any
// data yet. Self-hides forever once anything ingests, or on dismiss.

import { useEffect, useState } from "react";
import { Sparkles, X } from "lucide-react";
import { api } from "@/lib/api";

const DISMISS_KEY = "firstRunDismissed";

export function FirstRunBanner() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || localStorage.getItem(DISMISS_KEY)) return;
    Promise.all([api.getMe(), api.getSources()])
      .then(([me, s]) => {
        const empty = s.sources.length > 0 && s.sources.every((x) => !x.count);
        setShow(me.role === "admin" && empty);
      })
      .catch(() => {});
  }, []);

  if (!show) return null;
  return (
    <div className="mb-4 rounded-lg border border-primary/30 bg-primary/10 px-4 py-3 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2 text-sm">
        <Sparkles className="h-4 w-4 text-primary shrink-0" />
        <span>
          Nothing connected yet — walk through the{" "}
          <a href="/setup" className="font-medium underline">setup wizard</a> to
          bring your mail, chats, and meetings in.
        </span>
      </div>
      <button type="button" aria-label="Dismiss"
        onClick={() => { localStorage.setItem(DISMISS_KEY, "1"); setShow(false); }}
        className="text-muted-foreground hover:text-foreground shrink-0">
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
