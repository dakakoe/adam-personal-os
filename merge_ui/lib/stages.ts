"use client";

import { useEffect, useState } from "react";
import { api, type StageConfig } from "@/lib/api";

/** Fallback while /api/stages loads (and pre-migration) — the classic six. */
export const DEFAULT_STAGES: StageConfig[] = [
  { key: "intro", label: "Intro", sort: 1, terminal: false, closes: false, color: "sky", in_use: 0 },
  { key: "conversations", label: "Conversations", sort: 2, terminal: false, closes: false, color: "violet", in_use: 0 },
  { key: "mou", label: "Proposal", sort: 3, terminal: false, closes: false, color: "amber", in_use: 0 },
  { key: "contract", label: "Contract", sort: 4, terminal: false, closes: false, color: "orange", in_use: 0 },
  { key: "active", label: "Active", sort: 5, terminal: false, closes: true, color: "emerald", in_use: 0 },
  { key: "lost", label: "Lost", sort: 99, terminal: true, closes: true, color: "slate", in_use: 0 },
];

let cache: StageConfig[] | null = null;
let inflight: Promise<StageConfig[]> | null = null;

/** Client-side stage config with a module-level cache (one fetch per page
 *  load across all badges/boards). Pass `initial` when a server component
 *  already fetched the list. */
export function useStages(initial?: StageConfig[]): StageConfig[] {
  if (initial && !cache) cache = initial;
  const [stages, setStages] = useState<StageConfig[]>(initial ?? cache ?? DEFAULT_STAGES);
  useEffect(() => {
    if (initial || cache) return;
    inflight ??= api.listStages().then((s) => (cache = s));
    inflight.then(setStages).catch(() => {});
  }, [initial]);
  return stages;
}

/** Drop the cache after a stage-config mutation so the next render refetches. */
export function invalidateStages() {
  cache = null;
  inflight = null;
}

export function stageLabel(stages: StageConfig[], key: string): string {
  return stages.find((s) => s.key === key)?.label ?? key;
}

export function stageColor(stages: StageConfig[], key: string): string {
  return stages.find((s) => s.key === key)?.color ?? "slate";
}

export function isTerminal(stages: StageConfig[], key: string): boolean {
  return stages.find((s) => s.key === key)?.terminal ?? false;
}

// Literal class strings per palette name so Tailwind's JIT sees them.
type StageClasses = { badge: string; accent: string; text: string };
export const STAGE_PALETTE: Record<string, StageClasses> = {
  sky:     { badge: "text-sky-400 border-sky-500/40 bg-sky-500/10",             accent: "border-sky-500/60",     text: "text-sky-400" },
  violet:  { badge: "text-violet-400 border-violet-500/40 bg-violet-500/10",    accent: "border-violet-500/60",  text: "text-violet-400" },
  amber:   { badge: "text-amber-400 border-amber-500/40 bg-amber-500/10",       accent: "border-amber-500/60",   text: "text-amber-400" },
  orange:  { badge: "text-orange-400 border-orange-500/40 bg-orange-500/10",    accent: "border-orange-500/60",  text: "text-orange-400" },
  emerald: { badge: "text-emerald-400 border-emerald-500/40 bg-emerald-500/10", accent: "border-emerald-500/60", text: "text-emerald-400" },
  teal:    { badge: "text-teal-400 border-teal-500/40 bg-teal-500/10",          accent: "border-teal-500/60",    text: "text-teal-400" },
  cyan:    { badge: "text-cyan-400 border-cyan-500/40 bg-cyan-500/10",          accent: "border-cyan-500/60",    text: "text-cyan-400" },
  indigo:  { badge: "text-indigo-400 border-indigo-500/40 bg-indigo-500/10",    accent: "border-indigo-500/60",  text: "text-indigo-400" },
  fuchsia: { badge: "text-fuchsia-400 border-fuchsia-500/40 bg-fuchsia-500/10", accent: "border-fuchsia-500/60", text: "text-fuchsia-400" },
  rose:    { badge: "text-rose-400 border-rose-500/40 bg-rose-500/10",          accent: "border-rose-500/60",    text: "text-rose-400" },
  lime:    { badge: "text-lime-400 border-lime-500/40 bg-lime-500/10",          accent: "border-lime-500/60",    text: "text-lime-400" },
  slate:   { badge: "text-muted-foreground border-border bg-secondary/40",      accent: "border-border",         text: "text-muted-foreground" },
};
export const STAGE_COLOR_NAMES = Object.keys(STAGE_PALETTE);

export function paletteFor(color: string): StageClasses {
  return STAGE_PALETTE[color] ?? STAGE_PALETTE.slate;
}
