"use client";

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { useState } from "react";

export function RemoveIdentityConfirm({
  open, onOpenChange, personId, identityId, source, value, onRemoved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  personId: string;
  identityId: number;
  source: string;
  value: string;
  onRemoved: () => void;
}) {
  const [busy, setBusy] = useState(false);

  async function onConfirm() {
    setBusy(true);
    try {
      await api.removeIdentity(personId, identityId);
      toast.success(`Removed ${source}: ${value}`);
      onRemoved();
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Remove failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove this identity?</AlertDialogTitle>
          <AlertDialogDescription className="space-y-2">
            <span className="block">
              You&apos;re about to unlink{" "}
              <span className="font-mono text-foreground">{value}</span>{" "}
              ({source}) from this person.
            </span>
            <span className="block text-xs text-muted-foreground">
              Future messages from this identifier will create a new canonical
              person until you re-link them. Past interactions stay attached to
              this person.
            </span>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Keep</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            disabled={busy}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {busy ? "Removing…" : "Remove"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
