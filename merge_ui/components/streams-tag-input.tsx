"use client";

import { useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

/** Stream tags on a deal: chips for what's applied, plus an input that
 *  autocompletes from the vocabulary already in use (`known`) and still
 *  accepts a brand-new tag. Tags are lower-cased, because the streams registry
 *  and the board's stream filter compare them that way.
 *
 *  Same interaction as the detail panel's stream editor, but controlled, so a
 *  form can hold the tags until it submits. Enter never submits that form. */
export function StreamsTagInput({
  value, onChange, known, placeholder = "+ tag (job, consulting…)",
}: {
  value: string[];
  onChange: (tags: string[]) => void;
  known: string[];
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);

  const q = text.trim().toLowerCase();
  // Prefix matches first, then substring; nothing already applied.
  const matches = known
    .filter((t) => !value.includes(t) && (!q || t.includes(q)))
    .sort((a, b) => Number(b.startsWith(q)) - Number(a.startsWith(q)))
    .slice(0, 8);

  const add = (t: string) => {
    const v = t.trim().toLowerCase();
    if (v && !value.includes(v)) onChange([...value, v]);
    setText(""); setOpen(false); setHi(0);
  };

  return (
    <div className="flex items-center gap-1.5 flex-wrap min-h-9">
      {value.map((t) => (
        <span key={t} className="inline-flex items-center gap-1 h-6 pl-2 pr-1 rounded-full border border-border text-xs">
          {t}
          <button type="button" title={`Remove "${t}"`}
            onClick={() => onChange(value.filter((x) => x !== t))}
            className="inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground">
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
      <span className="relative inline-block">
        <input
          value={text}
          onChange={(e) => { setText(e.target.value); setOpen(true); setHi(0); }}
          onFocus={() => setOpen(true)}
          // Blur is delayed so a click on a suggestion still registers.
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" && matches.length) {
              e.preventDefault(); setOpen(true); setHi((i) => (i + 1) % matches.length); return;
            }
            if (e.key === "ArrowUp" && matches.length) {
              e.preventDefault(); setHi((i) => (i - 1 + matches.length) % matches.length); return;
            }
            if (e.key === "Escape") { setOpen(false); return; }
            if (e.key === "Backspace" && !text && value.length) {
              onChange(value.slice(0, -1)); return;
            }
            if (e.key !== "Enter") return;
            // Always swallow Enter here: it adds a tag, it must not submit.
            e.preventDefault();
            add(open && matches[hi] && q ? matches[hi] : text);
          }}
          placeholder={placeholder}
          className="h-6 px-2 text-xs rounded-full border border-dashed border-border bg-transparent outline-none focus:border-foreground/40 w-40"
        />
        {open && matches.length > 0 && (
          <div className="absolute z-30 left-0 top-7 w-44 rounded-md border border-border bg-popover shadow-lg p-1">
            {matches.map((t, i) => (
              <button
                key={t} type="button"
                onMouseDown={(e) => { e.preventDefault(); add(t); }}
                onMouseEnter={() => setHi(i)}
                className={cn("w-full text-left px-2 py-1 rounded text-xs",
                  i === hi ? "bg-accent text-foreground" : "hover:bg-accent/60")}
              >
                {t}
              </button>
            ))}
          </div>
        )}
      </span>
    </div>
  );
}
