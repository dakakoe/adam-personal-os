"use client";

import { useEffect, useRef, useState } from "react";
import { COUNTRIES, countryFlag } from "@/lib/utils";

/** Autocomplete for the closed set of countries. Typing filters the canonical
 *  list (each option flag-renderable); picking one sets the value and commits.
 *  Free text is still allowed (forgiving) but the list steers to canonical
 *  spellings so countryFlag() resolves. onCommit fires on pick or blur — the
 *  company card uses it to PATCH; the create dialog omits it (submit handles it). */
export function CountryCombobox({
  value, onChange, onCommit, placeholder = "Country", className, wrapperClassName = "relative inline-block", id, autoFocus,
}: {
  value: string;
  onChange: (v: string) => void;
  onCommit?: (v: string) => void;
  placeholder?: string;
  className?: string;
  wrapperClassName?: string;
  id?: string;
  autoFocus?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);

  const q = value.trim().toLowerCase();
  const matches = (q ? COUNTRIES.filter((c) => c.toLowerCase().includes(q)) : COUNTRIES).slice(0, 8);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
        onCommit?.(value);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open, value, onCommit]);

  function choose(c: string) {
    onChange(c);
    setOpen(false);
    onCommit?.(c);
  }

  return (
    <span className={wrapperClassName} ref={boxRef}>
      <input
        id={id}
        autoFocus={autoFocus}
        value={value}
        placeholder={placeholder}
        className={className}
        onChange={(e) => { onChange(e.target.value); setOpen(true); setHi(0); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setHi((h) => Math.min(h + 1, matches.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
          else if (e.key === "Enter" && open && matches[hi]) { e.preventDefault(); choose(matches[hi]); }
          else if (e.key === "Escape") { setOpen(false); }
        }}
        onBlur={() => { /* commit handled by outside-click / pick to avoid races with option mousedown */ }}
      />
      {open && matches.length > 0 && (
        <div className="absolute z-50 mt-1 left-0 w-52 rounded-md border border-border bg-popover shadow-lg p-1 max-h-60 overflow-auto">
          {matches.map((c, i) => (
            <button
              key={c}
              type="button"
              onMouseDown={(e) => { e.preventDefault(); choose(c); }}
              onMouseEnter={() => setHi(i)}
              className={`w-full text-left px-2 py-1.5 rounded text-sm flex items-center gap-2 ${i === hi ? "bg-accent" : "hover:bg-accent/60"}`}
            >
              <span className="w-5 text-center">{countryFlag(c) || "🏳️"}</span>
              <span className="truncate">{c}</span>
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
