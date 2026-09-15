"use client";

import { cn } from "@/lib/utils";
import { useStages, paletteFor, stageLabel, stageColor, isTerminal } from "@/lib/stages";

/** Stage chip styled from the config table (label + palette color). */
export function StageBadge({ stage, className }: { stage: string; className?: string }) {
  const stages = useStages();
  const pal = paletteFor(stageColor(stages, stage));
  return (
    <span className={cn(
      "inline-flex items-center text-[10px] font-medium px-1.5 py-0.5 rounded border",
      pal.badge,
      isTerminal(stages, stage) && "line-through",
      className,
    )}>
      {stageLabel(stages, stage)}
    </span>
  );
}
