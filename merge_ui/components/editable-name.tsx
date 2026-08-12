"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Pencil, Check, X } from "lucide-react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

/**
 * The person card's display_name as an inline-editable h1. Hover reveals
 * a pencil affordance; click swaps the h1 for an input. Enter or the
 * check button saves via POST /api/persons/{id}/rename; Escape or X
 * cancels. Always available regardless of whether the name looks
 * synthetic — the NameSuggestionBanner is the source-driven prompt,
 * this is the universal manual escape hatch.
 */
export function EditableName({
  personId,
  displayName,
  className,
}: {
  personId: string;
  displayName: string;
  className?: string;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(displayName);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      // Defer to next tick so focus lands after the input renders.
      requestAnimationFrame(() => inputRef.current?.select());
    }
  }, [editing]);

  // Keep local state in sync if the parent re-renders with a new name
  // (e.g. after a successful rename triggers router.refresh()).
  useEffect(() => { setValue(displayName); }, [displayName]);

  async function save() {
    const clean = value.trim();
    if (!clean || clean === displayName) {
      setEditing(false);
      setValue(displayName);
      return;
    }
    setBusy(true);
    try {
      await api.renamePerson(personId, clean);
      toast.success(`Renamed to ${clean}`);
      setEditing(false);
      router.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Rename failed");
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    setValue(displayName);
    setEditing(false);
  }

  if (!editing) {
    return (
      <h1
        className={cn(
          "group/name inline-flex items-center gap-2 cursor-text",
          className,
        )}
        onClick={() => setEditing(true)}
        title="Click to rename"
      >
        <span className="truncate">{displayName}</span>
        <Pencil className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 group-hover/name:opacity-100 transition-opacity" />
      </h1>
    );
  }

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); save(); }}
      className="flex items-center gap-2"
    >
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Escape") cancel(); }}
        disabled={busy}
        className={cn(
          "min-w-0 flex-1 bg-transparent border-b-2 border-primary outline-none px-1 py-0.5",
          className,
        )}
      />
      <button
        type="submit"
        disabled={busy || !value.trim() || value.trim() === displayName}
        className="grid place-items-center h-8 w-8 rounded text-primary hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed"
        title="Save (Enter)"
      >
        <Check className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={cancel}
        disabled={busy}
        className="grid place-items-center h-8 w-8 rounded text-muted-foreground hover:bg-accent hover:text-foreground"
        title="Cancel (Esc)"
      >
        <X className="h-4 w-4" />
      </button>
    </form>
  );
}
