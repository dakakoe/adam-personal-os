"use client";

import { Send, Linkedin, Mail } from "lucide-react";
import { Separator } from "@/components/ui/separator";
import type { BioRow } from "@/lib/api";

/**
 * Renders all per-source bio-ish blurbs (Telegram about, LinkedIn role,
 * LinkedIn job title from imported contacts, Google Contacts notes,
 * Google Contacts role) with a small source icon on each row so the
 * user sees provenance. The unified LLM summary (rendered separately
 * by PersonCard) consolidates these.
 */
export function BiosSection({ bios }: { bios: BioRow[] }) {
  if (!bios || bios.length === 0) return null;

  const KIND_LABEL: Record<string, string> = {
    bio: "Bio",
    role: "Role",
    title: "Title",
    notes: "Notes",
  };

  return (
    <>
      <Separator />
      <section>
        <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground mb-3">
          Bios across sources
        </h3>
        <ul className="space-y-2.5">
          {bios.map((b, idx) => (
            <li key={idx} className="flex items-start gap-3 text-sm">
              <span
                title={`${b.source} / ${b.kind}`}
                className="grid place-items-center h-6 w-6 rounded-md bg-secondary/60 shrink-0 mt-0.5"
              >
                <SourceIcon source={b.source} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground/80 mb-0.5">
                  {(KIND_LABEL[b.kind] ?? b.kind)} · {b.source}
                </div>
                <div className="whitespace-pre-wrap break-words text-foreground/90">
                  {b.text}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

function SourceIcon({ source }: { source: string }) {
  if (source === "telegram") return <Send className="h-3.5 w-3.5 text-sky-500" />;
  if (source === "linkedin") return <Linkedin className="h-3.5 w-3.5 text-blue-500" />;
  if (source === "google_contacts") return <Mail className="h-3.5 w-3.5 text-emerald-500" />;
  return <Mail className="h-3.5 w-3.5 text-muted-foreground" />;
}
