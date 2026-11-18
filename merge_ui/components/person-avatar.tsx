"use client";

import {
  Avatar, AvatarFallback, AvatarImage,
} from "@/components/ui/avatar";
import { initials } from "@/lib/utils";
import { cn } from "@/lib/utils";

/**
 * Avatar that points at /api/persons/{id}/photo. The endpoint redirects
 * to a real photo (telegram local file → google contacts URL → gravatar)
 * or 404s. Radix UI's AvatarImage swaps to AvatarFallback automatically
 * on a 404, so we always have initials as a graceful baseline.
 *
 * `size` matches the underlying Avatar primitive's size data-attr; pass
 * className for additional sizing (e.g. h-16 w-16 on the detail page).
 */
export function PersonAvatar({
  personId,
  displayName,
  size = "default",
  className,
}: {
  personId: string;
  displayName: string;
  size?: "default" | "sm" | "lg";
  className?: string;
}) {
  return (
    <Avatar size={size} className={cn("ring-1 ring-border", className)}>
      <AvatarImage
        src={`/api/persons/${personId}/photo`}
        alt={displayName}
        loading="lazy"
      />
      <AvatarFallback className="bg-secondary">
        {initials(displayName)}
      </AvatarFallback>
    </Avatar>
  );
}
