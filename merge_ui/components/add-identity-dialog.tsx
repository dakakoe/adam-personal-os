"use client";

import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { useState } from "react";

const SOURCES = [
  { value: "email", label: "Email" },
  { value: "linkedin", label: "LinkedIn (vanity)" },
  { value: "x", label: "X (handle)" },
  { value: "instagram", label: "Instagram (handle)" },
  { value: "github", label: "GitHub (handle)" },
  { value: "telegram_handle", label: "Telegram (@username)" },
  { value: "phone", label: "Phone (E.164)" },
  { value: "website", label: "Website" },
];

const PLACEHOLDER: Record<string, string> = {
  email: "jane@example.com",
  linkedin: "jane-doe",
  x: "janedoe",
  instagram: "janedoe",
  github: "janedoe",
  telegram_handle: "janedoe",
  phone: "+15551234567",
  website: "janedoe.com",
};

export function AddIdentityDialog({
  open, onOpenChange, personId, onAdded,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  personId: string;
  onAdded: () => void;
}) {
  const [source, setSource] = useState("email");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  function reset() {
    setSource("email");
    setValue("");
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.addIdentity(personId, { source, source_id: value.trim() });
      toast.success(`Added ${source}: ${value}`);
      reset();
      onAdded();
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Add failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add identity</DialogTitle>
          <DialogDescription>
            Attach a channel handle to this person. Linking an existing
            handle that belongs to a different person will fail — un-link it
            from the other person first.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="source">Channel</Label>
            <Select value={source} onValueChange={setSource}>
              <SelectTrigger id="source">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SOURCES.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="value">Value</Label>
            <Input
              id="value"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={PLACEHOLDER[source] ?? ""}
              autoFocus
              className="font-mono"
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !value.trim()}>
              {busy ? "Adding…" : "Add identity"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
