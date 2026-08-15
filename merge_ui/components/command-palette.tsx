"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command";
import { api, type PersonRow } from "@/lib/api";

export function CommandPalette({
  open, onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<PersonRow[]>([]);

  useEffect(() => {
    if (!open) return;
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const data = await api.listPersons({ q: q || undefined, limit: 20 });
        if (!ctrl.signal.aborted) setRows(data);
      } catch {
        /* ignore — empty list is fine */
      }
    }, 80);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [q, open]);

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput
        placeholder="Search people by name…"
        value={q}
        onValueChange={setQ}
      />
      <CommandList>
        <CommandEmpty>No matches</CommandEmpty>
        <CommandGroup heading="People">
          {rows.map((p) => (
            <CommandItem
              key={p.person_id}
              value={`${p.display_name} ${p.telegram_username ?? ""} ${p.email ?? ""}`}
              onSelect={() => {
                onOpenChange(false);
                router.push(`/persons/${p.person_id}`);
              }}
              className="flex items-center justify-between gap-3"
            >
              <span className="truncate">{p.display_name}</span>
              <span className="text-xs text-muted-foreground tabular">
                {p.total_interactions.toLocaleString()} msg
              </span>
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
