"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import type { OpportunityStage } from "@/lib/api";
import { useStages, stageLabel } from "@/lib/stages";

/** Shared "Move to {stage}" dialog — captures the next step + note that get
 *  recorded on the opportunity timeline. Used by the detail panel and the
 *  funnel board (drag → drop). Open when `stage` is non-null. */
export function StageChangeDialog({
  stage, title, onCancel, onConfirm,
}: {
  stage: OpportunityStage | null;
  title?: string | null;
  onCancel: () => void;
  onConfirm: (next_step?: string, note?: string) => void | Promise<void>;
}) {
  const stages = useStages();
  const [next, setNext] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (stage) { setNext(""); setNote(""); } }, [stage]);

  async function confirm() {
    setBusy(true);
    try { await onConfirm(next.trim() || undefined, note.trim() || undefined); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open={stage !== null} onOpenChange={(o) => { if (!o) onCancel(); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>Move to “{stage ? stageLabel(stages, stage) : ""}”</DialogTitle></DialogHeader>
        {title && <p className="text-xs text-muted-foreground -mt-1 truncate">{title}</p>}
        <p className="text-xs text-muted-foreground">What&apos;s the next step? (recorded on the timeline)</p>
        <div className="space-y-2">
          <Input autoFocus value={next} onChange={(e) => setNext(e.target.value)} placeholder="Next step…" />
          <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button onClick={confirm} disabled={busy}>Confirm move</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
