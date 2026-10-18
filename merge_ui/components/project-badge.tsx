import Link from "next/link";
import { cn } from "@/lib/utils";

/**
 * Small color-tinted chip linking to a project. Used by task + opp
 * rows + person-card sections to show project provenance. Color
 * comes from memory.project.color (hex string).
 */
export function ProjectBadge({
  slug, name, color, className,
}: {
  slug: string | null;
  name: string | null;
  color: string | null;
  className?: string;
}) {
  if (!slug || !name) return null;
  // Inline style for the swatch — `color` is user-controlled hex so we
  // keep it as a CSS var rather than dynamic Tailwind (which won't ship).
  return (
    <Link
      href={`/projects/${slug}`}
      className={cn(
        "inline-flex items-center gap-1.5 text-[10px] font-medium px-1.5 py-0.5 rounded border border-border bg-secondary/40 hover:bg-accent transition-colors max-w-[180px]",
        className,
      )}
    >
      <span
        aria-hidden
        className="h-2 w-2 rounded-full shrink-0"
        style={{ backgroundColor: color ?? "var(--color-muted-foreground)" }}
      />
      <span className="truncate">{name}</span>
    </Link>
  );
}
