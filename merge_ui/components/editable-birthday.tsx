"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Cake, Check, X, Pencil, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { formatBirthday } from "@/lib/utils";

/**
 * Inline-editable birthday on the person card. Shows 🎂 + the formatted date
 * when set (with a hover pencil); an "Add birthday" affordance when not. Click
 * swaps in a native date input. Saves via PUT /api/persons/{id}/birthday, which
 * writes the manual override on canonical.person — this takes precedence over
 * any Telegram/Google-sourced birthday. Clearing falls back to the source.
 */
export function EditableBirthday({
  personId,
  birthday,
}: {
  personId: string;
  birthday: string | null | undefined;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(toInputDate(birthday));
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setValue(toInputDate(birthday)); }, [birthday]);
  useEffect(() => {
    if (editing) requestAnimationFrame(() => inputRef.current?.showPicker?.());
  }, [editing]);

  const label = formatBirthday(birthday);

  async function save(next: string | null) {
    setBusy(true);
    try {
      await api.setPersonBirthday(personId, next);
      toast.success(next ? "Birthday saved" : "Birthday cleared");
      setEditing(false);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <span className="inline-flex items-center gap-1">
        <Cake className="h-3 w-3 shrink-0" />
        <input
          ref={inputRef}
          type="date"
          value={value}
          disabled={busy}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save(value || null);
            if (e.key === "Escape") { setValue(toInputDate(birthday)); setEditing(false); }
          }}
          className="h-6 rounded border border-border bg-background px-1 text-xs tabular [color-scheme:dark]"
        />
        <button type="button" title="Save" disabled={busy || !value}
          onClick={() => save(value || null)}
          className="grid place-items-center h-5 w-5 rounded text-emerald-400 hover:bg-emerald-500/10 disabled:opacity-40">
          <Check className="h-3.5 w-3.5" />
        </button>
        {label && (
          <button type="button" title="Clear birthday" disabled={busy}
            onClick={() => save(null)}
            className="grid place-items-center h-5 w-5 rounded text-muted-foreground hover:bg-accent">
            <X className="h-3.5 w-3.5" />
          </button>
        )}
        <button type="button" title="Cancel" disabled={busy}
          onClick={() => { setValue(toInputDate(birthday)); setEditing(false); }}
          className="grid place-items-center h-5 w-5 rounded text-muted-foreground hover:bg-accent">
          <X className="h-3.5 w-3.5" />
        </button>
      </span>
    );
  }

  if (!label) {
    return (
      <button type="button" onClick={() => setEditing(true)}
        title="Add a birthday"
        className="inline-flex items-center gap-1 text-muted-foreground/70 hover:text-foreground transition-colors">
        <Plus className="h-3 w-3" /> Add birthday
      </button>
    );
  }

  return (
    <button type="button" onClick={() => setEditing(true)}
      title="Edit birthday"
      className="group/bday inline-flex items-center gap-1 hover:text-foreground transition-colors">
      <Cake className="h-3 w-3" />
      {label}
      <Pencil className="h-3 w-3 opacity-0 group-hover/bday:opacity-100 transition-opacity" />
    </button>
  );
}

/** ISO (possibly with a time part or a 1900 sentinel) → YYYY-MM-DD for the input. */
function toInputDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return iso.split("T")[0].slice(0, 10);
}
